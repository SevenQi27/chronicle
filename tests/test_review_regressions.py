"""Cross-feature regressions from the versioning and retrieval review."""
import json
from datetime import timedelta
from pathlib import Path
import sys

import pytest
from neo4j.exceptions import ServiceUnavailable

from conftest import day
from chronicle_memory.config import Settings
from chronicle_memory.errors import ConflictError
from chronicle_memory.models import (Association,Entity,Experience,ExperienceTime,Extraction,
    MemoryChange,Query,ReviewCandidate,Source,Understanding,state_key)
from chronicle_memory.service import MemoryService
from chronicle_memory.store import ASSOCIATION_LOOKUP
from chronicle_memory.tools import MemoryTools


def write(s,identifier,*,conversation='test',role='user',**items):
    return s.store.apply(Source(id=identifier,content=identifier,role=role,occurred_at=s.now,
                               conversation_id=conversation),Extraction(**items))


def episode(identifier,text='camping',**kwargs):
    return Experience(id=identifier,title=text,narrative=text,
                      when=ExperienceTime(start=day(1),precision='exact'),**kwargs)


@pytest.mark.parametrize('revision_kind',['update','correct'])
async def test_revised_experience_keeps_bidirectional_evidence_and_review(scenario,revision_kind):
    s=scenario
    first=write(s,'first',experiences=[episode('trip',entities=[Entity(id='lin',name='小林')])],
        understandings=[Understanding(id='belief',title='理解',statement='一次感受',basis='inferred',
                                     supports=['trip'],valid_from=day(1))])['record_ids']
    s.now=day(5)
    current=write(s,'revision',experiences=[episode('trip','freshcamping',replaces=first[0],revision_kind=revision_kind,
                                                 entities=[Entity(id='lin',name='小林')])])['record_ids'][0]
    forward=s.store.neighbors(current)['neighbors']
    reverse=s.store.neighbors(first[1])['neighbors']
    assert forward[0]['record']['record_id']==first[1]
    assert forward[0]['record']['status']=='needs_review'
    assert forward[0]['reasons'][0]['via']['cited_record_ids']==[first[0]]
    assert forward[0]['reasons'][0]['via']['evidence_status']=='changed'
    assert reverse[0]['record']['record_id']==current
    assert s.store.details([first[1]])[0]['status']=='needs_review'
    assert s.store.details([first[1]])[0]['evidence_status']=='changed'
    old=s.store.details([first[1]],at=day(2),known_at=day(2))[0]
    assert old['status']=='active' and old['evidence_status']=='current'
    reviews=s.store.reviews(kind='understanding')['items']
    assert [r['record_id'] for r in reviews]==[first[1]]
    assert reviews[0]['resolution']=='memory_write'
    assert s.store.reviews(kind='understanding',known_at=day(2))['items']==[]
    assert first[1] in {r['record_id'] for r in s.store.history(entity_id='lin')['records']}
    service=MemoryService(s.store,Settings())
    try:
        context=await service.context(Query(query='freshcamping'))
        assert any(r['record_id']==first[1] for r in context['related_memories'])
        assert any(r.get('record_id')==first[1] for r in context['attention'])
    finally:await service.provider.close()
    # Re-evaluating the understanding with current evidence clears the queue.
    s.now=day(6)
    write(s,'reconsider',understandings=[Understanding(id='belief',title='理解',statement='重新核实',basis='inferred',
              supports=[current],valid_from=day(1),replaces=first[1])])
    assert s.store.reviews(kind='understanding')['items']==[]
    replacement=s.store.records(logical_ids=['belief'])[0]['record_id']
    write(s,'different-support',experiences=[episode('unrelated')],understandings=[Understanding(
        id='belief',title='新的依据',statement='不再引用露营经历',basis='inferred',supports=['unrelated'],replaces=replacement)])
    assert s.store.neighbors(current)['neighbors']==[]


async def test_old_keyword_recalls_current_episode_with_historical_match(scenario):
    s=scenario
    old=write(s,'old',experiences=[episode('color','saffron')])['record_ids'][0]
    s.now=day(5)
    new=write(s,'new',experiences=[episode('color','cobalt',replaces=old,revision_kind='correct')])['record_ids'][0]
    service=MemoryService(s.store,Settings())
    try:
        result=await service.search(Query(query='saffron'))
        assert result['candidate_count']==1
        assert result['results'][0]['record_id']==new
        assert result['results'][0]['match_scope']=='historical'
        assert result['results'][0]['matched_record_ids']==[old]
        past=await service.search(Query(query='saffron',known_at=day(2),at=day(2)))
        assert past['results'][0]['record_id']==old
        assert past['results'][0]['match_scope']=='current'
        # A superseded exact fact must not be presented as a current fact by an old word.
        s.write('beijing',subject='traveler',key='city',text='beijing')
        s.write('shanghai',subject='traveler',key='city',text='shanghai',effective=5)
        assert (await service.search(Query(query='beijing')))['results']==[]
    finally:await service.provider.close()


class CaptureProvider:
    async def extract(self,source,existing,previous):
        self.existing=existing
        return Extraction()
    async def embed(self,texts):return [[1.,0.] for _ in texts]
    async def close(self):pass


async def test_ingest_merges_common_word_hits_and_latest_cross_conversation_context(scenario):
    s=scenario
    original=write(s,'past',conversation='current',experiences=[episode('episode','玄武岩观察')],
        understandings=[Understanding(id='belief',title='对岩石的认识',statement='最初认识',basis='inferred',supports=['episode'])])['record_ids']
    old=original[1]
    s.now=day(2)
    # The latest understanding's source is outside the active conversation.
    for index in range(22):
        old=write(s,f'other-{index}',conversation='elsewhere',understandings=[Understanding(id='belief',
            title='旧版本关键词',statement='更新认识',basis='inferred',supports=[original[0]],replaces=old)])['record_ids'][0]
    latest=old
    for batch in range(2):
        write(s,f'noise-{batch}',conversation='noise',experiences=[episode(f'noise-{batch}-{i}','我们讨论一下') for i in range(20)])
    provider=CaptureProvider();service=MemoryService(s.store,Settings(model='test'),provider)
    source=Source(id='ingest',content='我们补充一下上次的认识',conversation_id='current',occurred_at=s.now)
    assert s.store.keyword_matches(source.content,s.store.capture_view(),20)
    await service.ingest(source)
    ids=[r['record_id'] for r in provider.existing]
    assert latest in ids and len(ids)<=20
    assert sum(r.get('id')=='belief' for r in provider.existing)==1
    assert next(r for r in provider.existing if r.get('id')=='belief')['latest_record_id']==latest


async def test_ingest_keyword_hits_resolve_latest_writable_future_version(scenario):
    s=scenario
    base=write(s,'base',experiences=[episode('trip','uniqueoldterm')],
        understandings=[Understanding(id='belief',title='uniqueoldterm',statement='现在的认识',basis='inferred',supports=['trip'])])['record_ids']
    future=write(s,'future',conversation='other',understandings=[Understanding(id='belief',title='newterm',
        statement='未来适用的认识',basis='inferred',supports=[base[0]],replaces=base[1],valid_from=day(20))])['record_ids'][0]
    provider=CaptureProvider()
    await MemoryService(s.store,Settings(model='test'),provider).ingest(Source(id='new-input',content='uniqueoldterm',occurred_at=s.now))
    assert any(r['record_id']==future and r['latest_record_id']==future for r in provider.existing)
    assert s.store.details([future])[0]['status']=='scheduled'


async def test_long_chinese_ingest_and_failed_context_retry(scenario,monkeypatch):
    s=scenario;provider=CaptureProvider();service=MemoryService(s.store,Settings(model='test'),provider)
    long_text=' '.join(chr(0x4e00+i)+chr(0x6000+i) for i in range(1500))
    # The last chunk must be searched too, rather than silently truncating input.
    end=long_text.split()[-1]
    write(s,'last-token',experiences=[episode('last-token',end)])
    source=Source(id='long',content=long_text,occurred_at=s.now)
    await service.ingest(source)
    assert s.store.source('long')['status']=='applied'
    assert s.store.source('long')['content']==long_text
    assert any(r.get('id')=='last-token' for r in provider.existing)
    original=s.store.keyword_matches
    def fail(*args,**kwargs):raise ServiceUnavailable('test context failure')
    monkeypatch.setattr(s.store,'keyword_matches',fail)
    failed=Source(id='retry-after-failure',content='retry',occurred_at=s.now)
    with pytest.raises(ServiceUnavailable):await service.ingest(failed)
    assert s.store.source(failed.id)['status']=='failed'
    monkeypatch.setattr(s.store,'keyword_matches',original)
    assert (await service.ingest(failed))['status']=='applied'


def test_history_intervals_point_and_day_boundaries(scenario):
    s=scenario
    s.now=day(10)
    ids=write(s,'dates',experiences=[
        episode('point','point'),
        Experience(id='day',title='day',narrative='a day',when=ExperienceTime(start=day(1),precision='day')),
        Experience(id='range',title='range',narrative='a range',when=ExperienceTime(start=day(1),end=day(8),precision='range'))],
        understandings=[Understanding(id='interval',title='interval',statement='valid for a week',basis='inferred',supports=['point'],valid_from=day(1),valid_to=day(8)),
                        Understanding(id='open',title='open',statement='still valid',basis='inferred',supports=['point'],valid_from=day(1))])['record_ids']
    found={r['id'] for r in s.store.history(start=day(5),end=day(6))['records']}
    assert found=={'range','interval','open'}
    middle=day(1)+timedelta(hours=1)
    found={r['id'] for r in s.store.history(start=middle,end=middle+timedelta(hours=1))['records']}
    assert 'day' in found and 'point' not in found
    assert 'interval' not in {r['id'] for r in s.store.history(start=day(8),end=day(9))['records']}
    s.store._run('MATCH (r:Understanding {namespace:$ns,logical_id:"interval"}) SET r.valid_to=datetime($end)',
                 ns=s.namespace,end=day(8).isoformat())
    assert 'interval' not in {r['id'] for r in s.store.history(start=day(8),end=day(9))['records']}


def test_upcoming_uses_plan_due_time_and_excludes_closed_versions(scenario):
    s=scenario
    first=s.write('上线',subject='project',key='release',kind='plan',due_at=day(8))
    s.now=day(3)
    upcoming=s.store.upcoming({'project'},s.now,s.now)
    assert [r['event_id'] for r in upcoming]==[first]
    s.write('延期',subject='project',key='release',kind='plan',due_at=day(10),effective=3)
    upcoming=s.store.upcoming({'project'},s.now,s.now)
    assert len(upcoming)==1 and upcoming[0]['value']=='延期'
    s.now=day(4)
    s.write('取消',subject='project',key='release',kind='plan',plan_status='cancelled',effective=4)
    assert s.store.upcoming({'project'},s.now,s.now)==[]


def test_history_association_truncation_and_superseded_markers(scenario):
    s=scenario
    write(s,'episodes',experiences=[episode('a'),episode('b'),episode('c')])
    first=write(s,'link',associations=[Association(id='relation',from_id='a',to_id='b',kind='related_to',
                 explanation='first',basis='inferred',evidence_ids=['a'])])['association_ids'][0]
    s.now=day(2)
    newest=write(s,'link-update',associations=[Association(id='relation',from_id='a',to_id='c',kind='continues',
                 explanation='updated',basis='inferred',evidence_ids=['a'],replaces=first)])['association_ids'][0]
    history=s.store.history(memory_id='a',limit=1)
    assert history['truncated'] is True
    assert history['associations'][0]['superseded'] is True
    assert history['associations'][0]['current_version_id']==newest
    old=s.store.history(memory_id='a',known_at=day(1))
    assert old['associations'][0]['superseded'] is False


def test_entity_metadata_requires_explicit_correction(scenario):
    s=scenario
    old=write(s,'name',experiences=[episode('a',entities=[Entity(id='person',name='旧名',aliases=['错误别名'])])])['record_ids'][0]
    write(s,'mention',experiences=[episode('b',entities=[Entity(id='person',name='其他称呼',aliases=['新别名'])])])
    row=s.store._run('MATCH (e:Entity {uid:$uid}) RETURN e.name AS name,e.aliases AS aliases',uid=s.store._uid('entity:person'))[0]
    assert row['name']=='旧名' and set(row['aliases'])=={'错误别名','新别名'}
    with pytest.raises(ValueError,match='完整 aliases'):
        Entity(id='person',name='规范名',operation='correct')
    write(s,'rename',experiences=[episode('c',entities=[Entity(id='person',name='规范名',category='person',aliases=['正确别名'],operation='correct')])])
    assert s.store.entity_candidates('规范名',s.store.capture_view())
    assert s.store.entity_candidates('错误别名',s.store.capture_view())==[]
    assert s.store.details([old])[0]['entities'][0]['name']=='旧名'


def test_state_identity_disambiguates_and_reads_legacy_rows(scenario):
    s=scenario
    left=s.write('left',subject='a:b',key='c')
    right=s.write('right',subject='a',key='b:c')
    assert state_key('a:b','c')!=state_key('a','b:c')
    with pytest.raises(ValueError,match='主体'):
        s.write('invalid',subject='a',key='b:c',operation='correct',corrects=left)
    assert s.store.state('a:b','c')['memory']['value']=='left'
    assert s.store.state('a','b:c')['memory']['value']=='right'
    # Recreate legacy layout with both pairs sharing one Memory node. Records and
    # their source/embedding IDs remain unchanged when the new code reads them.
    s.store._run('MATCH (old:Memory)-[h:HAS_REVISION]->(r:MemoryRecord {namespace:$ns}) '
        'WHERE r.event_id IN $ids MERGE (legacy:Memory {uid:$uid}) '
        'SET legacy.namespace=$ns,legacy.id=$key,legacy.kind="state",r.logical_id=$key '
        'MERGE (legacy)-[:HAS_REVISION]->(r) DELETE h',ns=s.namespace,ids=[left,right],
        uid=s.store._uid('memory:state:a:b:c'),key='state:a:b:c')
    s.now=day(2)
    s.write('corrected-left',subject='a:b',key='c',operation='correct',corrects=left)
    assert s.store.state('a:b','c')['memory']['value']=='corrected-left'
    assert s.store.state('a','b:c')['memory']['value']=='right'
    assert {r['event_id'] for r in s.store.timeline('a:b','c')['events']}!={right}
    assert all(r['subject']=='a:b' and r['key']=='c' for r in s.store.timeline('a:b','c')['events'])
    from chronicle_memory.ui import graph_snapshot
    versions=[n for n in graph_snapshot(s.store)['nodes'] if n.get('payload',{}).get('subject')=='a:b']
    assert len(versions)==2 and {n['logical_id'] for n in versions}=={state_key('a:b','c')}


def test_assistant_cannot_label_an_association_explicit(scenario):
    s=scenario
    write(s,'user-evidence',experiences=[episode('a'),episode('b')])
    with pytest.raises(ValueError,match='助手复述'):
        write(s,'assistant-explicit',role='assistant',associations=[Association(id='link',from_id='a',to_id='b',
             kind='related_to',explanation='助手引用了用户资料',basis='explicit',evidence_ids=['user-evidence'])])
    assert write(s,'assistant-inferred',role='assistant',associations=[Association(id='link',from_id='a',to_id='b',
             kind='related_to',explanation='助手归纳',basis='inferred',evidence_ids=['user-evidence'])])['association_ids']


def test_conflict_check_is_processed_once_and_remains_visible(scenario):
    s=scenario
    s.write('A');s.write('B');s.now=day(2)
    assert all(row['outcome']=='conflict' for row in s.store.tick())
    assert s.store.tick()==[]
    assert s.state()['status']=='conflict'


def test_checks_and_reviews_have_bounded_stable_pages(scenario):
    s=scenario
    for i in range(5):
        s.write(i,key=f'key-{i}')
        write(s,f'review-{i}',review=[ReviewCandidate(text=f'candidate-{i}',reason='uncertain')])
    for method in (s.store.checks,s.store.reviews):
        first=method(limit=2);second=method(limit=2,offset=first['next_offset']);third=method(limit=2,offset=second['next_offset'])
        assert len(first['items'])==len(second['items'])==2 and len(third['items'])==1
        assert first['truncated'] and not third['truncated'] and third['next_offset'] is None
        assert len({r.get('id',r.get('uid')) for page in (first,second,third) for r in page['items']})==5


def test_association_lookup_uses_property_indexes(scenario):
    with scenario.store.driver.session(database=scenario.settings.neo4j_database) as session:
        plan=session.run('EXPLAIN '+ASSOCIATION_LOOKUP,ns=scenario.namespace,id='relationship').consume().plan
    def operators(p):return [p['operatorType']]+[o for child in p.get('children',[]) for o in operators(child)]
    names=operators(plan)
    assert not any('AllRelationshipsScan' in name for name in names)
    assert any('RelationshipIndexSeek' in name for name in names)


async def test_disabled_mcp_tool_has_structured_error(scenario):
    from mcp.client import ClientSession
    from mcp.client.stdio import StdioServerParameters,stdio_client
    params=StdioServerParameters(command=str(Path(sys.executable).with_name('chronicle')),
        args=['mcp','--env-file',str(Path(__file__).resolve().parents[1]/'.env'),'--namespace',scenario.namespace],
        env={'MEMORY_LLM_MODEL':'','MEMORY_EMBEDDING_MODEL':''})
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=15) as client:
            await client.initialize()
            result=await client.call_tool('memory_ingest',{})
            assert result.is_error
            assert result.structured_content['error']['code']=='unknown_tool'
            assert result.structured_content['error']['message']


async def test_vector_versions_share_candidate_budget_and_retain_historical_recall(scenario):
    s=scenario;model='review-vector'
    s.store.ensure_vector_index(model,2)
    initial=write(s,'initial',experiences=[episode('many','original')])['record_ids'][0]
    other=write(s,'other',experiences=[episode('other','other')])['record_ids'][0]
    s.store.save_embedding(initial,model,[1.,0.]);s.store.save_embedding(other,model,[.95,.05])
    s.now=day(2);latest=initial
    for i in range(135):
        latest=write(s,f'version-{i}',experiences=[episode('many',f'past-{i}',replaces=latest)])['record_ids'][0]
        s.store.save_embedding(latest,model,[1.,0.])
    s.now=day(3)
    latest=write(s,'latest',experiences=[episode('many','current',replaces=latest)])['record_ids'][0]
    s.store.save_embedding(latest,model,[0.,1.])
    rows,warning=s.store.vector_matches([1.,0.],model,s.store.capture_view(),limit=2)
    assert {r['key'] for r in rows}=={'many','other'} and warning is None
    historical,_=s.store.vector_matches([1.,0.],model,s.store.capture_view(known_at=day(1)),limit=2)
    assert {r['id'] for r in historical}=={initial,other}
    service=MemoryService(s.store,Settings(embedding_model=model),CaptureProvider())
    result=await service.search(Query(query='semanticquery'))
    found=next(r for r in result['results'] if r['id']=='many')
    assert found['record_id']==latest and found['match_scope']=='historical'
    assert latest not in found['matched_record_ids']


def test_legacy_source_retry_survives_added_entity_metadata_field(scenario):
    s=scenario
    source=Source(id='legacy-input',content='old payload',occurred_at=s.now)
    extraction=Extraction(experiences=[episode('legacy',entities=[Entity(id='friend',name='朋友')])])
    first=s.store.apply(source,extraction)
    receipt=s.store.source(source.id)
    old=json.loads(receipt['extraction'])
    del old['extraction']['experiences'][0]['entities'][0]['operation']
    s.store._run('MATCH (s:EvidenceSource {uid:$uid}) SET s.extraction=$extraction',
                 uid=s.store._uid(source.id),extraction=json.dumps(old,ensure_ascii=False,sort_keys=True))
    assert s.store.apply(source,extraction)==first


async def test_review_tools_pagination_validation_and_detail_time_view(scenario):
    service=MemoryService(scenario.store,Settings())
    try:
        tools=MemoryTools(service)
        page=await tools.call('memory_reviews',{'kind':'understanding','limit':1,'at':day(1).isoformat(),'known_at':day(1).isoformat()})
        assert page['ok'] and page['result']['items']==[] and page['result']['next_offset'] is None
        invalid=await tools.call('memory_reviews',{'limit':101})
        assert not invalid['ok'] and invalid['error']['code']=='invalid_arguments'
        assert 'at' in next(d['parameters']['properties'] for d in tools.definitions() if d['name']=='memory_details')
    finally:await service.provider.close()


def test_vector_budget_exhaustion_is_reported(scenario,monkeypatch):
    s=scenario;original=s.store._run;fetches=[]
    def run(query,**params):
        if 'EmbeddingIndex' in query:return [{'dimensions':2}]
        if 'db.index.vector.queryNodes' not in query:return original(query,**params)
        fetches.append(params['fetch'])
        return [{'id':f'old-{i}','key':'one-memory','kind':'experience','subject':None,'state_key':None,
                 'score':1.,'eligible':True} for i in range(params['fetch'])]
    monkeypatch.setattr(s.store,'_run',run)
    rows,warning=s.store.vector_matches([1.,0.],'budget-test',s.store.capture_view(),limit=2)
    assert len(rows)==1 and warning and max(fetches)==4096
    assert fetches==sorted(set(fetches))
