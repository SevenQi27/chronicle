"""Deterministic, model-free storage/retrieval baseline; all data is isolated and removed."""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime,timezone
import hashlib
import inspect
import json
import logging
import math
from pathlib import Path
import statistics
import subprocess
import time
from uuid import uuid4

from chronicle_memory.capture import CaptureEvent,archive_turn
from chronicle_memory.config import Settings
from chronicle_memory.models import Entity,Experience,ExperienceTime,Extraction,MemoryChange,Query,Source,Understanding
from chronicle_memory.service import MemoryService
from chronicle_memory.store import MemoryStore

ROOT=Path(__file__).resolve().parents[1]
def day(n):return datetime(2026,11,n,9,tzinfo=timezone.utc)
def size(value):return len(json.dumps(value,ensure_ascii=False,default=str).encode())


async def benchmark(samples,revisions):
    settings=replace(Settings.from_env(ROOT/'.env'),model='',embedding_model='')
    namespace='benchmark-memory-'+uuid4().hex
    clock=[day(1)];store=MemoryStore(settings,namespace,clock=lambda:clock[0])
    service=MemoryService(store,settings)
    original_run,original_write=store._run,store._write
    tracing=[False];metrics={}
    def run(*args,**kwargs):
        rows=original_run(*args,**kwargs)
        if tracing[0]:
            metrics['statements']+=1;metrics['returned_rows']+=len(rows);metrics['decoded_row_json_bytes']+=size(rows)
        return rows
    def write_tx(callback):
        if tracing[0]:metrics['write_transactions']+=1
        return original_write(callback)
    store._run=run;store._write=write_tx
    def write(identifier,**items):
        return store.apply(Source(id=identifier,content='Synthetic benchmark '+identifier,occurred_at=clock[0]),Extraction(**items))
    def episode(identifier,title,text,**kwargs):
        return Experience(id=identifier,title=title,narrative=text,when=ExperienceTime(start=day(1),precision='day'),**kwargs)
    async def measure(operation):
        async def invoke():
            value=operation()
            return await value if hasattr(value,'__await__') else value
        for _ in range(2):await invoke()
        elapsed=[]
        for _ in range(samples):
            start=time.perf_counter();result=await invoke();elapsed.append((time.perf_counter()-start)*1000)
        metrics.clear();metrics.update(statements=0,returned_rows=0,decoded_row_json_bytes=0,write_transactions=0)
        tracing[0]=True
        try:result=await invoke()
        finally:tracing[0]=False
        return {'median_ms':round(statistics.median(elapsed),3),'p95_ms':round(sorted(elapsed)[math.ceil(.95*len(elapsed))-1],3),
                'samples':samples,'response_json_bytes':size(result),**metrics}
    report={'schema':1,'measured_at':datetime.now(timezone.utc).isoformat(),
            'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'code_sha256':hashlib.sha256(b''.join((Path(inspect.getfile(MemoryStore)).parent/f).read_bytes() for f in ('store.py','service.py','capture.py','models.py'))).hexdigest(),
            'model_calls':0,'dataset':{'noise_memories':160,'historical_revisions':revisions,'capture_messages':16,'comparison_capture_messages':2},
            'notes':'Local warm samples, two warmups. JSON sizes are decoded payload estimates, not Bolt wire bytes. No external models. Not a production load test.'}
    try:
        alpha=Entity(id='project-alpha',name='海风项目',aliases=['Alpha']);beta=Entity(id='project-beta',name='山谷项目')
        first=write('initial',experiences=[
            episode('alpha-migration','海风数据库迁移','海风项目的数据库迁移已完成验证，仍未上线。',entities=[alpha]),
            episode('beta-migration','山谷数据库迁移','山谷项目的数据库迁移计划下周启动。',entities=[beta]),
            episode('response-style','回答习惯','回答请保持简短，先说结论。'),
            episode('deployment','部署地点','部署地点在北京。',entities=[alpha])],
            understandings=[Understanding(id='caution',title='部署认识',statement='先验证再上线。',basis='inferred',supports=['alpha-migration'],valid_from=day(1))])
        old_deployment=first['record_ids'][3]
        clock[0]=day(5)
        deployment=write('correct-location',experiences=[episode('deployment','部署地点','部署地点已更正为青岛。',entities=[alpha],replaces=old_deployment,revision_kind='correct')])['record_ids'][0]
        for i in range(8):
            write(f'noise-{i}',experiences=[episode(f'noise-{i}-{j}','日常工作记录',f'今天讨论了一下例行工作，编号 {i}-{j}。') for j in range(20)])
        clock[0]=day(20)
        archive_turn(store,'capture-turn:comparison',[
            CaptureEvent(origin='benchmark',turn_id='comparison',workspace='/synthetic/alpha',
                source=Source(id='comparison-user',conversation_id='comparison',role='user',
                    content='海风项目的数据库迁移目前怎样？',occurred_at=day(20))),
            CaptureEvent(origin='benchmark',turn_id='comparison',workspace='/synthetic/alpha',
                source=Source(id='comparison-assistant',conversation_id='comparison',role='assistant',
                    content='我猜测海风数据库迁移可能尚未完成验证，但没有核实。',occurred_at=day(20))),
        ])
        cases=[
            ('exact','海风数据库迁移',{'alpha-migration'},{}),
            ('entity_alias','Alpha',{'alpha-migration','deployment'},{'subject':'project-alpha'}),
            ('lexical_rephrase','海风项目迁移是否上线',{'alpha-migration'},{}),
            ('explicit_entity_scope','数据库迁移',{'alpha-migration'},{'subject':'project-alpha'}),
            ('corrected_current','青岛',{'deployment'},{}),
            ('historical_clue','北京',{'deployment'},{}),
            ('known_then','北京',{'deployment'},{'at':day(2),'known_at':day(2)}),
            ('unrelated','南极企鹅迁徙',set(),{}),
            ('semantic_only','答复不要啰嗦',{'response-style'},{}),
            ('mixed_archive_and_structured','海风数据库迁移',{'alpha-migration'},{}),
        ]
        quality=[]
        for name,query,expected,options in cases:
            result=await service.search(Query(query=query,limit=3,**options))
            ids=[r.get('id') for r in result['results']]
            hit=bool(expected.intersection(ids)) if expected else not ids
            row={'case':name,'expected':sorted(expected),'returned':ids,'hit_at_3':hit}
            if name=='corrected_current':row['correct_version']=result['results'][0]['record_id']==deployment if ids else False
            if name=='historical_clue':row['historical_marked']=result['results'][0]['match_scope']=='historical' if ids else False
            if name=='known_then':row['correct_version']=result['results'][0]['record_id']==old_deployment if ids else False
            if name=='mixed_archive_and_structured':row['structured_first']=bool(ids) and ids[0]=='alpha-migration'
            if name=='explicit_entity_scope':row['cross_project_results']=sum(identifier=='beta-migration' for identifier in ids)
            quality.append(row)
        report['quality']=quality
        report['search']=await measure(lambda:service.search(Query(query='海风数据库迁移',limit=3)))
        initial_source=Source(id='hot-initial',content='benchmark body',occurred_at=day(20))
        hot=episode('hot-memory','版本增长测试','固定检索锚点。'+'背景叙述。'*500)
        initial_payload=Extraction(experiences=[hot])
        head=store.apply(initial_source,initial_payload)['record_ids'][0]
        report['head_read_one_version']=await measure(lambda:store.records(logical_ids=['hot-memory']))
        for i in range(revisions-1):
            head=write(f'hot-{i}',experiences=[hot.model_copy(update={'replaces':head,'narrative':hot.narrative+f' 补充 {i}'})])['record_ids'][0]
        report['head_read_many_versions']=await measure(lambda:store.records(logical_ids=['hot-memory']))
        report['search_many_versions']=await measure(lambda:service.search(Query(query='固定检索锚点',limit=1)))
        report['idempotent_replay']=await measure(lambda:store.apply(initial_source,initial_payload))
        report['replay_added_versions']=store.counts()['experience']-(4+160+revisions+2)
        events=[];turn_key='capture-turn:benchmark'
        for i in range(16):
            events.append(CaptureEvent(origin='benchmark',turn_id='dialogue',workspace='/synthetic/project',
                source=Source(id=f'message-{i}',conversation_id='dialogue',role='user' if i%2==0 else 'assistant',
                    content=f'合成消息 {i}。'+'这是测试用的背景信息。'*60,occurred_at=day(20))))
            capture_head=archive_turn(store,turn_key,events)
        report['capture_replay']=await measure(lambda:archive_turn(store,turn_key,events))
        report['capture_full_details']=await measure(lambda:store.details([capture_head]))
        detail=store.details([capture_head])[0]
        report['capture_source_count']=len(detail['sources'])
        report['capture_source_bytes']=sum(len(source['content'].encode()) for source in detail['sources'])
        try:
            report['capture_summary_details']=await measure(lambda:store.details([capture_head],source_mode='summary'))
        except TypeError:report['capture_summary_details']={'available':False}
        report['counts']=store.counts()
    finally:
        store._run=original_run;store._write=original_write
        store._run('MATCH (n {namespace:$ns}) DETACH DELETE n',ns=namespace)
        store._run('MATCH (n:MemoryNamespace {name:$ns}) DELETE n',ns=namespace)
        report['cleaned']=store._run('MATCH (n {namespace:$ns}) RETURN count(n) AS n',ns=namespace)[0]['n']==0
        await service.close()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=20)
    parser.add_argument('--revisions',type=int,default=128)
    args=parser.parse_args()
    if args.samples<2 or args.revisions<2:parser.error('samples and revisions must be >= 2')
    logging.getLogger('neo4j.notifications').setLevel(logging.ERROR)
    result=asyncio.run(benchmark(args.samples,args.revisions))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
