"""Explicit memory lifecycle operations, shared by every client."""
import hashlib
import json

from .models import stamp,state_key
from .errors import ConflictError,ForgottenError


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


def marker_uid(store,kind,identifier):
    return store._uid('forgotten:'+kind+':'+fingerprint(identifier))


def logical_key(row):
    return state_key(row['subject'],row['key']) if row['record_kind']=='state' else row['logical_id']


def select_records(store,identifiers,run):
    pairs=[]
    for identifier in identifiers:
        if identifier.startswith('state:v2:'):
            try:pair=json.loads(identifier[9:])
            except ValueError:continue
            if isinstance(pair,list) and len(pair)==2:pairs.append({'subject':pair[0],'key':pair[1]})
    return [dict(r['r']) for r in run('MATCH (r:MemoryRecord {namespace:$ns}) '
        'WHERE r.logical_id IN $ids OR r.event_id IN $ids OR '
        '(r.record_kind="state" AND any(p IN $pairs WHERE r.subject=p.subject AND r.key=p.key)) '
        'RETURN properties(r) AS r',ns=store.namespace,ids=identifiers,pairs=pairs)]


def is_forgotten(store,kind,identifier):
    return bool(store._run('MATCH (f:ForgetMarker {uid:$uid}) RETURN f.uid AS uid',uid=marker_uid(store,kind,identifier)))


def ensure_writable(store,source,extraction,run):
    keys=[m.id for m in extraction.experiences+extraction.understandings]
    keys += [state_key(m.subject,m.key) for m in extraction.memories]
    uids=[marker_uid(store,'source',source.id)]+[marker_uid(store,'memory',key) for key in keys]
    uids += [marker_uid(store,'association',a.id) for a in extraction.associations]
    if list(run('UNWIND $uids AS uid MATCH (f:ForgetMarker {uid:uid}) RETURN f.uid AS uid LIMIT 1',uids=uids)):
        raise ForgottenError('指定来源或记忆已被永久遗忘，不能通过重放重新创建')


def visibility(store,memory_ids,action,reason=''):
    identifiers=memory_ids
    if action not in ('withdraw','restore'):raise ValueError('未知可见性操作')
    def write(tx):
        run=lambda q,**p:list(tx.run(q,**p))
        records=select_records(store,identifiers,run)
        if not records:raise ValueError('未找到当前分区的目标记忆')
        keys=sorted({logical_key(r) for r in records})
        # Resolve all revisions, even when the caller supplied one record ID.
        records=select_records(store,keys,run)
        withdrawn=action=='withdraw'
        for key in keys:
            tx.run('MERGE (v:MemoryVisibility {uid:$uid}) SET v.namespace=$ns,v.memory_id=$key,'
                'v.withdrawn=$value,v.reason=$reason,v.changed_at=datetime($now)',
                uid=store._uid('visibility:'+fingerprint(key)),ns=store.namespace,key=key,
                value=withdrawn,reason=reason,now=stamp(store.clock())).consume()
        tx.run('UNWIND $uids AS uid MATCH (r:MemoryRecord {uid:uid}) SET r.withdrawn=$value',
               uids=[r['uid'] for r in records],value=withdrawn).consume()
        store._seq(tx)
        return {'action':action,'memory_ids':keys,'revisions':len(records),'withdrawn':withdrawn}
    return store._write(write)


def _forget_plan(store,identifiers,run):
    records=select_records(store,identifiers,run)
    if not records:raise ValueError('未找到当前分区的目标记忆')
    requested=sorted({logical_key(r) for r in records})
    keys=set(requested);source_ids=set();association_ids=set()
    while True:
        records=select_records(store,sorted(keys),run)
        record_uids=[r['uid'] for r in records]
        source_ids.update(r['id'] for r in run('UNWIND $uids AS uid MATCH (r:MemoryRecord {uid:uid})-[:EVIDENCED_BY]->(s:EvidenceSource) '
            'RETURN DISTINCT s.id AS id',uids=record_uids))
        references=['source:'+i for i in source_ids]+['record:'+r['event_id'] for r in records]
        associations=list(run('MATCH (a:Memory {namespace:$ns})-[r]->(b:Memory {namespace:$ns}) '
            'WHERE r.category="association" AND (a.id IN $keys OR b.id IN $keys OR r.source_id IN $sources '
            'OR r.logical_id IN $associations OR any(e IN r.evidence_ids WHERE e IN $references)) '
            'RETURN DISTINCT r.id AS id,r.logical_id AS logical_id,r.source_id AS source_id',
            ns=store.namespace,keys=sorted(keys),sources=sorted(source_ids),associations=sorted(association_ids),references=references))
        expanded_sources=source_ids|{r['source_id'] for r in associations}
        expanded_associations=association_ids|{r['logical_id'] for r in associations}
        related=run('MATCH (r:MemoryRecord {namespace:$ns}) WHERE '
            'EXISTS { MATCH (r)-[:EVIDENCED_BY]->(s) WHERE s.id IN $sources } OR '
            'EXISTS { MATCH (r)-[:SUPPORTED_BY]->(e) WHERE e.uid IN $records } '
            'RETURN properties(r) AS r',ns=store.namespace,sources=sorted(expanded_sources),records=record_uids)
        expanded=keys|{logical_key(r['r']) for r in related}
        if expanded==keys and expanded_sources==source_ids and expanded_associations==association_ids:break
        keys=expanded;source_ids=expanded_sources;association_ids=expanded_associations
    # Include shared immutable receipts too: their extraction JSON retains the
    # original request. No hidden partial rewrites of that evidence are possible.
    sources=list(run('MATCH (s:EvidenceSource {namespace:$ns}) WHERE s.id IN $ids RETURN s.id AS id,s.uid AS uid',
                     ns=store.namespace,ids=sorted(source_ids)))
    affected=sorted(keys)
    token=fingerprint(json.dumps({'namespace':store.namespace,'memory_ids':affected,
        'record_ids':sorted(r['event_id'] for r in records),'source_ids':sorted(source_ids),
        'association_versions':sorted(r['id'] for r in associations)},sort_keys=True))
    return {'requested_memory_ids':requested,'affected_memory_ids':affected,'additional_memory_ids':sorted(keys-set(requested)),
            'affected_association_ids':sorted(association_ids),'association_version_count':len(associations),
            'revision_count':len(records),'source_count':len(sources),'confirmation':token},records,sources



def forget(store,memory_ids,confirmation=None):
    from .store import RELATIONS
    identifiers=memory_ids
    if confirmation is None:
        plan,_,_=_forget_plan(store,identifiers,store._run)
        return {'action':'preview','namespace':store.namespace,**plan,
                'notice':'将删除这些记忆的全部版本、共享来源关联的记忆及依赖认识。请用户确认影响范围；旧备份和宿主聊天不在删除范围内。'}
    def write(tx):
        plan,records,sources=_forget_plan(store,identifiers,lambda q,**p:list(tx.run(q,**p)))
        if confirmation!=plan['confirmation']:
            raise ConflictError('version_conflict','遗忘范围已变化或确认 token 不匹配，请重新预览')
        seq=store._seq(tx);now=stamp(store.clock())
        markers=[{'kind':'memory','id':key} for key in plan['affected_memory_ids']]+[{'kind':'source','id':s['id']} for s in sources]
        markers += [{'kind':'association','id':key} for key in plan['affected_association_ids']]
        for marker in markers:
            tx.run('MERGE (f:ForgetMarker {uid:$uid}) ON CREATE SET f.namespace=$ns,f.kind=$kind,'
                'f.fingerprint=$hash,f.seq=$seq,f.deleted_at=datetime($now)',
                uid=marker_uid(store,marker['kind'],marker['id']),ns=store.namespace,kind=marker['kind'],
                hash=fingerprint(marker['id']),seq=seq,now=now).consume()
        for kind in RELATIONS.values():
            tx.run(f'UNWIND $ids AS id MATCH ()-[r:{kind}]->() '
                   'WHERE r.namespace=$ns AND r.logical_id=id AND r.category="association" DELETE r',
                   ns=store.namespace,ids=plan['affected_association_ids']).consume()
        uids=[r['uid'] for r in records]
        extras=tx.run('UNWIND $uids AS uid MATCH (r:MemoryRecord {uid:uid}) '
            'OPTIONAL MATCH (m:Memory)-[:HAS_REVISION]->(r) OPTIONAL MATCH (r)-[:INVOLVES]->(e:Entity) '
            'RETURN collect(DISTINCT m.uid) AS memories,collect(DISTINCT e.uid) AS entities',uids=uids).single()
        tx.run('UNWIND $uids AS uid MATCH (r:MemoryRecord {uid:uid}) OPTIONAL MATCH (r)-[:HAS_EMBEDDING]->(e:Embedding) '
               'OPTIONAL MATCH (c:DueCheck)-[:CHECKS]->(r) DETACH DELETE e,c,r',uids=uids).consume()
        tx.run('MATCH (m:Memory) WHERE m.uid IN $uids AND NOT EXISTS { MATCH (m)-[:HAS_REVISION]->() } DETACH DELETE m',uids=extras['memories']).consume()
        tx.run('MATCH (e:Entity) WHERE e.uid IN $uids AND NOT EXISTS { MATCH ()-[:INVOLVES]->(e) } DETACH DELETE e',uids=extras['entities']).consume()
        tx.run('MATCH (r:Review)-[:EVIDENCED_BY]->(s:EvidenceSource) WHERE s.uid IN $uids DETACH DELETE r',uids=[s['uid'] for s in sources]).consume()
        tx.run('MATCH (s:EvidenceSource) WHERE s.uid IN $uids DETACH DELETE s',uids=[s['uid'] for s in sources]).consume()
        tx.run('MATCH (v:MemoryVisibility) WHERE v.uid IN $uids DELETE v',
               uids=[store._uid('visibility:'+fingerprint(k)) for k in plan['affected_memory_ids']]).consume()
        return {'action':'forgotten',**plan,'capture_cleanup':'queued_for_capture_workers',
                'notice':'仅删除当前数据库中的相关数据；已导出的备份和宿主聊天仍需由用户另行管理。'}
    return store._write(write)
