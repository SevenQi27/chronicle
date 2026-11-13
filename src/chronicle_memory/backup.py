"""Portable, checksummed namespace snapshots; restore never merges live data."""
import hashlib
import json
import os
from pathlib import Path
import re
from datetime import timezone

from neo4j.time import DateTime

from .errors import ConflictError
from .models import Model, Text, stamp
from pydantic import Field


LABELS={'MemoryRecord','MemoryEvent','Experience','Understanding','EvidenceSource','Memory',
        'Entity','DueCheck','Review','Embedding','EmbeddingIndex','MemoryVisibility','ForgetMarker'}
RELATIONS={'HAS_REVISION','EVIDENCED_BY','INVOLVES','SUPPORTED_BY','REVISES','CORRECTS','CHECKS',
           'HAS_EMBEDDING','RELATED_TO','CONTINUES','CONTRADICTS','CAUSED_BY'}


class Node(Model):
    labels: list[Text] = Field(min_length=1)
    properties: dict


class Link(Model):
    start: Text
    end: Text
    kind: Text
    properties: dict


class Snapshot(Model):
    namespace: Text
    sequence: int = Field(ge=0)
    exported_at: Text
    nodes: list[Node]
    relationships: list[Link]


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def checksum(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def encode(properties):
    return {key:({'$datetime':value.iso_format()} if isinstance(value,DateTime) else value)
            for key,value in properties.items()}


def decode(properties):
    result={}
    for key,value in properties.items():
        if isinstance(value,dict):
            if set(value)!={'$datetime'} or not isinstance(value['$datetime'],str):
                raise ValueError('备份包含不支持的属性类型')
            value=DateTime.from_iso_format(value['$datetime'])
            if value.tzinfo is None:raise ValueError('备份时间缺少时区')
            # Chronicle writes fixed-offset UTC. The driver's ISO parser uses
            # named UTC; Neo4j orders that differently at an equal cutoff.
            value=value.replace(tzinfo=timezone(value.utcoffset()))
        elif isinstance(value,list):
            if any(not isinstance(item,(str,int,float,bool)) for item in value):
                raise ValueError('备份数组属性类型不合法')
        elif not isinstance(value,(str,int,float,bool)):
            raise ValueError('备份属性类型不合法')
        result[key]=value
    return result


def read_snapshot(path):
    envelope=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(envelope,dict) or envelope.get('format')!='chronicle-namespace-v1':
        raise ValueError('不支持的备份格式')
    payload=envelope.get('data')
    digest=checksum(payload)
    if digest!=envelope.get('sha256'):raise ValueError('备份完整性校验失败')
    data=Snapshot.model_validate(payload)
    identifiers=set();prefix=data.namespace+'::'
    for node in data.nodes:
        p=node.properties;uid=p.get('uid')
        if not isinstance(uid,str) or not uid.startswith(prefix) or uid in identifiers or p.get('namespace')!=data.namespace:
            raise ValueError('备份节点 ID 重复或不属于来源分区')
        if not set(node.labels)&LABELS or any(label not in LABELS and not re.fullmatch(r'ChronicleVector_[a-f0-9]{24}',label) for label in node.labels):
            raise ValueError('备份包含未知节点标签')
        if 'Embedding' not in node.labels and any(label.startswith('ChronicleVector_') for label in node.labels):
            raise ValueError('向量标签只能用于 Embedding')
        decode(p);identifiers.add(uid)
        if 'MemoryRecord' in node.labels and (not isinstance(p.get('seq'),int) or p['seq']>data.sequence):
            raise ValueError('备份版本序号不一致')
    for link in data.relationships:
        if link.kind not in RELATIONS or link.start not in identifiers or link.end not in identifiers:
            raise ValueError('备份包含未知关系或悬空端点')
        if link.properties.get('namespace',data.namespace)!=data.namespace:
            raise ValueError('备份关系分区不一致')
        decode(link.properties)
    return data,digest


def backup(store,action,path,confirmation=None):
    path=Path(path).expanduser().resolve()
    if action=='export':
        def collect(tx):
            nodes=[{'labels':sorted(r['labels']),'properties':encode(dict(r['props']))} for r in tx.run(
                'MATCH (n {namespace:$ns}) RETURN labels(n) AS labels,properties(n) AS props ORDER BY n.uid',ns=store.namespace)]
            links=[{'start':r['start'],'end':r['end'],'kind':r['kind'],'properties':encode(dict(r['props']))} for r in tx.run(
                'MATCH (a {namespace:$ns})-[r]->(b {namespace:$ns}) '
                'RETURN a.uid AS start,b.uid AS end,type(r) AS kind,properties(r) AS props '
                'ORDER BY start,end,kind,r.id',ns=store.namespace)]
            sequence=tx.run('MATCH (n:MemoryNamespace {name:$ns}) RETURN n.sequence AS seq',ns=store.namespace).single()['seq']
            return {'namespace':store.namespace,'sequence':sequence,'exported_at':stamp(store.clock()),'nodes':nodes,'relationships':links}
        data=store._write(collect)
        digest=checksum(data)
        # Exclusive creation prevents both overwrite and following a destination symlink.
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as output:
                output.write(canonical({'format':'chronicle-namespace-v1','sha256':digest,'data':data})+'\n')
                output.flush();os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {'action':'exported','path':str(path),'sha256':digest,'namespace':store.namespace,
                'nodes':len(data['nodes']),'relationships':len(data['relationships'])}
    if action not in ('validate','restore'):raise ValueError('未知备份操作')
    data,digest=read_snapshot(path)
    token=checksum({'sha256':digest,'target_namespace':store.namespace})
    summary={'path':str(path),'sha256':digest,'source_namespace':data.namespace,'target_namespace':store.namespace,
             'nodes':len(data.nodes),'relationships':len(data.relationships),'confirmation':token}
    if action=='validate':
        occupied=bool(store._run('MATCH (n {namespace:$ns}) RETURN n.uid AS uid LIMIT 1',ns=store.namespace))
        return {'action':'validated',**summary,'target_empty':not occupied}
    if confirmation!=token:raise ValueError('请先 validate 并提交其 confirmation token')
    def restore(tx):
        if tx.run('MATCH (n {namespace:$ns}) RETURN n.uid AS uid LIMIT 1',ns=store.namespace).single():
            raise ConflictError('version_conflict','恢复目标分区必须为空；请使用新的 namespace')
        mapping={n.properties['uid']:store._uid(n.properties['uid'][len(data.namespace)+2:]) for n in data.nodes}
        for node in data.nodes:
            props=decode(node.properties);props['uid']=mapping[props['uid']];props['namespace']=store.namespace
            labels=[label for label in node.labels if not label.startswith('ChronicleVector_')]
            if 'Embedding' in labels:labels.append(store._vector_name(props['model'])[1])
            if 'EmbeddingIndex' in labels:props['name']=store._vector_name(props['model'])[0]
            tx.run('CREATE (n:'+':'.join('`'+label+'`' for label in labels)+') SET n=$props',props=props).consume()
        for link in data.relationships:
            props=decode(link.properties)
            if 'namespace' in props:props['namespace']=store.namespace
            tx.run('MATCH (a {uid:$start}),(b {uid:$end}) CREATE (a)-[r:'+link.kind+']->(b) SET r=$props',
                   start=mapping[link.start],end=mapping[link.end],props=props).consume()
        tx.run('MATCH (n:MemoryNamespace {name:$ns}) SET n.sequence=$seq',ns=store.namespace,seq=data.sequence).consume()
        actual=tx.run('MATCH (n {namespace:$ns}) RETURN count(n) AS count',ns=store.namespace).single()['count']
        relations=tx.run('MATCH (a {namespace:$ns})-[r]->(b {namespace:$ns}) RETURN count(r) AS count',ns=store.namespace).single()['count']
        if actual!=len(data.nodes) or relations!=len(data.relationships):raise ValueError('恢复数量核对失败，已回滚')
    store._write(restore)
    # Index DDL cannot share the data transaction. Raw vectors are already restored;
    # expose any unfinished index work without misreporting a rolled-back restore.
    pending=[]
    for node in data.nodes:
        if 'EmbeddingIndex' in node.labels:
            try:store.ensure_vector_index(node.properties['model'],node.properties['dimensions'])
            except Exception:pending.append(node.properties['model'])
    return {'action':'restored',**summary,'verified_counts':True,'vector_indexes_pending':pending}
