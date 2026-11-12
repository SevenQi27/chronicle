"""Codex hooks plus narrowly scoped recovery of the current turn's first prompt."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shlex
import sys

from ..capture import CaptureEvent, CaptureQueue, digest
from ..models import Source, utcnow


EVENTS={'UserPromptSubmit':('user','prompt'),'Stop':('assistant','last_assistant_message')}


def transcript_metadata(path,session):
    if not path:return None
    file=Path(path)
    if not file.is_file():return None
    with file.open(encoding='utf-8') as stream:
        try:record=json.loads(stream.readline())
        except (ValueError,UnicodeError):return None
    metadata=record.get('payload',{})
    if (record.get('type')!='session_meta' or metadata.get('thread_source')!='user'
            or metadata.get('id')!=session):return None
    return metadata


def enqueue_message(queue,session,turn,role,content,workspace,occurred_at=None):
    content=content.replace('\r\n','\n').strip()
    if not content:return {'skipped':'empty_message'}
    identifier=digest([session,turn,role,content])
    message=CaptureEvent(origin='codex',turn_id=turn,workspace=workspace,
        source=Source(id=identifier,conversation_id=session,role=role,content=content,occurred_at=occurred_at or utcnow()))
    return queue.submit(message,use_receipt_time=True)


def recover_turn_prompt(path,session,turn,queue,workspace):
    config_path=queue.path.with_suffix('.codex.json')
    if not config_path.exists():return 0
    cutoff=datetime.fromisoformat(json.loads(config_path.read_text())['enabled_at'])
    recovered=0
    # Some desktop first turns fire Stop but omit UserPromptSubmit. Only read
    # typed, completed user items from THIS turn, after activation. Never ingest
    # response_item messages: those also contain injected instructions/history.
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            try:record=json.loads(line)
            except ValueError:continue
            if record.get('type')!='event_msg':continue
            payload=record.get('payload',{})
            if payload.get('type')!='item_completed' or payload.get('turn_id')!=turn:continue
            item=payload.get('item',{})
            if item.get('type')!='UserMessage':continue
            if payload.get('thread_id')!=session:continue
            occurred_at=datetime.fromisoformat(record['timestamp'].replace('Z','+00:00'))
            if occurred_at<cutoff:continue
            content='\n'.join(part['text'] for part in item.get('content',[])
                if isinstance(part,dict) and str(part.get('type','')).lower()=='text' and isinstance(part.get('text'),str))
            if content.strip():
                enqueue_message(queue,session,turn,'user',content,workspace,occurred_at)
                recovered+=1
    return recovered


def capture_hook(payload,queue):
    if not isinstance(payload,dict):raise ValueError('Codex hook input must be a JSON object')
    event=payload.get('hook_event_name')
    if event not in EVENTS:return {'skipped':'unsupported_event'}
    session=payload.get('session_id');turn=payload.get('turn_id')
    if not isinstance(session,str) or not session.strip():raise ValueError('Codex hook is missing session_id')
    if not isinstance(turn,str) or not turn.strip():raise ValueError('Codex hook is missing turn_id')
    path=payload.get('transcript_path')
    metadata=transcript_metadata(path,session)
    if metadata is None:return {'skipped':'not_a_verified_user_session'}
    role,field=EVENTS[event];content=payload.get(field)
    if not isinstance(content,str) or not content.strip():return {'skipped':'empty_message'}
    # Retries and Stop re-entry keep the same ID. A continued turn with a changed
    # final answer is a new event, still grouped under the original turn.
    workspace=payload.get('cwd') or metadata.get('cwd') or ''
    recovered=0
    if event=='Stop' and not queue.has_user('codex',session,turn):
        recovered=recover_turn_prompt(path,session,turn,queue,workspace)
        if not queue.has_user('codex',session,turn):return {'skipped':'no_captured_user_prompt'}
    # Native events do not supply a stable timestamp. Reuse the first receipt's
    # timestamp when the same event is delivered again, instead of conflicting.
    result=enqueue_message(queue,session,turn,role,content,workspace)
    return {**result,'recovered_prompts':recovered}


def install_hooks(codex_home,queue_path):
    codex_home=Path(codex_home).expanduser().resolve()
    path=codex_home/'hooks.json'
    config=json.loads(path.read_text()) if path.exists() else {'hooks':{}}
    if not isinstance(config,dict) or not isinstance(config.get('hooks',{}),dict):raise ValueError('Invalid existing hooks.json')
    hooks=config.setdefault('hooks',{})
    command=shlex.join([sys.executable,'-m','chronicle_memory.adapters.codex','hook','--queue',str(Path(queue_path).expanduser().resolve())])
    for event in EVENTS:
        groups=hooks.setdefault(event,[])
        if any(h.get('command')==command for group in groups for h in group.get('hooks',[])):continue
        handler={'type':'command','command':command,'timeout':5,'statusMessage':'Queueing Chronicle conversation capture'}
        if event=='Stop':handler['async']=True
        groups.append({'hooks':[handler]})
    codex_home.mkdir(parents=True,exist_ok=True)
    queue=Path(queue_path).expanduser().resolve()
    queue.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    activation=queue.with_suffix('.codex.json')
    if not activation.exists():
        activation.write_text(json.dumps({'enabled_at':utcnow().isoformat()})+'\n')
        activation.chmod(0o600)
    temporary=path.with_suffix('.json.chronicle-tmp')
    temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    temporary.chmod(0o600);temporary.replace(path)
    return {'path':str(path),'command':command,'events':list(EVENTS),'requires_hook_trust':True}


def main():
    parser=argparse.ArgumentParser(description='Chronicle Codex adapter')
    parser.add_argument('action',choices=['hook','install'])
    parser.add_argument('--queue',type=Path,required=True)
    parser.add_argument('--codex-home',type=Path,default=Path.home()/'.codex')
    args=parser.parse_args()
    try:
        if args.action=='install':
            print(json.dumps(install_hooks(args.codex_home,args.queue),ensure_ascii=False));return
        payload=json.load(sys.stdin)
        result=capture_hook(payload,CaptureQueue(args.queue))
        audit=args.queue.expanduser().resolve().parent/'codex-hooks.jsonl'
        with audit.open('a') as stream:
            stream.write(json.dumps({'at':utcnow().isoformat(),'event':payload.get('hook_event_name'),
                'session_id':payload.get('session_id'),'turn_id':payload.get('turn_id'),**result})+'\n')
        audit.chmod(0o600)
        print(json.dumps({'continue':True}))
    except Exception as error:
        if args.action=='hook':
            print(json.dumps({'continue':True,'systemMessage':'Chronicle capture was not queued: '+type(error).__name__+'. Check the capture worker/status.'}))
            return
        raise


if __name__=='__main__':main()
