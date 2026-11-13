"""Neo4j is the sole writable store: evidence, episodes, understanding and time."""
from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from neo4j import GraphDatabase

from .config import Settings
from .models import Extraction, Source, stamp, utcnow, state_key, WriteContext
from .state_projection import StateProjection
from .errors import ConflictError, NotInitializedError


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _utc_iso(value):
    return stamp(datetime.fromisoformat(value)) if value is not None else None


def filter_params(filters=None):
    values=filters.model_dump() if hasattr(filters,'model_dump') else (filters or {})
    return {'filter_'+key:values.get(key) for key in ('origin','workspace','conversation_id','source_id')}


def filter_clause(alias):
    return (' AND '.join(f'($filter_{key} IS NULL OR {alias}.{key}=$filter_{key})'
                       for key in ('origin','workspace','conversation_id'))+
            f' AND ($filter_source_id IS NULL OR EXISTS {{ MATCH ({alias})-[:EVIDENCED_BY]->(fs:EvidenceSource) WHERE fs.id=$filter_source_id }})')


def tokens(text):
    result = re.findall(r"[a-z0-9_]+", text.lower())
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(part[i:i+2] for i in range(len(part)-1))
        if len(part)==1:
            result.append(part)
    return list(dict.fromkeys(result))


def _native(row):
    return {k:stamp(v.to_native()) if hasattr(v, 'to_native') else v for k,v in row.items()}


RELATIONS = {'related_to':'RELATED_TO','continues':'CONTINUES','contradicts':'CONTRADICTS',
             'caused_by':'CAUSED_BY','revises':'REVISES'}

# Each branch can use its relationship-property index; an untyped relationship
# MATCH scans every relationship, even when namespace and logical_id are given.
ASSOCIATION_LOOKUP = 'CALL { '+ ' UNION ALL '.join(
    f'MATCH ()-[r:{kind}]->() WHERE r.namespace=$ns AND r.logical_id=$id '
    'AND r.category="association" RETURN r' for kind in RELATIONS.values()
)+' } RETURN r.id AS id ORDER BY r.seq DESC LIMIT 1'


# Resolve one logical memory before limiting graph traversal results. For an
# experience, its latest account may move outside the requested time. For an
# understanding, corrections replace their root before applicability is tested.
_RECORD_SELECTION = '''
CALL {
    WITH m
    MATCH (m)-[:HAS_REVISION]->(candidate:MemoryRecord)
    WHERE candidate.record_kind IN ['experience','understanding']
      AND candidate.seq <= $revision AND candidate.recorded_at <= datetime($known)
      AND ($include_withdrawn OR NOT coalesce(candidate.withdrawn,false))
      AND (candidate.record_kind='experience' OR (
        (candidate.effective_at IS NULL OR candidate.effective_at <= datetime($at))
        AND NOT EXISTS {
            MATCH (m)-[:HAS_REVISION]->(newer:MemoryRecord)
            WHERE newer.root_id=candidate.root_id AND newer.seq>candidate.seq
              AND newer.seq <= $revision AND newer.recorded_at <= datetime($known)
        }
      ))
    WITH candidate ORDER BY candidate.seq DESC LIMIT 1
    WITH candidate
    WHERE (candidate.record_kind<>'experience' OR candidate.effective_at IS NULL OR candidate.effective_at <= datetime($at))
'''
RECORD_HEAD = _RECORD_SELECTION+'    RETURN candidate AS current\n}\n'
CURRENT_RECORD = _RECORD_SELECTION+'''
      AND (candidate.record_kind<>'understanding' OR candidate.valid_to IS NULL OR datetime({datetime:candidate.valid_to,timezone:'+00:00'}) > datetime($at))
    RETURN candidate AS current
}
'''


@dataclass(frozen=True)
class ReadView:
    at: datetime
    known_at: datetime
    revision: int

    def at_time(self, at):
        return replace(self, at=at)


class MemoryStore(StateProjection):
    def __init__(self, settings: Settings, namespace=None, clock=utcnow, *, require_initialized=True):
        self.settings = settings
        self.namespace = namespace or settings.namespace
        if not self.namespace.strip():
            raise ValueError('namespace 不能为空')
        self.clock = clock
        self.driver = GraphDatabase.driver(settings.neo4j_uri,
            auth=(settings.neo4j_user,settings.neo4j_password), connection_timeout=10)
        try:
            self.driver.verify_connectivity()
            if require_initialized:
                row=self._run('MATCH (s:ChronicleSchema {name:"chronicle"}) RETURN s.version AS version')
                if not row or row[0]['version']!=1:
                    raise NotInitializedError('数据库尚未初始化；请先运行 chronicle init')
        except Exception:
            self.driver.close()
            raise

    def initialize(self):
        for label in ('MemoryRecord','EvidenceSource','Memory','Entity','DueCheck','Review','Embedding','EmbeddingIndex','MemoryVisibility','ForgetMarker'):
            self._run(f'CREATE CONSTRAINT chronicle_{label.lower()}_uid IF NOT EXISTS FOR (n:{label}) REQUIRE n.uid IS UNIQUE')
        self._run('CREATE CONSTRAINT chronicle_namespace IF NOT EXISTS FOR (n:MemoryNamespace) REQUIRE n.name IS UNIQUE')
        self._run('CREATE CONSTRAINT chronicle_schema IF NOT EXISTS FOR (n:ChronicleSchema) REQUIRE n.name IS UNIQUE')
        self._run('CREATE INDEX chronicle_time IF NOT EXISTS FOR (n:MemoryRecord) ON (n.namespace,n.recorded_at)')
        self._run('CREATE INDEX chronicle_pending IF NOT EXISTS FOR (n:DueCheck) ON (n.namespace,n.due_at)')
        self._run('CREATE FULLTEXT INDEX chronicle_text IF NOT EXISTS FOR (n:MemoryRecord) ON EACH [n.search_terms]')
        self._run('CREATE INDEX chronicle_entity_name IF NOT EXISTS FOR (n:Entity) ON (n.namespace,n.name)')
        self._run('CREATE INDEX chronicle_experience_time IF NOT EXISTS FOR (n:Experience) ON (n.namespace,n.occurred_start)')
        self._run('CREATE INDEX chronicle_state_identity IF NOT EXISTS FOR (n:MemoryRecord) ON (n.namespace,n.subject,n.key)')
        self._run('CREATE INDEX chronicle_memory_kind IF NOT EXISTS FOR (n:Memory) ON (n.namespace,n.kind)')
        for kind in RELATIONS.values():
            self._run(f'CREATE INDEX chronicle_association_{kind.lower()} IF NOT EXISTS FOR ()-[r:{kind}]-() ON (r.namespace,r.logical_id)')
        for field in ('origin','workspace','conversation_id'):
            self._run(f'CREATE INDEX chronicle_scope_{field} IF NOT EXISTS FOR (r:MemoryRecord) ON (r.namespace,r.{field})')
        self.backfill_context()
        self._run('CALL db.awaitIndexes(30)')
        self._run('MERGE (s:ChronicleSchema {name:"chronicle"}) SET s.version=1')
        return {'initialized':True,'schema_version':1}

    def backfill_context(self):
        self._run('MATCH (r:MemoryRecord)-[:EVIDENCED_BY]->(s:EvidenceSource) '
            'WHERE r.namespace=$ns AND r.source_id=s.id AND r.conversation_id IS NULL SET r.conversation_id=s.conversation_id',ns=self.namespace)
        after=''
        while True:
            rows=self._run('MATCH (r:MemoryRecord)-[:EVIDENCED_BY]->(s:EvidenceSource) '
                'WHERE r.namespace=$ns AND r.source_id=s.id AND r.origin IS NULL AND s.id STARTS WITH "capture-snapshot:" AND r.uid>$after '
                'OPTIONAL MATCH (r)-[:INVOLVES]->(e:Entity {category:"workspace"}) '
                'RETURN r.uid AS uid,s.content AS content,head(collect(e.name)) AS workspace ORDER BY uid LIMIT 100',after=after,ns=self.namespace)
            if not rows:break
            updates=[]
            for row in rows:
                try:content=json.loads(row['content'])
                except (TypeError,ValueError):continue
                if isinstance(content,dict) and content.get('capture_kind')=='conversation_record' and isinstance(content.get('origin'),str):
                    updates.append({'uid':row['uid'],'origin':content['origin'],'workspace':row['workspace']})
            if updates:self._run('UNWIND $rows AS x MATCH (r:MemoryRecord {uid:x.uid}) SET r.origin=x.origin,r.workspace=x.workspace',rows=updates)
            after=rows[-1]['uid']

    def matching_records(self,ids,filters,view,include_withdrawn=False):
        if not ids:return set()
        rows=self._run('UNWIND $uids AS uid MATCH (r:MemoryRecord {uid:uid}) '
            'WHERE r.seq<=$revision AND r.recorded_at<=datetime($known) '
            'AND ($include_withdrawn OR NOT coalesce(r.withdrawn,false)) AND '+filter_clause('r')+' RETURN r.event_id AS id',
            uids=[self._uid(i) for i in ids],revision=view.revision,known=stamp(view.known_at),
            include_withdrawn=include_withdrawn,**filter_params(filters))
        return {row['id'] for row in rows}

    def scoped_states(self,states,filters,view):
        if not filters:return states
        ids=[e['event_id'] for state in states for c in state['candidates'] for e in c['evidence']]
        allowed=self.matching_records(ids,filters,view)
        result=[]
        for state in states:
            candidates=[]
            for candidate in state['candidates']:
                evidence=[e for e in candidate['evidence'] if e['event_id'] in allowed]
                if evidence:candidates.append({'memory':evidence[-1],'evidence':evidence})
            if not candidates:continue
            scoped={**state,'candidates':candidates,'scope_partial':sum(len(c['evidence']) for c in candidates)<sum(len(c['evidence']) for c in state['candidates'])}
            if 'memory' in scoped:scoped['memory']=candidates[0]['memory']
            result.append(scoped)
        return result

    def capture_view(self, at=None, known_at=None):
        now=self.clock()
        rows=self._run('MATCH (n:MemoryNamespace {name:$ns}) RETURN n.sequence AS revision',ns=self.namespace)
        return ReadView(at or now,known_at or now,rows[0]['revision'] if rows else 0)

    def close(self):
        self.driver.close()

    def _run(self, cypher, **params):
        params.setdefault('include_withdrawn',False)
        with self.driver.session(database=self.settings.neo4j_database) as session:
            return [dict(row) for row in session.run(cypher,params)]

    def _write(self, callback):
        with self.driver.session(database=self.settings.neo4j_database) as session:
            def transaction(tx):
                # All writers in this namespace share an actual Neo4j write lock.
                tx.run('MERGE (n:MemoryNamespace {name:$ns}) ON CREATE SET n.sequence=0 '
                       'SET n.lock=coalesce(n.lock,0)+1', ns=self.namespace).consume()
                return callback(tx)
            return session.execute_write(transaction)

    def _seq(self, tx):
        return tx.run('MATCH (n:MemoryNamespace {name:$ns}) SET n.sequence=n.sequence+1 RETURN n.sequence AS seq',
                      ns=self.namespace).single()['seq']

    def _id(self, key):
        return str(uuid5(NAMESPACE_URL,self.namespace+':'+key))

    def _uid(self, identifier):
        return self.namespace+'::'+identifier

    def source(self, source_id):
        rows=self._run('MATCH (s:EvidenceSource {uid:$uid}) RETURN properties(s) AS s',uid=self._uid(source_id))
        return _native(rows[0]['s']) if rows else None

    def receive(self, source: Source):
        from .maintenance import ensure_writable
        body=_json(source.model_dump(mode='json'))
        def write(tx):
            ensure_writable(self,source,Extraction(),lambda q,**p:list(tx.run(q,**p)))
            now=stamp(self.clock())
            found=tx.run('MATCH (s:EvidenceSource {uid:$uid}) RETURN properties(s) AS s',uid=self._uid(source.id)).single()
            if found:
                if found['s']['body']!=body:
                    raise ConflictError('source_conflict','相同 source.id 的内容或时间不能改变；重试必须提交相同消息',source_id=source.id)
                return _native(found['s'])
            row=tx.run('CREATE (s:EvidenceSource {uid:$uid,id:$id,namespace:$ns,body:$body,content:$content,'
                       'role:$role,conversation_id:$conversation,occurred_at:datetime($occurred),recorded_at:datetime($now),status:"received"}) '
                       'RETURN properties(s) AS s',uid=self._uid(source.id),id=source.id,ns=self.namespace,body=body,
                       content=source.content,role=source.role,conversation=source.conversation_id,
                       occurred=stamp(source.occurred_at),now=now).single()
            return _native(row['s'])
        return self._write(write)

    def fail(self, source_id, reason):
        def write(tx):
            row=tx.run('MATCH (s:EvidenceSource {uid:$uid}) WHERE s.status<>"applied" '
                       'SET s.status="failed",s.error=$error RETURN s.id AS id',
                       uid=self._uid(source_id),error=reason).single()
            return row is not None
        return self._write(write)

    def _put_record(self,tx,record,source_ids,label,logical_id,context=None):
        # Search text and temporal properties are native graph properties; body
        # preserves the full, typed payload without flattening its narrative.
        payload=json.loads(record['body'])
        when=payload.get('when',{})
        row=tx.run(f'MATCH (primary:EvidenceSource {{uid:$primary}}) OPTIONAL MATCH (v:MemoryVisibility {{uid:$visibility}}) CREATE (r:MemoryRecord:{label}) SET r=$props '
                   'SET r.conversation_id=primary.conversation_id,r.withdrawn=coalesce(v.withdrawn,false) '
                   'SET r.recorded_at=datetime($known),r.effective_at=datetime($effective),'
                   'r.occurred_start=datetime($start),r.occurred_end=datetime($end),'
                   'r.valid_from=datetime($valid_from),r.valid_to=datetime($valid_to),'
                   'r.time_precision=$precision,r.time_description=$description '
                   'MERGE (m:Memory {uid:$mid}) ON CREATE SET m.namespace=$ns,m.id=$logical,m.kind=$kind '
                   'CREATE (m)-[:HAS_REVISION]->(r) RETURN properties(r) AS r',
                   props={**record,**(context.model_dump(exclude_none=True) if context else {}),'uid':self._uid(record['event_id']),'namespace':self.namespace,'logical_id':logical_id},
                   primary=self._uid(source_ids[0]),visibility=self._uid('visibility:'+hashlib.sha256(logical_id.encode()).hexdigest()),
                   known=record['recorded_at'],effective=record.get('effective_at'),
                   start=_utc_iso(when.get('start')),end=_utc_iso(when.get('end')),
                   valid_from=_utc_iso(payload.get('valid_from',payload.get('effective_at'))),
                   valid_to=_utc_iso(payload.get('valid_to',payload.get('expires_at'))),
                   precision=when.get('precision'),description=when.get('description'),
                   mid=self._uid('memory:'+logical_id),logical=logical_id,ns=self.namespace,kind=record['record_kind']).single()
        for source_id in dict.fromkeys(source_ids):
            result=tx.run('MATCH (r:MemoryRecord {uid:$rid}),(s:EvidenceSource {uid:$sid}) '
                          'MERGE (r)-[:EVIDENCED_BY]->(s) RETURN s.id AS id',
                          rid=self._uid(record['event_id']),sid=self._uid(source_id)).single()
            if not result:
                raise ValueError(f'来源不存在于当前分区：{source_id}')
        return _native(row['r'])

    def _check(self,tx,event_id,trigger,when):
        if when is not None:
            tx.run('MATCH (r:MemoryRecord {uid:$rid}) CREATE (c:DueCheck {uid:$uid,namespace:$ns,'
                   'event_id:$id,trigger:$trigger,due_at:datetime($due)}) CREATE (c)-[:CHECKS]->(r)',
                   rid=self._uid(event_id),uid=self._uid('check:'+event_id+':'+trigger),ns=self.namespace,
                   id=event_id,trigger=trigger,due=stamp(when)).consume()

    def _resolve(self,tx,identifier,local):
        if identifier in local:
            return local[identifier]
        row=tx.run('MATCH (r:MemoryRecord {namespace:$ns}) WHERE r.event_id=$id OR r.logical_id=$id '
                   'RETURN properties(r) AS r ORDER BY r.seq DESC LIMIT 1',ns=self.namespace,id=identifier).single()
        if not row:
            raise ValueError(f'记忆引用不存在于当前分区：{identifier}')
        return _native(row['r'])

    def _revision(self,tx,logical_id,replaces,kind):
        row=tx.run('MATCH (m:Memory {uid:$uid})-[:HAS_REVISION]->(r) RETURN properties(r) AS r ORDER BY r.seq DESC LIMIT 1',
                   uid=self._uid('memory:'+logical_id)).single()
        if row:
            old=_native(row['r'])
            if old['record_kind']!=kind:
                raise ValueError('同一记忆 ID 不能改变类型')
            if old['event_id']!=replaces:
                raise ConflictError('version_conflict','更新已有记忆必须通过 replaces 指向其最新 record_id',
                                    memory_id=logical_id,current_record_id=old['event_id'])
            return old
        if replaces:
            raise ValueError('replaces 与此记忆 ID 不匹配')
        return None

    def _write_states(self,tx,source,changes,now,context=None):
        ids=[]
        for index,change in enumerate(changes):
            eid=self._id(f'source:{source.id}:memory:{index}')
            logical=state_key(change.subject,change.key)
            previous=tx.run('MATCH (r:MemoryRecord {namespace:$ns,subject:$subject,key:$key}) '
                            'WHERE r.record_kind="state" RETURN r.body AS body LIMIT 1',
                            ns=self.namespace,subject=change.subject,key=change.key).single()
            if previous and json.loads(previous['body'])['kind']!=change.kind:
                raise ValueError('同一主体和属性不能混用事实、规则与计划；请使用不同 key')
            root=eid
            if change.operation=='correct':
                target=self._resolve(tx,change.corrects,{})
                if target['record_kind']!='state' or (target['subject'],target['key'])!=(change.subject,change.key):
                    raise ValueError('corrects 未找到匹配的主体、属性和分区')
                latest=tx.run('MATCH (r:MemoryRecord {namespace:$ns,subject:$subject,key:$key,root_id:$root}) '
                              'RETURN r.event_id AS id ORDER BY r.seq DESC LIMIT 1',
                              ns=self.namespace,subject=change.subject,key=change.key,root=target['root_id']).single()
                if latest['id']!=change.corrects:
                    raise ConflictError('version_conflict','纠错目标已被更正，请读取最新 event_id 后再提交',current_record_id=latest['id'])
                root=target['root_id']
            payload=change.model_dump(mode='json')
            self._put_record(tx,{'event_id':eid,'source_id':source.id,'subject':change.subject,'key':change.key,
                'root_id':root,'effective_at':stamp(change.effective_at),'recorded_at':now,'body':_json(payload),
                'record_kind':'state','seq':self._seq(tx),'text':change.text,
                'search_terms':' '.join(tokens(f'{change.subject} {change.key} {change.text} {_json(change.value)}'))},
                [source.id],'MemoryEvent',logical,context)
            if change.corrects:
                tx.run('MATCH (a:MemoryRecord {uid:$a}),(b:MemoryRecord {uid:$b}) CREATE (a)-[:CORRECTS]->(b)',
                       a=self._uid(eid),b=self._uid(change.corrects)).consume()
            checks={'activate':change.effective_at,'expire':change.expires_at,'review':change.review_at}
            if change.kind=='plan':
                checks=checks|{'deadline':change.due_at} if change.plan_status=='open' else {}
            for trigger,when in checks.items():
                self._check(tx,eid,trigger,when)
            ids.append(eid)
        return ids

    def _graph(self,tx,source,extraction,now,context=None):
        ids=[]; local={}
        for kind,items,label in [('experience',extraction.experiences,'Experience'),('understanding',extraction.understandings,'Understanding')]:
            for item in items:
                old=self._revision(tx,item.id,item.replaces,kind)
                eid=self._id(f'source:{source.id}:{kind}:{item.id}')
                effective=item.valid_from if kind=='understanding' else item.when.start
                root=old['root_id'] if old and item.revision_kind=='correct' else eid
                content=item.narrative if kind=='experience' else item.statement
                props={'event_id':eid,'root_id':root,'source_id':source.id,'record_kind':kind,
                       'seq':self._seq(tx),'recorded_at':now,'effective_at':stamp(effective) if effective else None,
                       'body':item.model_dump_json(),'title':item.title,'text':content}
                source_ids=[source.id]
                if kind=='experience':
                    source_ids+=item.source_ids
                    if old:
                        source_ids += [r['id'] for r in tx.run('MATCH (r:Experience {uid:$uid})-[:EVIDENCED_BY]->(s:EvidenceSource) RETURN s.id AS id',uid=old['uid'])]
                    search=f'{item.title} {content} '+ ' '.join(e.name+' '+' '.join(e.aliases) for e in item.entities)
                else:
                    search=f'{item.title} {content}'
                props['search_terms']=' '.join(tokens(search))
                row=self._put_record(tx,props,source_ids,label,item.id,context)
                local[item.id]=row;local[eid]=row;ids.append(eid)
                if old:
                    relation='CORRECTS' if item.revision_kind=='correct' else 'REVISES'
                    tx.run(f'MATCH (a:MemoryRecord {{uid:$a}}),(b:MemoryRecord {{uid:$b}}) CREATE (a)-[:{relation}]->(b)',
                           a=self._uid(eid),b=self._uid(old['event_id'])).consume()
                if kind=='experience':
                    for entity in item.entities:
                        tx.run('MATCH (r:MemoryRecord {uid:$rid}) MERGE (e:Entity {uid:$uid}) '
                               'ON CREATE SET e.namespace=$ns,e.id=$id,e.name=$name,e.category=$category,e.created_at=datetime($now),e.aliases=[] '
                               'SET e.aliases=reduce(a=e.aliases,x IN $aliases | CASE WHEN x IN a THEN a ELSE a+x END) '
                               'MERGE (r)-[:INVOLVES]->(e)',rid=self._uid(eid),uid=self._uid('entity:'+entity.id),
                               ns=self.namespace,id=entity.id,name=entity.name,category=entity.category,
                               aliases=entity.aliases,now=now).consume()
                        if entity.operation=='correct':
                            tx.run('MATCH (e:Entity {uid:$uid}) SET e.name=$name,e.category=$category,e.aliases=$aliases',
                                   uid=self._uid('entity:'+entity.id),name=entity.name,category=entity.category,
                                   aliases=entity.aliases).consume()
                else:
                    for ref in item.supports:
                        support=self._resolve(tx,ref,local)
                        if support['record_kind']!='experience':
                            raise ValueError('认识必须由真实经历支撑，不能让认识相互循环充当证据')
                        tx.run('MATCH (a:MemoryRecord {uid:$a}),(b:Experience {uid:$b}) MERGE (a)-[:SUPPORTED_BY]->(b)',
                               a=self._uid(eid),b=self._uid(support['event_id'])).consume()
                    for trigger,when in {'activate':item.valid_from,'expire':item.valid_to,'review':item.review_at}.items():
                        self._check(tx,eid,trigger,when)
        association_ids=[]
        for item in extraction.associations:
            previous=tx.run(ASSOCIATION_LOOKUP,ns=self.namespace,id=item.id).single()
            if (previous and previous['id']!=item.replaces) or (not previous and item.replaces):
                raise ConflictError('version_conflict','更新关联需要 replaces 指向其最新 association_version_id',
                                    current_record_id=previous['id'] if previous else None)
            left,right=self._resolve(tx,item.from_id,local),self._resolve(tx,item.to_id,local)
            if left['record_kind']=='state' or right['record_kind']=='state':
                raise ValueError('通用关联的两端应为经历或认识')
            if left['logical_id']==right['logical_id']:
                raise ValueError('关联不能指向同一记忆自身')
            evidence=[]
            for ref in item.evidence_ids:
                src=tx.run('MATCH (s:EvidenceSource {uid:$uid}) RETURN s.id AS id,s.role AS role',uid=self._uid(ref)).single()
                if src:
                    if src['role']=='assistant':
                        raise ValueError('关联依据应引用用户或工具来源、真实经历，不能用助手自己的表述作证据')
                    evidence.append('source:'+ref)
                else:
                    support=self._resolve(tx,ref,local)
                    if support['record_kind']!='experience':
                        raise ValueError('关联依据必须为原始来源或经历')
                    evidence.append('record:'+support['event_id'])
            aid=self._id(f'source:{source.id}:association:{item.id}')
            props={'id':aid,'logical_id':item.id,'namespace':self.namespace,'category':'association','kind':item.kind,
                   'explanation':item.explanation,'basis':item.basis,'evidence_ids':evidence,'source_id':source.id,
                   'seq':self._seq(tx),'body':item.model_dump_json(),'conversation_id':source.conversation_id,
                   **(context.model_dump(exclude_none=True) if context else {})}
            tx.run(f'MATCH (a:Memory {{uid:$a}}),(b:Memory {{uid:$b}}) CREATE (a)-[r:{RELATIONS[item.kind]}]->(b) '
                   'SET r=$props,r.recorded_at=datetime($now),r.valid_from=datetime($start),r.valid_to=datetime($end)',
                   a=self._uid('memory:'+left['logical_id']),b=self._uid('memory:'+right['logical_id']),
                   props=props,now=now,start=stamp(item.valid_from) if item.valid_from else None,
                   end=stamp(item.valid_to) if item.valid_to else None).consume()
            association_ids.append(aid)
        return ids,association_ids

    def _applied_result(self,receipt,source,fingerprint):
        if receipt['body']!=_json(source.model_dump(mode='json')):
            raise ConflictError('source_conflict','相同 source.id 的内容或时间不能改变；重试必须提交相同消息',source_id=source.id)
        saved=json.loads(receipt['extraction'])
        saved['extraction']=Extraction.model_validate(saved['extraction']).model_dump(mode='json')
        saved.setdefault('context',None)
        if _json(saved)!=fingerprint:
            raise ConflictError('source_conflict','该 source.id 已应用另一份提取结果；修改记忆请使用新的来源 ID',source_id=source.id)
        return json.loads(receipt['result'])

    def apply(self,source:Source,extraction:Extraction,*,review_id=None,context=None):
        from .maintenance import ensure_writable
        ensure_writable(self,source,extraction,self._run)
        receipt=self.source(source.id)
        if receipt is None or receipt['status']!='applied':
            self.receive(source)
        fingerprint=_json({'extraction':extraction.model_dump(mode='json'),'review_id':review_id,'context':context.model_dump() if context else None})
        if source.role=='assistant' and (extraction.memories or extraction.experiences or
                                       any(u.basis=='explicit' for u in extraction.understandings) or
                                       any(a.basis=='explicit' for a in extraction.associations)):
            raise ValueError('助手复述不能成为新事实或经历；可以引用真实经历保存明确标注 inferred 的认识')
        # An applied receipt is immutable. Validating it is a read, not another
        # namespace write; the transaction below still arbitrates racing writers.
        if receipt and receipt['status']=='applied':
            return self._applied_result(receipt,source,fingerprint)
        def write(tx):
            ensure_writable(self,source,extraction,lambda q,**p:list(tx.run(q,**p)))
            receipt=tx.run('MATCH (s:EvidenceSource {uid:$uid}) RETURN properties(s) AS s',uid=self._uid(source.id)).single()['s']
            if receipt['status']=='applied':
                return self._applied_result(receipt,source,fingerprint)
            now=stamp(self.clock())
            state_ids=self._write_states(tx,source,extraction.memories,now,context)
            records,associations=self._graph(tx,source,extraction,now,context)
            review_ids=[]
            for index,candidate in enumerate(extraction.review):
                rid=self._id(f'source:{source.id}:review:{index}')
                tx.run('MATCH (s:EvidenceSource {uid:$sid}) CREATE (r:Review {uid:$uid,id:$id,namespace:$ns,'
                       'source_id:$source,body:$body,created_at:datetime($now)}) CREATE (r)-[:EVIDENCED_BY]->(s)',
                       sid=self._uid(source.id),uid=self._uid(rid),id=rid,ns=self.namespace,source=source.id,
                       body=candidate.model_dump_json(),now=now).consume()
                review_ids.append(rid)
            if review_id:
                row=tx.run('MATCH (r:Review {uid:$uid}) WHERE r.resolved_at IS NULL '
                           'SET r.resolved_at=datetime($now),r.resolution_source=$source RETURN r.id AS id',
                           uid=self._uid(review_id),now=now,source=source.id).single()
                if row is None:
                    raise ValueError('待确认项不存在或已经处理')
            result={'source_id':source.id,'event_ids':state_ids+records,'record_ids':records,
                    'association_ids':associations,'review_ids':review_ids,'status':'applied','recorded_at':now}
            tx.run('MATCH (s:EvidenceSource {uid:$uid}) SET s.status="applied",s.error=null,s.result=$result,s.extraction=$input',
                   uid=self._uid(source.id),result=_json(result),input=fingerprint).consume()
            return result
        return self._write(write)

    def _rows(self,known_at,kind=None,logical_ids=None,revision=None,subject=None,include_withdrawn=False):
        if logical_ids is not None and not logical_ids:
            return []
        if revision is None:
            revision=self.capture_view(known_at=known_at).revision
        identities=[];generic=[]
        for key in dict.fromkeys(logical_ids or []):
            if key.startswith('state:v2:'):
                try:
                    pair=json.loads(key[len('state:v2:'):])
                except json.JSONDecodeError:
                    pair=None
                if isinstance(pair,list) and len(pair)==2 and all(isinstance(v,str) for v in pair):
                    identities.append({'subject':pair[0],'key':pair[1]});continue
            generic.append(key)
        if logical_ids is None:
            query='MATCH (r:MemoryRecord {namespace:$ns}) '
        else:
            # Match state identity by its actual fields to include legacy rows
            # without conflating colon-separated subject/key pairs.
            query=('CALL { UNWIND $uids AS uid MATCH (m:Memory {uid:uid})-[:HAS_REVISION]->(r:MemoryRecord) RETURN r '
                   'UNION UNWIND $identities AS pair MATCH (r:MemoryRecord {namespace:$ns,subject:pair.subject,key:pair.key}) '
                   'WHERE r.record_kind="state" RETURN r } WITH DISTINCT r ')
        query+='WHERE r.namespace=$ns AND r.seq<=$revision AND r.recorded_at<=datetime($known) AND ($include_withdrawn OR NOT coalesce(r.withdrawn,false))'
        if kind:
            query+=' AND r.record_kind=$kind'
        if subject is not None:
            query+=' AND r.subject=$subject'
        return [_native(x['r']) for x in self._run(query+' RETURN properties(r) AS r ORDER BY r.seq',
                ns=self.namespace,known=stamp(known_at),kind=kind,revision=revision,subject=subject,
                uids=[self._uid('memory:'+key) for key in generic],identities=identities,include_withdrawn=include_withdrawn)]

    def _visible(self,known_at,subject=None,key=None,*,logical_ids=None,revision=None):
        if subject is not None and key is not None:
            logical_ids=[state_key(subject,key)]
        rows=self._rows(known_at,'state',logical_ids,revision,subject)
        roots={(r['subject'],r['key'],r['root_id']):r for r in rows}
        return rows,list(roots.values())

    @staticmethod
    def _select_records(rows, at):
        groups={}
        for row in rows:
            if row['record_kind']!='state':
                groups.setdefault(row['logical_id'],[]).append(row)
        selected=[]
        for versions in groups.values():
            roots={r['root_id']:r for r in versions}
            choices=([versions[-1]] if versions[0]['record_kind']=='experience' else
                     sorted(roots.values(),key=lambda r:r['seq'],reverse=True))
            for row in choices:
                payload=json.loads(row['body'])
                start=payload.get('valid_from') if row['record_kind']=='understanding' else payload['when']['start']
                if start and datetime.fromisoformat(start)>at:
                    continue
                selected.append({**payload,'record_id':row['event_id'],'event_id':row['event_id'],
                    'record_kind':row['record_kind'],'recorded_at':row['recorded_at'],'source_id':row['source_id'],
                    'text':row['text'],'withdrawn':bool(row.get('withdrawn')),'status':'withdrawn' if row.get('withdrawn') else 'active'})
                break
        return selected

    def _head_rows(self,view,logical_ids=None,include_withdrawn=False):
        if logical_ids==[]:
            return []
        match=('MATCH (m:Memory {namespace:$ns}) ' if logical_ids is None else
               'UNWIND $uids AS uid MATCH (m:Memory {uid:uid,namespace:$ns}) ')
        rows=self._run(match+RECORD_HEAD+'RETURN properties(current) AS r ORDER BY current.seq',
            ns=self.namespace,uids=[self._uid('memory:'+key) for key in dict.fromkeys(logical_ids or [])],
            at=stamp(view.at),known=stamp(view.known_at),revision=view.revision,include_withdrawn=include_withdrawn)
        return [_native(row['r']) for row in rows]

    def records(self,at=None,known_at=None,include_inactive=False,*,view=None,logical_ids=None,filters=None,include_withdrawn=False):
        view=view or self.capture_view(at,known_at)
        selected=self._select_records(self._head_rows(view,logical_ids,include_withdrawn),view.at)
        understanding_ids=[r['record_id'] for r in selected if r['record_kind']=='understanding']
        supports={}; support_keys=set()
        if understanding_ids:
            support_rows=self._run('UNWIND $uids AS uid MATCH (r:Understanding {uid:uid})-[:SUPPORTED_BY]->(e:Experience) '
                'WHERE e.seq<=$revision AND e.recorded_at<=datetime($known) '
                'RETURN r.event_id AS id,collect(e.event_id) AS ids,collect(e.logical_id) AS keys',
                uids=[self._uid(i) for i in understanding_ids],revision=view.revision,known=stamp(view.known_at))
            supports={r['id']:r['ids'] for r in support_rows}
            for r in support_rows:
                support_keys.update(r['keys'])
        live_supports=self._select_records(self._head_rows(view,list(support_keys)),view.at) if support_keys else []
        live_experiences={r['record_id'] for r in live_supports}
        for record in selected:
            if record['record_kind']=='understanding':
                record['support_record_ids']=supports.get(record['record_id'],[])
                record['evidence_status']='current' if all(e in live_experiences for e in record['support_record_ids']) else 'changed'
                if record.get('withdrawn'):continue
                if record.get('valid_to') and datetime.fromisoformat(record['valid_to'])<=view.at:
                    record['status']='expired'
                elif record['evidence_status']=='changed' or (record.get('review_at') and datetime.fromisoformat(record['review_at'])<=view.at):
                    record['status']='needs_review'
        selected=[r for r in selected if include_inactive or r['status']!='expired']
        allowed=self.matching_records([r['record_id'] for r in selected],filters,view,include_withdrawn) if filters else None
        return [r for r in selected if allowed is None or r['record_id'] in allowed]

    def record_keys(self, identifiers, view):
        rows=self._run('UNWIND $uids AS uid MATCH (r:MemoryRecord {uid:uid}) '
            'WHERE r.seq<=$revision AND r.recorded_at<=datetime($known) '
            'RETURN DISTINCT r.logical_id AS key,r.record_kind AS kind,r.subject AS subject,r.key AS state_key',
            uids=[self._uid(i) for i in identifiers],revision=view.revision,known=stamp(view.known_at))
        return list(dict.fromkeys(state_key(r['subject'],r['state_key']) if r['kind']=='state' else r['key'] for r in rows))

    def details(self,event_ids,known_at=None,*,at=None,view=None,source_mode='full',include_withdrawn=False):
        if source_mode not in ('full','summary'):
            raise ValueError('source_mode 必须是 full 或 summary')
        view=view or self.capture_view(at=at,known_at=known_at)
        keys=self.record_keys(event_ids,view)
        current={r['record_id']:r for r in self.records(view=view,logical_ids=keys,include_inactive=True,include_withdrawn=include_withdrawn)}
        projection=('s.body' if source_mode=='full' else
            'CASE WHEN s IS NULL THEN null ELSE {id:s.id,role:s.role,occurred_at:toString(s.occurred_at),'
            'conversation_id:s.conversation_id,content_chars:size(s.content),content_omitted:true} END')
        rows=self._run('UNWIND $uids AS uid MATCH (r:MemoryRecord {uid:uid}) '
                       'WHERE r.seq<=$revision AND r.recorded_at<=datetime($known) AND ($include_withdrawn OR NOT coalesce(r.withdrawn,false)) '
                       'OPTIONAL MATCH (r)-[:EVIDENCED_BY]->(s:EvidenceSource) '
                       'RETURN properties(r) AS r,collect('+projection+') AS sources',
                       uids=[self._uid(i) for i in dict.fromkeys(event_ids)],revision=view.revision,known=stamp(view.known_at),include_withdrawn=include_withdrawn)
        out=[]
        for item in rows:
            row=_native(item['r'])
            record=self._event(row)
            record['record_id']=row['event_id'];record['record_kind']=row['record_kind']
            record['sources']=[json.loads(s) for s in item['sources']] if source_mode=='full' else item['sources']
            record['source_mode']=source_mode
            record['source_count']=len(record['sources'])
            record['source']=next((s for s in record['sources'] if s['id']==row['source_id']),None)
            if row['record_kind']!='state':
                record['superseded']=row['event_id'] not in current
            if row['record_kind']=='understanding':
                evidence=self._run('MATCH (r:Understanding {uid:$uid})-[:SUPPORTED_BY]->(e:Experience) '
                                  'WHERE e.seq<=$revision AND e.recorded_at<=datetime($known) RETURN e.event_id AS id',
                                  uid=row['uid'],revision=view.revision,known=stamp(view.known_at))
                record['support_record_ids']=[r['id'] for r in evidence]
                projection=current.get(row['event_id'])
                if projection:
                    record.update({k:projection[k] for k in ('status','evidence_status')})
                else:
                    support_keys=self.record_keys(record['support_record_ids'],view)
                    live={r['record_id'] for r in self.records(view=view,logical_ids=support_keys,include_inactive=True)}
                    record['evidence_status']='current' if all(i in live for i in record['support_record_ids']) else 'changed'
                    record['status']='superseded'
                    if record.get('valid_from') and datetime.fromisoformat(record['valid_from'])>view.at:
                        latest=self.latest_record_ids([record['id']],view)
                        if latest and latest[0]['id']==record['record_id']:
                            record['status']='scheduled'
                            record['superseded']=False
            if row.get('withdrawn'):record['status']='withdrawn'
            record['at']=stamp(view.at)
            record['known_at']=stamp(view.known_at)
            out.append(record)
        by_id={r['event_id']:r for r in out}
        return [by_id[i] for i in dict.fromkeys(event_ids) if i in by_id]

    def keyword_matches(self,query,view,limit=100,*,logical_keys=False,record_ids=None,subject=None,filters=None):
        parts=tokens(query)
        if not parts or record_ids==[]:
            return []
        cypher='CALL db.index.fulltext.queryNodes("chronicle_text",$query) YIELD node,score '
        cypher+='WHERE node.namespace=$ns AND node.seq<=$revision AND node.recorded_at<=datetime($known) AND NOT coalesce(node.withdrawn,false) AND '+filter_clause('node')+' '
        if record_ids is not None:
            cypher+='AND node.event_id IN $ids '
        if subject is not None:
            cypher+='AND (node.subject=$subject OR node.logical_id=$subject OR node.title=$subject OR EXISTS { MATCH (node)-[:INVOLVES]->(e:Entity) WHERE e.id=$subject OR e.name=$subject OR $subject IN e.aliases }) '
        if logical_keys:
            cypher+=('WITH node,score ORDER BY score DESC,node.seq DESC '
                'WITH node.logical_id AS key,node.record_kind AS kind,node.subject AS subject,node.key AS state_key, '
                'max(score) AS score,collect(node.event_id)[..8] AS matched_record_ids '
                'RETURN key,kind,subject,state_key,score,head(matched_record_ids) AS id,matched_record_ids '
                'ORDER BY score DESC,key LIMIT $limit')
        else:
            cypher+=('RETURN node.event_id AS id,node.logical_id AS key,node.record_kind AS kind, '
                     'node.subject AS subject,node.key AS state_key,score ORDER BY score DESC,id LIMIT $limit')
        found={}
        # Lucene caps BooleanQuery clauses. Keep the entire source and query all
        # chunks; LIMIT alone does not protect query parsing from long messages.
        for offset in range(0,len(parts),256):
            expression=' OR '.join('"'+part+'"' for part in parts[offset:offset+256])
            rows=self._run(cypher,query=expression,ns=self.namespace,revision=view.revision,known=stamp(view.known_at),
                           ids=record_ids,subject=subject,limit=limit,**filter_params(filters))
            for row in rows:
                if row['kind']=='state':
                    row['key']=state_key(row['subject'],row['state_key'])
                key=row['key'] if logical_keys else row['id']
                previous=found.get(key)
                if previous and logical_keys:
                    ids=list(dict.fromkeys(previous['matched_record_ids']+row['matched_record_ids']))[:8]
                else:
                    ids=row.get('matched_record_ids',[])
                if previous is None or row['score']>previous['score']:
                    found[key]=row
                if logical_keys:
                    found[key]['matched_record_ids']=ids
        return sorted(found.values(),key=lambda r:(-r['score'],r['key'],r['id']))[:limit]

    def lexical(self,query,limit=100):
        return [r['id'] for r in self.keyword_matches(query,self.capture_view(),limit)]

    def association_views(self,at=None,known_at=None,*,view=None,memory_keys=None):
        view=view or self.capture_view(at,known_at)
        if memory_keys is None:
            match='MATCH (a:Memory)-[r]->(b:Memory) '
        else:
            match='UNWIND $uids AS uid MATCH (m:Memory {uid:uid})-[r]-(other:Memory) WITH DISTINCT r,startNode(r) AS a,endNode(r) AS b '
        rows=self._run(match+'WHERE r.category="association" AND r.namespace=$ns '
                       'AND r.seq<=$revision AND r.recorded_at<=datetime($known) '
                       'RETURN properties(r) AS r,a.id AS from_id,b.id AS to_id ORDER BY r.seq',
                       ns=self.namespace,revision=view.revision,known=stamp(view.known_at),
                       uids=[self._uid('memory:'+key) for key in memory_keys or []])
        latest={row['r']['logical_id']:{**_native(row['r']),'from_id':row['from_id'],'to_id':row['to_id']} for row in rows}
        return [r for r in latest.values() if (not r.get('valid_from') or datetime.fromisoformat(r['valid_from'])<=view.at)
                and (not r.get('valid_to') or view.at<datetime.fromisoformat(r['valid_to']))]

    def _entity_records(self,matching,view,limit,*,record_ids=None,exclude=None,filters=None,**parameters):
        return self._run(matching+
            ' MATCH (e)<-[:INVOLVES]-(previous:Experience)<-[:HAS_REVISION]-(m:Memory {namespace:$ns}) '
            'WHERE previous.seq<=$revision AND previous.recorded_at<=datetime($known) '
            'WITH m,collect(DISTINCT e) AS matched_entities '+CURRENT_RECORD+
            'MATCH (current)-[:INVOLVES]->(entity:Entity) '
            'WHERE entity IN matched_entities AND ($ids IS NULL OR current.event_id IN $ids) '
            'AND ($exclude IS NULL OR current.event_id<>$exclude) AND '+filter_clause('current')+' '
            'WITH current,entity ORDER BY entity.id '
            'WITH current,collect(DISTINCT {entity_id:entity.id,name:entity.name}) AS entities '
            'RETURN current.event_id AS id,current.logical_id AS key,entities '
            'ORDER BY current.recorded_at DESC,key LIMIT $limit',
            ns=self.namespace,revision=view.revision,known=stamp(view.known_at),at=stamp(view.at),
            ids=record_ids,exclude=exclude,limit=limit,**filter_params(filters),**parameters)

    def entity_candidates(self,query,view,limit=100,*,record_ids=None,subject=None,filters=None):
        if record_ids==[]:
            return []
        # Short names require an exact query or explicit subject. English names
        # use word boundaries, so "Ann" does not match "planning".
        return self._entity_records('MATCH (e:Entity {namespace:$ns}) '
            'WHERE any(name IN [e.id,e.name]+e.aliases WHERE '
            'toLower(name)=toLower(trim($query)) OR name=$subject OR '
            '(name =~ ".*[一-鿿].*" AND size(name)>=3 AND toLower($query) CONTAINS toLower(name)) OR '
            '(NOT name =~ ".*[一-鿿].*" AND toLower(name) IN $words)) '
            'AND ($subject IS NULL OR e.id=$subject OR e.name=$subject OR $subject IN e.aliases)',
            view,limit,record_ids=record_ids,query=query,subject=subject,filters=filters,
            words=re.findall(r"[a-z0-9_]+",query.lower()))

    def entity_matches(self,query):
        return [r['id'] for r in self.entity_candidates(query,self.capture_view())]

    def neighbors(self,record_id,at=None,known_at=None,limit=12,*,view=None,filters=None):
        view=view or self.capture_view(at,known_at)
        keys=self.record_keys([record_id],view) or [record_id]
        seed_records=self.records(view=view,logical_ids=keys,filters=filters)
        seed=next((r for r in seed_records if record_id in (r['record_id'],r['id'])),None)
        if not seed:
            raise ValueError('该时间视角下没有此经历或认识；旧版本请提供对应 known_at')
        links=self.association_views(view=view,memory_keys=[seed['id']])
        shared=self._entity_records('MATCH (a:MemoryRecord {uid:$uid})-[:INVOLVES]->(e:Entity)',
            view,limit+1,exclude=seed['record_id'],uid=self._uid(seed['record_id']),filters=filters)
        if seed['record_kind']=='experience':
            evidence_query=('MATCH (a:Memory {uid:$memory})-[:HAS_REVISION]->(:Experience)<-[:SUPPORTED_BY]-(previous:Understanding)'
                '<-[:HAS_REVISION]-(m:Memory {namespace:$ns}) '
                'WHERE previous.seq<=$revision AND previous.recorded_at<=datetime($known) '
                'WITH DISTINCT a,m '+CURRENT_RECORD+
                'MATCH (current)-[:SUPPORTED_BY]->(cited:Experience)<-[:HAS_REVISION]-(a) ')
        else:
            evidence_query=('MATCH (a:Understanding {uid:$uid})-[:SUPPORTED_BY]->(previous:Experience)'
                '<-[:HAS_REVISION]-(m:Memory {namespace:$ns}) '
                'WHERE previous.seq<=$revision AND previous.recorded_at<=datetime($known) '
                'WITH DISTINCT a,m '+CURRENT_RECORD+
                'MATCH (a)-[:SUPPORTED_BY]->(cited:Experience)<-[:HAS_REVISION]-(m) ')
        evidence=self._run(evidence_query+
            'WHERE cited.seq<=$revision AND cited.recorded_at<=datetime($known) AND '+filter_clause('current')+' '
            'WITH current,collect(DISTINCT cited.event_id) AS cited_record_ids '
            'RETURN current.event_id AS id,current.logical_id AS key,cited_record_ids '
            'ORDER BY current.recorded_at DESC,key LIMIT $limit',uid=self._uid(seed['record_id']),memory=self._uid('memory:'+seed['id']),
            ns=self.namespace,revision=view.revision,known=stamp(view.known_at),at=stamp(view.at),limit=limit+1,**filter_params(filters))
        traversal_truncated=len(shared)>limit or len(evidence)>limit
        shared=shared[:limit];evidence=evidence[:limit]
        targets={r['key'] for r in shared+evidence}
        for link in links:
            targets.update((link['from_id'],link['to_id']))
        visible=self.records(view=view,logical_ids=list(targets),filters=filters)
        by_id={r['record_id']:r for r in visible};by_key={r['id']:r for r in visible}
        found={}
        def add(other,reason,via):
            if other and other['record_id']!=seed['record_id']:
                item=found.setdefault(other['record_id'],{'record':other,'reasons':[]})
                item['reasons'].append({'reason':reason,'via':via})
        for link in links:
            target=link['to_id'] if link['from_id']==seed['id'] else link['from_id']
            add(by_key.get(target),link['kind'],{k:link.get(k) for k in ('id','basis','explanation','evidence_ids')})
        for row in shared:
            for entity in row['entities']:
                add(by_id.get(row['id']),'shared_entity',entity)
        for row in evidence:
            current_id=seed['record_id'] if seed['record_kind']=='experience' else row['id']
            add(by_id.get(row['id']),'evidence',{'cited_record_ids':row['cited_record_ids'],
                'current_record_id':current_id,'evidence_status':'current' if row['cited_record_ids']==[current_id] else 'changed'})
        def rank(item):
            priority=min(2 if r['reason']=='shared_entity' else 1 if r['reason']=='evidence' else 0 for r in item['reasons'])
            return priority,-datetime.fromisoformat(item['record']['recorded_at']).timestamp(),item['record']['id']
        ordered=sorted(found.values(),key=rank)
        return {'record_id':seed['record_id'],'at':stamp(view.at),'known_at':stamp(view.known_at),
                'revision':view.revision,'neighbors':ordered[:limit],
                'truncated':traversal_truncated or len(ordered)>limit}

    def history(self,memory_id=None,entity_id=None,start=None,end=None,known_at=None,limit=30,filters=None,include_withdrawn=False):
        view=self.capture_view(known_at=known_at)
        base='MATCH (r:MemoryRecord {namespace:$ns}) WHERE r.record_kind<>"state" AND r.seq<=$revision AND r.recorded_at<=datetime($known) AND ($include_withdrawn OR NOT coalesce(r.withdrawn,false)) AND '+filter_clause('r')+' '
        if memory_id:
            base+='AND (r.logical_id=$id OR r.event_id=$id) '
        if entity_id:
            base+=('AND (EXISTS { MATCH (r)-[:INVOLVES]->(e:Entity {uid:$entity}) } OR EXISTS { '
                   'MATCH (r)-[:SUPPORTED_BY]->(e:Experience)-[:INVOLVES]->(:Entity {uid:$entity}) '
                   'WHERE e.seq<=$revision AND e.recorded_at<=datetime($known) }) ')
        base+=('WITH r,coalesce(r.occurred_start,r.valid_from) AS begin, '
               'CASE WHEN r.record_kind="understanding" THEN r.valid_to '
               'WHEN r.occurred_end IS NOT NULL THEN r.occurred_end '
               'WHEN r.time_precision="day" THEN r.occurred_start+duration({days:1}) '
               'ELSE r.occurred_start END AS finish, '
               '(r.record_kind="understanding" OR r.occurred_end IS NOT NULL OR r.time_precision="day") AS interval '
               'WITH r,interval,CASE WHEN begin IS NULL THEN null ELSE datetime({datetime:begin,timezone:"+00:00"}) END AS begin, '
               'CASE WHEN finish IS NULL THEN null ELSE datetime({datetime:finish,timezone:"+00:00"}) END AS finish ')
        args=dict(ns=self.namespace,revision=view.revision,known=stamp(view.known_at),id=memory_id,
                  entity=self._uid('entity:'+entity_id) if entity_id else None,
                  start=stamp(start) if start else None,end=stamp(end) if end else None,limit=limit+1,include_withdrawn=include_withdrawn,**filter_params(filters))
        timed=self._run(base+'WHERE begin IS NOT NULL AND ($start IS NULL OR finish IS NULL OR '
            '(interval AND finish>datetime($start)) OR (NOT interval AND finish>=datetime($start))) '
            'AND ($end IS NULL OR begin<datetime($end)) RETURN r.event_id AS id,r.logical_id AS key ORDER BY begin,r.seq LIMIT $limit',**args)
        unknown=self._run(base+'WHERE begin IS NULL RETURN r.event_id AS id,r.logical_id AS key ORDER BY r.recorded_at,r.seq LIMIT $limit',**args)
        ids=[r['id'] for r in timed[:limit]];unknown_ids=[r['id'] for r in unknown[:limit]]
        keys=list({r['key'] for r in timed[:limit]+unknown[:limit]})
        links=self._run('UNWIND $uids AS uid MATCH (m:Memory {uid:uid})-[r]-(other:Memory) '
            'WHERE r.category="association" AND r.namespace=$ns AND r.seq<=$revision AND r.recorded_at<=datetime($known) '
            'AND ($include_withdrawn OR NOT EXISTS { MATCH (other)-[:HAS_REVISION]->(hidden:MemoryRecord) WHERE coalesce(hidden.withdrawn,false) }) '
            'AND ($filter_origin IS NULL OR r.origin=$filter_origin) '
            'AND ($filter_workspace IS NULL OR r.workspace=$filter_workspace) '
            'AND ($filter_conversation_id IS NULL OR r.conversation_id=$filter_conversation_id) '
            'AND ($filter_source_id IS NULL OR r.source_id=$filter_source_id) '
            'WITH DISTINCT r RETURN properties(r) AS r,startNode(r).id AS from_id,endNode(r).id AS to_id '
            'ORDER BY r.recorded_at,r.seq LIMIT $limit',uids=[self._uid('memory:'+key) for key in keys],
            ns=self.namespace,revision=view.revision,known=stamp(view.known_at),limit=limit+1,
            include_withdrawn=include_withdrawn,**filter_params(filters))
        latest=self._association_heads([r['r']['logical_id'] for r in links[:limit]],view)
        return {'known_at':stamp(view.known_at),'revision':view.revision,
                'records':self.details(ids,view=view,include_withdrawn=include_withdrawn),'undated':self.details(unknown_ids,view=view,include_withdrawn=include_withdrawn),
                'associations':[{**_native(r['r']),'from_id':r['from_id'],'to_id':r['to_id'],
                    'superseded':r['r']['id']!=latest[r['r']['logical_id']],
                    'current_version_id':latest[r['r']['logical_id']]} for r in links[:limit]],
                'truncated':len(timed)>limit or len(unknown)>limit or len(links)>limit,
                'note':'undated 不参与精确日期范围断言；完整叙述保留时间的不确定性。'}

    def _association_heads(self,identifiers,view):
        if not identifiers:
            return {}
        query='CALL { '+' UNION ALL '.join(
            f'UNWIND $ids AS id MATCH ()-[r:{kind}]->() WHERE r.namespace=$ns AND r.logical_id=id '
            'AND r.category="association" AND r.seq<=$revision AND r.recorded_at<=datetime($known) RETURN r'
            for kind in RELATIONS.values())+' } WITH r ORDER BY r.seq DESC '
        rows=self._run(query+'RETURN r.logical_id AS key,head(collect(r.id)) AS id',
                       ids=list(dict.fromkeys(identifiers)),ns=self.namespace,revision=view.revision,known=stamp(view.known_at))
        return {r['key']:r['id'] for r in rows}

    def upcoming(self,subjects,at,known_at,limit=8,*,view=None,filters=None):
        view=view or self.capture_view(at,known_at)
        candidates=self._run('MATCH (r:MemoryEvent {namespace:$ns}) WHERE r.subject IN $subjects '
            'AND r.seq<=$revision AND r.recorded_at<=datetime($known) '
            'AND (r.effective_at>datetime($at) OR EXISTS { MATCH (c:DueCheck)-[:CHECKS]->(r) '
            'WHERE c.trigger IN ["deadline","review"] AND c.due_at>datetime($at) }) '
            'RETURN DISTINCT r.subject AS subject,r.key AS key',ns=self.namespace,subjects=list(subjects),
            revision=view.revision,known=stamp(view.known_at),at=stamp(view.at))
        keys=[state_key(r['subject'],r['key']) for r in candidates]
        _,rows=self._visible(view.known_at,logical_ids=keys,revision=view.revision)
        states={(r['subject'],r['key']):r for r in self.states(view=view,logical_ids=keys)}
        due=[]
        future_states={}
        for row in rows:
            event=self._event(row)
            if event['kind']=='plan' and event['plan_status']!='open':
                continue
            effective=datetime.fromisoformat(event['effective_at'])
            if effective>view.at:
                key=(event['subject'],event['key'],effective)
                if key not in future_states:
                    future_states[key]=self.state(event['subject'],event['key'],view=view.at_time(effective))
                state=future_states[key]
                if any(event['event_id']==c['memory']['event_id'] for c in state['candidates']):
                    status='scheduled' if state['status']=='active' else state['status']
                    due.append((stamp(effective),{**event,'status':status}))
                continue
            if event['kind']!='plan' or event['plan_status']!='open':
                continue
            point=event.get('due_at') or event.get('review_at')
            if not point or datetime.fromisoformat(point)<=view.at:
                continue
            state=states.get((event['subject'],event['key']),{'candidates':[]})
            if any(event['event_id']==c['memory']['event_id'] for c in state['candidates']):
                due.append((stamp(datetime.fromisoformat(point)),{**event,'status':state['status']}))
        allowed=self.matching_records([event['event_id'] for _,event in due],filters,view) if filters else None
        return [event for _,event in sorted(due,key=lambda item:(item[0],item[1]['event_id'])) if allowed is None or event['event_id'] in allowed][:limit]

    def tick(self):
        def write(tx):
            now=self.clock()
            revision=tx.run('MATCH (n:MemoryNamespace {name:$ns}) RETURN n.sequence AS revision',ns=self.namespace).single()['revision']
            view=ReadView(now,now,revision)
            rows=list(tx.run('MATCH (c:DueCheck {namespace:$ns})-[:CHECKS]->(r:MemoryRecord) '
                             'WHERE c.processed_at IS NULL AND c.due_at<=datetime($now) '
                             'AND NOT coalesce(r.withdrawn,false) '
                             'RETURN properties(c) AS c,properties(r) AS r ORDER BY c.due_at,c.uid LIMIT 100',ns=self.namespace,now=stamp(now)))
            keys=list({r['r']['logical_id'] for r in rows})
            general={r['record_id']:r for r in self.records(view=view,logical_ids=keys,include_inactive=True)}
            results=[]
            projected_states={}
            for row in rows:
                check,record=_native(row['c']),_native(row['r'])
                if record['record_kind']=='state':
                    if record['logical_id'] not in projected_states:
                        projected_states[record['logical_id']]=self.state(record['subject'],record['key'],view=view)
                    state=projected_states[record['logical_id']]
                    active={e['event_id'] for c in state['candidates'] for e in c['evidence']}
                    status=state['status']
                else:
                    active=set(general)
                    status=general.get(record['event_id'],{}).get('status')
                if check['event_id'] not in active:
                    outcome='superseded'
                elif status in ('conflict','expired'):
                    outcome=status
                elif status in ('completed','cancelled'):
                    outcome='closed'
                else:
                    outcome={'activate':'activated','expire':'expired','review':'needs_review','deadline':'awaiting_outcome'}[check['trigger']]
                tx.run('MATCH (c:DueCheck {uid:$uid}) SET c.processed_at=datetime($now),c.outcome=$outcome',
                       uid=check['uid'],now=stamp(now),outcome=outcome).consume()
                results.append({k:check[k] for k in ('event_id','trigger','due_at')}|{'processed_at':stamp(now),'outcome':outcome})
            return results
        return self._write(write)

    def checks(self,limit=50,offset=0):
        rows=self._run('MATCH (c:DueCheck {namespace:$ns}) RETURN properties(c) AS c '
                       'ORDER BY c.due_at,c.uid SKIP $offset LIMIT $limit',ns=self.namespace,offset=offset,limit=limit+1)
        return {'items':[_native(r['c']) for r in rows[:limit]],'truncated':len(rows)>limit,
                'next_offset':offset+limit if len(rows)>limit else None}

    def pending_understandings(self,view,limit=50,offset=0):
        rows=self._run('MATCH (m:Memory {namespace:$ns,kind:"understanding"}) '+CURRENT_RECORD+
            'WITH current WHERE (EXISTS { '
            'MATCH (c:DueCheck)-[:CHECKS]->(current) WHERE c.trigger="review" AND c.due_at<=datetime($at) }) '
            'OR EXISTS { MATCH (current)-[:SUPPORTED_BY]->(cited:Experience)<-[:HAS_REVISION]-(e:Memory) '
            'WHERE coalesce(cited.withdrawn,false) OR cited.occurred_start>datetime($at) OR EXISTS { MATCH (e)-[:HAS_REVISION]->(newer:Experience) '
            'WHERE newer.seq>cited.seq AND newer.seq<=$revision AND newer.recorded_at<=datetime($known) } } '
            'RETURN current.logical_id AS key ORDER BY current.recorded_at,current.logical_id SKIP $offset LIMIT $limit',
            ns=self.namespace,revision=view.revision,known=stamp(view.known_at),at=stamp(view.at),offset=offset,limit=limit)
        records={r['id']:r for r in self.records(view=view,logical_ids=[r['key'] for r in rows])}
        return [{**records[row['key']],'review_kind':'understanding','resolution':'memory_write'}
                for row in rows if row['key'] in records and records[row['key']]['status']=='needs_review']

    def reviews(self,kind='all',limit=50,offset=0,at=None,known_at=None):
        view=self.capture_view(at,known_at)
        candidates=[];understandings=[]
        if kind in ('all','candidate'):
            rows=self._run('MATCH (r:Review {namespace:$ns}) WHERE r.created_at<=datetime($known) '
                'AND (r.resolved_at IS NULL OR r.resolved_at>datetime($known)) '
                'RETURN properties(r) AS r ORDER BY r.created_at,r.id LIMIT $limit',
                ns=self.namespace,known=stamp(view.known_at),limit=offset+limit+1)
            candidates=[{**_native(row['r']),'body':json.loads(row['r']['body']),
                         'review_kind':'candidate','resolution':'memory_resolve_review'} for row in rows]
        if kind in ('all','understanding'):
            understandings=self.pending_understandings(view,offset+limit+1)
        items=sorted(candidates+understandings,key=lambda r:(r.get('created_at',r.get('recorded_at')),r['id']))
        more=len(items)>offset+limit
        return {'items':items[offset:offset+limit],'truncated':more,'next_offset':offset+limit if more else None,
                'at':stamp(view.at),'known_at':stamp(view.known_at)}

    def recent_sources(self,conversation_id,before_source,limit=6,*,known_at=None):
        rows=self._run('MATCH (s:EvidenceSource {namespace:$ns,conversation_id:$conversation}) WHERE s.id<>$before '
                       'AND s.recorded_at<=datetime($known) AND ('
                       'NOT EXISTS { MATCH (r:MemoryRecord)-[:EVIDENCED_BY]->(s) '
                       'WHERE r.recorded_at<=datetime($known) AND coalesce(r.withdrawn,false) } '
                       'OR EXISTS { MATCH (r:MemoryRecord)-[:EVIDENCED_BY]->(s) '
                       'WHERE r.recorded_at<=datetime($known) AND NOT coalesce(r.withdrawn,false) }) '
                       'RETURN s.body AS body ORDER BY s.recorded_at DESC LIMIT $limit',
                       ns=self.namespace,conversation=conversation_id,before=before_source,limit=limit,
                       known=stamp(known_at or self.clock()))
        return list(reversed([json.loads(row['body']) for row in rows]))

    def latest_record_ids(self,keys,view):
        rows=self._rows(view.known_at,logical_ids=keys,revision=view.revision,include_withdrawn=True)
        latest={}
        for row in rows:
            key=state_key(row['subject'],row['key']) if row['record_kind']=='state' else row['logical_id']
            latest[key]=row['event_id']
        return [{'id':latest[key],'key':key} for key in dict.fromkeys(keys) if key in latest]

    def recent_memory_ids(self,source_ids,view,limit=20):
        if not source_ids:
            return []
        rows=self._run('UNWIND $uids AS uid MATCH (s:EvidenceSource {uid:uid})<-[:EVIDENCED_BY]-(r:MemoryRecord) '
            'WHERE r.seq<=$revision AND r.recorded_at<=datetime($known) '
            'WITH r.logical_id AS key,r.record_kind AS kind,r.subject AS subject,r.key AS state_key,max(r.seq) AS latest '
            'RETURN key,kind,subject,state_key ORDER BY latest DESC,key LIMIT $limit',
            uids=[self._uid(i) for i in source_ids],revision=view.revision,known=stamp(view.known_at),limit=limit)
        keys=[state_key(r['subject'],r['state_key']) if r['kind']=='state' else r['key'] for r in rows]
        # The most recent write can come from a different conversation/source.
        return [r['id'] for r in self.latest_record_ids(keys,view)]

    def _vector_name(self,model):
        digest=hashlib.sha256((self.namespace+'\0'+model).encode()).hexdigest()[:24]
        return 'chronicle_vec_'+digest,'ChronicleVector_'+digest

    def ensure_vector_index(self,model,dimensions):
        name,label=self._vector_name(model)
        existing=self._run('MATCH (n:EmbeddingIndex {uid:$uid}) RETURN n.dimensions AS dimensions',uid=self._uid('vector:'+model))
        if existing and existing[0]['dimensions']!=dimensions:
            raise ValueError('向量维度与已保存索引不一致；更换模型后请使用新模型名重建索引')
        self._run(f'CREATE VECTOR INDEX `{name}` IF NOT EXISTS FOR (e:`{label}`) ON e.values '
                  'OPTIONS {indexConfig: {`vector.dimensions`:$dimensions,`vector.similarity_function`:"cosine"}}',dimensions=dimensions)
        self._run('MERGE (n:EmbeddingIndex {uid:$uid}) SET n.namespace=$ns,n.model=$model,n.name=$name,n.dimensions=$dimensions',
                  uid=self._uid('vector:'+model),ns=self.namespace,model=model,name=name,dimensions=dimensions)
        self._run(f'MATCH (e:Embedding {{namespace:$ns,model:$model}}) SET e:`{label}`',ns=self.namespace,model=model)
        self._run('CALL db.awaitIndex($name,30)',name=name)

    def prepare_existing_vectors(self,model):
        row=self._run('MATCH (e:Embedding {namespace:$ns,model:$model}) RETURN size(e.values) AS dimensions LIMIT 1',ns=self.namespace,model=model)
        if row:
            self.ensure_vector_index(model,row[0]['dimensions'])

    def save_embedding(self,event_id,model,vector):
        _,label=self._vector_name(model)
        def write(tx):
            tx.run(f'MATCH (r:MemoryRecord {{uid:$rid}}) MERGE (e:Embedding:`{label}` {{uid:$uid}}) '
                   'SET e.namespace=$ns,e.model=$model,e.values=$values,e.event_id=$id MERGE (r)-[:HAS_EMBEDDING]->(e)',
                   rid=self._uid(event_id),uid=self._uid('embedding:'+event_id+':'+model),ns=self.namespace,model=model,values=vector,id=event_id).consume()
        self._write(write)

    def vector_matches(self,vector,model,view,limit=100,*,record_ids=None,subject=None,filters=None):
        name,_=self._vector_name(model)
        found=self._run('MATCH (n:EmbeddingIndex {uid:$uid}) RETURN n.dimensions AS dimensions',uid=self._uid('vector:'+model))
        if not found:
            return [],'向量索引尚未建立；可运行 chronicle reindex'
        if found[0]['dimensions']!=len(vector):
            raise ValueError('查询向量维度与索引不一致')
        fetch=max(128,limit*4);maximum=max(fetch,4096)
        while True:
            rows=self._run('CALL db.index.vector.queryNodes($index,$fetch,$vector) YIELD node,score '
                'MATCH (node)<-[:HAS_EMBEDDING]-(r:MemoryRecord) WHERE r.namespace=$ns '
                'RETURN r.event_id AS id,r.logical_id AS key,r.record_kind AS kind,r.subject AS subject,r.key AS state_key,score, '
                '(r.seq<=$revision AND r.recorded_at<=datetime($known) AND score>0.5 '
                'AND ($ids IS NULL OR r.event_id IN $ids) AND NOT coalesce(r.withdrawn,false) AND '+filter_clause('r')+' '
                'AND ($subject IS NULL OR r.subject=$subject OR r.logical_id=$subject OR r.title=$subject OR EXISTS { MATCH (r)-[:INVOLVES]->(e:Entity) WHERE e.id=$subject OR e.name=$subject OR $subject IN e.aliases })) AS eligible '
                'ORDER BY score DESC,id',index=name,fetch=fetch,vector=vector,ns=self.namespace,revision=view.revision,
                known=stamp(view.known_at),ids=record_ids,subject=subject,**filter_params(filters))
            logical={}
            for row in rows:
                if not row.pop('eligible'):
                    continue
                if row['kind']=='state':
                    row['key']=state_key(row['subject'],row['state_key'])
                if row['key'] not in logical:
                    logical[row['key']]={**row,'matched_record_ids':[]}
                if len(logical[row['key']]['matched_record_ids'])<8:
                    logical[row['key']]['matched_record_ids'].append(row['id'])
            exhausted=len(rows)<fetch
            if len(logical)>=limit or exhausted or fetch>=maximum:
                warning=None if exhausted or len(logical)>=limit else '向量候选预算已用完，版本或时间过滤可能导致召回不完整；可使用 history 查询指定对象'
                return list(logical.values())[:limit],warning
            fetch=min(fetch*2,maximum)

    def unembedded(self,model,limit=32):
        rows=self._run('MATCH (r:MemoryRecord {namespace:$ns}) WHERE NOT EXISTS { MATCH (r)-[:HAS_EMBEDDING]->(e:Embedding {model:$model}) } '
                       'RETURN properties(r) AS r ORDER BY r.seq LIMIT $limit',ns=self.namespace,model=model,limit=limit)
        return [self._event(_native(r['r']))|{'record_kind':r['r']['record_kind']} for r in rows]

    def vector_record_matches(self,vector,model,view,record_ids):
        return self._run('UNWIND $uids AS uid MATCH (r:MemoryRecord {uid:uid})-[:HAS_EMBEDDING]->(e:Embedding {model:$model}) '
            'WHERE r.seq<=$revision AND r.recorded_at<=datetime($known) '
            'WITH r,vector.similarity.cosine(e.values,$vector) AS score WHERE score>0.5 '
            'RETURN r.event_id AS id,score ORDER BY score DESC,id',uids=[self._uid(i) for i in record_ids],
            model=model,vector=vector,revision=view.revision,known=stamp(view.known_at))

    def drop_vector_indexes(self):
        # Used by isolated test/demo namespace cleanup, never by normal reads.
        for row in self._run('MATCH (n:EmbeddingIndex {namespace:$ns}) RETURN n.name AS name',ns=self.namespace):
            if not re.fullmatch(r'chronicle_vec_[a-f0-9]{24}',row['name']):
                raise ValueError('索引名称不符合当前命名规则')
            self._run(f"DROP INDEX `{row['name']}` IF EXISTS")

    def counts(self):
        rows=self._run('MATCH (r:MemoryRecord {namespace:$ns}) RETURN r.record_kind AS kind,count(*) AS count',ns=self.namespace)
        return {r['kind']:r['count'] for r in rows}
