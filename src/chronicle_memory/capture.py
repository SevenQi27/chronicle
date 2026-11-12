"""Host-neutral durable capture of conversation events, without model inference."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import signal
import sqlite3
import time

from .models import Entity, Experience, ExperienceTime, Extraction, Model, Source, Text, stamp, utcnow


class CaptureEvent(Model):
    origin: Text
    turn_id: Text
    source: Source
    workspace: str = ''
    private: bool = False


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def redact(text):
    text=re.sub(r'<private\b[^>]*>[\s\S]*?(?:</private\s*>|$)','[private]',text,flags=re.I)
    text=re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b','[credential]',text)
    text=re.sub(r'(?im)((?:password|passwd|api[_-]?key|access[_-]?token|secret)\s*[=:]\s*)(["\']?)[^\s,;"\']+\2',r'\1[credential]',text)
    return text


class CaptureQueue:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    turn_key TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    body TEXT,
                    received_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    record_id TEXT,
                    saved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS capture_ready ON events(status,retry_at,sequence);
                CREATE INDEX IF NOT EXISTS capture_turn ON events(turn_key,sequence);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=2)
        db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def submit(self,event:CaptureEvent,*,use_receipt_time=False):
        turn_key='capture-turn:'+digest([event.origin,event.source.conversation_id,event.turn_id])
        event_key=digest([event.origin,event.source.id])
        content=redact(event.source.content)
        private=event.private or not content.replace('[private]','').strip()
        if event.source.role=='user' and re.search(r'不要(?:保存|记录|记住)|do not (?:save|record|remember)|don.t (?:save|record|remember)',event.source.content,re.I):
            private=True
        clean=event.model_copy(update={'source':event.source.model_copy(update={'content':content})})
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM events WHERE turn_key=? AND status='private' LIMIT 1",(turn_key,)).fetchone():
                private=True
            old=db.execute('SELECT body,status FROM events WHERE event_key=?',(event_key,)).fetchone()
            if old and old['body'] and use_receipt_time:
                first=CaptureEvent.model_validate_json(old['body'])
                clean=clean.model_copy(update={'source':clean.source.model_copy(update={'occurred_at':first.source.occurred_at})})
            body=None if private else clean.model_dump_json()
            if old:
                if old['body']!=body:
                    raise ValueError('capture event ID already exists with different content')
                return {'accepted':True,'duplicate':True,'status':old['status'],'event_key':event_key}
            status='private' if private else 'queued'
            db.execute('INSERT INTO events(event_key,turn_key,origin,conversation_id,body,received_at,status) VALUES(?,?,?,?,?,?,?)',
                (event_key,turn_key,event.origin,event.source.conversation_id,body,stamp(utcnow()),status))
        return {'accepted':True,'duplicate':False,'status':status,'event_key':event_key}

    def ready(self,limit=20,now=None):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM events WHERE status='queued' AND retry_at<=? ORDER BY sequence LIMIT ?",(time.time() if now is None else now,limit))]

    def turn(self,key):
        with self.connect() as db:
            return [CaptureEvent.model_validate_json(r['body']) for r in db.execute("SELECT body FROM events WHERE turn_key=? AND body IS NOT NULL ORDER BY sequence",(key,))]

    def has_user(self,origin,conversation,turn_id):
        key='capture-turn:'+digest([origin,conversation,turn_id])
        return any(event.source.role=='user' for event in self.turn(key))

    def done(self,key,record_id):
        with self.connect() as db:
            db.execute("UPDATE events SET status='saved',record_id=?,saved_at=?,last_error=NULL WHERE event_key=?",
                       (record_id,stamp(utcnow()),key))

    def failed(self,key,error):
        with self.connect() as db:
            attempts=db.execute('SELECT attempts FROM events WHERE event_key=?',(key,)).fetchone()['attempts']+1
            db.execute('UPDATE events SET attempts=?,last_error=?,retry_at=? WHERE event_key=?',
                       (attempts,error,time.time()+min(60,2**min(attempts,6)),key))

    def status(self):
        with self.connect() as db:
            counts={r['status']:r['n'] for r in db.execute('SELECT status,count(*) AS n FROM events GROUP BY status')}
            pending=db.execute("SELECT count(*) AS n,min(received_at) AS oldest FROM events WHERE status='queued'").fetchone()
            recent=[dict(r) for r in db.execute('SELECT origin,conversation_id,received_at,status,attempts,last_error,record_id,saved_at FROM events ORDER BY sequence DESC LIMIT 10')]
        return {'queue':str(self.path),'counts':counts,'pending':pending['n'],'oldest_pending_at':pending['oldest'],'recent':recent}

    @contextmanager
    def worker_lock(self):
        # One worker per queue keeps turn snapshots ordered; enqueueing remains
        # independent and works while Neo4j is unavailable or a worker restarts.
        import fcntl
        with self.path.with_suffix('.worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('A capture worker is already running for this queue') from error
            try:yield
            finally:fcntl.flock(lock,fcntl.LOCK_UN)


def archive_turn(store,turn_key,events):
    events=sorted(events,key=lambda e:(e.source.occurred_at,e.source.id))
    envelope={'capture_kind':'conversation_record','origin':events[0].origin,
              'conversation_id':events[0].source.conversation_id,'turn_id':events[0].turn_id,
              'messages':[e.model_dump(mode='json') for e in events]}
    legacy_content=json.dumps(envelope,ensure_ascii=False,sort_keys=True)
    source_id='capture-snapshot:'+digest(envelope)
    receipt=store.source(source_id)
    if receipt and receipt['status']=='applied':
        return json.loads(receipt['result'])['record_ids'][0]
    evidence=[]
    for event in events:
        original=event.source.model_copy(update={'id':'capture-source:'+digest([event.origin,event.source.id]),
                    'conversation_id':event.origin+':'+event.source.conversation_id})
        # Original assistant text is evidence of a conversation, not a new user
        # fact. No inferred understanding or exact-state write is produced here.
        store.apply(original,Extraction())
        evidence.append(original.id)
    conversation=events[0].source.conversation_id
    workspace=next((e.workspace for e in events if e.workspace),'')
    entities=[Entity(id='capture-conversation:'+digest([events[0].origin,conversation]),
                     name=f'{events[0].origin} 对话 {conversation[:8]}',category='conversation')]
    if workspace:
        entities.append(Entity(id='capture-workspace:'+digest(workspace),name=workspace,category='workspace'))
    prompt=next((e.source.content for e in events if e.source.role=='user'),events[0].source.content)
    title='对话 · '+' '.join(prompt.split())[:90]
    roles={'user':'用户','assistant':'助手（未经用户确认）','tool':'工具结果'}
    narrative='自动采集的对话记录；不将助手回答视为已核实的用户事实。\n'
    if workspace:narrative+='工作目录：'+workspace+'\n'
    narrative+='\n\n'.join(roles[e.source.role]+'：\n'+e.source.content for e in events)
    start=min(e.source.occurred_at for e in events);end=max(e.source.occurred_at for e in events)
    latest=store.latest_record_ids([turn_key],store.capture_view())
    episode=Experience(id=turn_key,title=title,narrative=narrative,entities=entities,source_ids=evidence,
        replaces=latest[0]['id'] if latest else None,
        when=ExperienceTime(start=start,end=end,precision='range' if start!=end else 'exact'))
    # Each original message already has an immutable source. Snapshot sources
    # reference those messages rather than copying the growing transcript again.
    manifest={**envelope,'format':'source-references-v1','content_hash':digest(envelope),
        'messages':[{'source_id':identifier,'role':event.source.role,
                     'occurred_at':stamp(event.source.occurred_at),
                     'content_sha256':hashlib.sha256(event.source.content.encode()).hexdigest()}
                    for event,identifier in zip(events,evidence)]}
    content=json.dumps(manifest,ensure_ascii=False,sort_keys=True)
    # A previous version may have received the old snapshot before failing to
    # commit it. Retry that receipt unchanged; never rewrite existing evidence.
    if receipt and receipt['content']==legacy_content:
        content=legacy_content
    source=Source(id=source_id,content=content,role='tool',occurred_at=end,
                  conversation_id=events[0].origin+':'+conversation)
    return store.apply(source,Extraction(experiences=[episode]))['record_ids'][0]


def process_pending(queue,settings,*,now=None,store_factory=None):
    from .errors import error_result
    from .store import MemoryStore
    rows=queue.ready(now=now)
    if not rows:return {'saved':0,'failed':0}
    store=None;saved=failed=0
    try:
        for row in rows:
            try:
                if store is None:store=(store_factory or MemoryStore)(settings)
                identifier=archive_turn(store,row['turn_key'],queue.turn(row['turn_key']))
                queue.done(row['event_key'],identifier);saved+=1
            except Exception as error:
                # Queue status contains a safe diagnostic, never source contents
                # or provider/driver strings that might contain credentials.
                code=error_result(error)['error']['code']
                queue.failed(row['event_key'],code+':'+type(error).__name__);failed+=1
                if store is not None:
                    store.close();store=None
    finally:
        if store is not None:store.close()
    return {'saved':saved,'failed':failed}


def run_worker(queue,settings,interval=2,once=False):
    if interval<=0:raise ValueError('capture interval must be positive')
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    previous={sig:signal.signal(sig,stop) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        with queue.worker_lock():
            while not stopping:
                result=process_pending(queue,settings)
                if once:return result
                if result['saved'] or result['failed']:print(json.dumps(result),flush=True)
                deadline=time.monotonic()+interval
                while not stopping and time.monotonic()<deadline:time.sleep(min(.2,max(0,deadline-time.monotonic())))
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
    return {'stopped':True}
