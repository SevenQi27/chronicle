import json
from uuid import uuid4

import pytest

from conftest import day
from chronicle_memory.backup import backup
from chronicle_memory.capture import CaptureEvent,CaptureQueue,archive_turn,process_pending
from chronicle_memory.errors import ConflictError,ForgottenError
from chronicle_memory.maintenance import forget,visibility
from chronicle_memory.models import (Association,Entity,Experience,Extraction,MemoryChange,
    MemoryFilters,Query,Source,Understanding,WriteContext,state_key)
from chronicle_memory.service import MemoryService
from chronicle_memory.store import MemoryStore
from chronicle_memory.tools import MemoryTools
from chronicle_memory.ui import graph_snapshot


def put(s,key,text='aurora',*,conversation='alpha',workspace='/one',replaces=None,entities=None):
    source=Source(id='source-'+uuid4().hex,content=text,conversation_id=conversation,occurred_at=s.now)
    result=s.store.apply(source,Extraction(experiences=[Experience(id=key,title=text,narrative=text,
        replaces=replaces,entities=entities or [])]),context=WriteContext(origin='test',workspace=workspace))
    return source,result['record_ids'][0]


async def test_multi_query_unions_deduplicates_and_keeps_one_view(scenario):
    s=scenario
    _,first=put(s,'first','aurora')
    _,second=put(s,'second','zephyr')
    service=MemoryService(s.store,s.settings)
    try:
        result=await service.search(Query(query='aurora',alternatives=['zephyr','aurora']))
        assert {r['record_id'] for r in result['results']}=={first,second}
        assert [q['query'] for q in result['queries']]==['aurora','zephyr']
        assert result['score_method']=='query_rrf'
        assert all(r['matched_queries'] and r['matches'] for r in result['results'])
        same=await service.search(Query(query='aurora',alternatives=['aurora']))
        assert len(same['results'])==1 and same['results'][0]['matched_queries']==['aurora']
        # Both wordings hit one logical memory; neither duplicates it nor changes
        # the current-vs-historical evidence distinction.
        s.now=day(2)
        _,new=put(s,'first','zephyr',replaces=first)
        result=await service.search(Query(query='aurora',alternatives=['zephyr']))
        row=next(r for r in result['results'] if r['id']=='first')
        assert row['record_id']==new and row['match_scope']=='current'
        assert set(row['matched_record_ids'])=={first,new}
        assert {m['match_scope'] for m in row['matches']}=={'current','historical'}
    finally:await service.provider.close()


async def test_scopes_cover_search_context_neighbors_history_and_version_cutoff(scenario):
    s=scenario;entity=Entity(id='friend',name='sharedfriend')
    source,first=put(s,'first',entities=[entity])
    _,second=put(s,'second',conversation='beta',workspace='/two',entities=[entity])
    service=MemoryService(s.store,s.settings)
    try:
        for scope in ({'conversation_id':'alpha'},{'workspace':'/one'},{'source_id':source.id}):
            query=Query(query='aurora',alternatives=['sharedfriend'],filters=MemoryFilters(**scope))
            result=await service.context(query)
            assert [r['record_id'] for r in result['results']]==[first]
            assert result['related_memories']==[]
            history=s.store.history(filters=scope)
            assert [r['record_id'] for r in history['undated']]==[first]
            assert s.store.neighbors(first,filters=scope)['neighbors']==[]
        assert len((await service.search(Query(query='aurora')))['results'])==2
        assert (await service.search(Query(query='aurora',filters=MemoryFilters(origin='missing'))))['results']==[]
        s.now=day(2)
        put(s,'first','newword',conversation='beta',workspace='/two',replaces=first)
        # A match in an old scope must not expose the new out-of-scope revision.
        assert (await service.search(Query(query='aurora',filters=MemoryFilters(workspace='/one'))))['results']==[]
        past=await service.search(Query(query='aurora',known_at=day(1),filters=MemoryFilters(workspace='/one')))
        assert past['results'][0]['record_id']==first
    finally:await service.provider.close()


async def test_scoped_state_candidates_and_upcoming_do_not_leak(scenario):
    s=scenario
    for name,workspace in [('alice','/one'),('bob','/two')]:
        s.store.apply(Source(id=name,content='owner '+name,conversation_id=name,occurred_at=s.now),Extraction(memories=[
            MemoryChange(subject='project',key='owner',kind='fact',text='owner '+name,value=name,effective_at=s.now),
            MemoryChange(subject='project',key='plan-'+name,kind='plan',text='launch '+name,value=name,effective_at=s.now,due_at=day(3))
        ]),context=WriteContext(workspace=workspace))
    service=MemoryService(s.store,s.settings)
    try:
        result=await service.context(Query(query='owner',subject='project',filters=MemoryFilters(workspace='/one')))
        assert all(item['value']=='alice' for row in result['results'] for item in row['items'])
        assert all(item['value']=='alice' for item in result['upcoming'])
        # Retain the known conflict even if the selected scope shows one side.
        assert result['results'][0]['status']=='conflict'
    finally:await service.provider.close()


async def test_withdraw_restore_all_revisions_and_new_versions(scenario):
    s=scenario;source,record=put(s,'first')
    belief=s.store.apply(Source(id='belief-src',content='aurora belief',occurred_at=s.now),Extraction(understandings=[
        Understanding(id='belief',title='belief',statement='aurora belief',basis='inferred',supports=[record])]))['record_ids'][0]
    visibility(s.store,[record],'withdraw','user request')
    assert s.store.details([record])==[]
    assert s.store.details([record],include_withdrawn=True)[0]['status']=='withdrawn'
    assert s.store.history(memory_id='first')['undated']==[]
    assert s.store.history(memory_id='first',include_withdrawn=True)['undated'][0]['status']=='withdrawn'
    assert s.store.records(logical_ids=['belief'])[0]['status']=='needs_review'
    assert [x['id'] for x in s.store.reviews(kind='understanding')['items']]==['belief']
    assert all(n.get('record_id')!=record for n in graph_snapshot(s.store)['nodes'])
    assert not s.store.recent_sources('alpha','other')
    s.now=day(2)
    _,new=put(s,'first','aurora new',replaces=record)
    assert s.store.records(logical_ids=['first'])==[]
    service=MemoryService(s.store,s.settings)
    try:
        found=await service.search(Query(query='aurora',include_inactive=True))
        assert all(r['id']!='first' for r in found['results'])
        assert s.store.history(memory_id='first',known_at=day(1))['undated']==[]
        visibility(s.store,['first'],'restore')
        assert s.store.records(logical_ids=['first'])[0]['record_id']==new
        assert s.store.details([record]) and s.store.details([belief])
    finally:await service.provider.close()


def test_state_visibility_uses_unambiguous_key_and_preserves_timeline(scenario):
    s=scenario;record=s.write()
    visibility(s.store,[record],'withdraw')
    assert s.state()['status']=='unknown'
    assert s.store.timeline('项目 A','owner')['events']==[]
    s.now=day(2);s.write('李四',effective=2)
    assert s.state()['status']=='unknown'
    visibility(s.store,[state_key('项目 A','owner')],'restore')
    assert s.state()['memory']['value']=='李四'
    assert len(s.store.timeline('项目 A','owner')['events'])==2


def test_forget_preview_shared_sources_dependencies_and_association_evidence(scenario):
    s=scenario
    source=Source(id='shared-source',content='sensitive aurora',occurred_at=s.now)
    request=Extraction(experiences=[Experience(id=k,title=k,narrative='sensitive aurora') for k in ['one','two']])
    records=s.store.apply(source,request)['record_ids']
    s.store.apply(Source(id='belief-src',content='belief sensitive',occurred_at=s.now),Extraction(understandings=[
        Understanding(id='belief',title='belief',statement='sensitive aurora',basis='inferred',supports=[records[0]])]))
    put(s,'keep-left','left');put(s,'keep-right','right')
    s.store.apply(Source(id='assoc-source',content='sensitive evidence',occurred_at=s.now),Extraction(associations=[
        Association(id='evidence-link',from_id='keep-left',to_id='keep-right',kind='related_to',
                    explanation='sensitive aurora',basis='explicit',evidence_ids=[records[0]])]))
    preview=forget(s.store,['one'])
    assert set(preview['affected_memory_ids'])=={'one','two','belief'}
    assert preview['affected_association_ids']==['evidence-link']
    assert s.store.details(records)
    with pytest.raises(ConflictError):forget(s.store,['one'],'bad-token')
    deleted=forget(s.store,['one'],preview['confirmation'])
    assert deleted['action']=='forgotten'
    assert s.store.details(records,include_withdrawn=True)==[]
    assert {r['id'] for r in s.store.records()}=={'keep-left','keep-right'}
    assert s.store.association_views()==[] and s.store.source('assoc-source') is None
    assert s.store.source('shared-source') is None
    with pytest.raises(ForgottenError):s.store.apply(source,request)
    with pytest.raises(ForgottenError):put(s,'one','replayed under another source')
    assert s.store._run('MATCH (n {namespace:$ns}) WHERE any(k IN keys(n) WHERE toString(n[k]) CONTAINS "sensitive aurora") RETURN count(n) AS n',ns=s.namespace)[0]['n']==0


def test_forget_rechecks_preview_after_new_revision(scenario):
    s=scenario;_,record=put(s,'one')
    preview=forget(s.store,['one'])
    s.now=day(2);put(s,'one','new content',replaces=record)
    with pytest.raises(ConflictError):forget(s.store,['one'],preview['confirmation'])
    assert len(s.store.history(memory_id='one')['undated'])==2


def test_forget_capture_purges_saved_outbox_and_rejects_replay(scenario,tmp_path):
    s=scenario;queue=CaptureQueue(tmp_path/'capture.sqlite3')
    event=CaptureEvent(origin='test',turn_id='turn',workspace='/one',source=Source(id='message',content='secret passage',conversation_id='conv',occurred_at=s.now))
    queue.submit(event)
    factory=lambda settings:MemoryStore(settings,s.namespace,clock=lambda:s.now)
    assert process_pending(queue,s.settings,store_factory=factory)['saved']==1
    record=s.store.records()[0]
    preview=forget(s.store,[record['id']]);forget(s.store,[record['id']],preview['confirmation'])
    assert process_pending(queue,s.settings,store_factory=factory)=={'saved':0,'failed':0}
    assert queue.status()['counts']=={'forgotten':1}
    with queue.connect() as db:assert db.execute('SELECT body FROM events').fetchone()['body'] is None
    assert queue.submit(event)['status']=='forgotten'
    assert queue.submit(event.model_copy(update={'source':event.source.model_copy(update={'id':'next'})}))['status']=='forgotten'
    # A separate queue does not yet have the local tombstone; the DB still blocks it.
    other=CaptureQueue(tmp_path/'other.sqlite3');other.submit(event)
    assert process_pending(other,s.settings,store_factory=factory)=={'saved':0,'failed':0}
    assert other.status()['counts']=={'forgotten':1}
    assert s.store.records()==[]


@pytest.fixture
def restored(scenario):
    store=MemoryStore(scenario.settings,'test-restore-'+uuid4().hex,clock=lambda:scenario.now)
    yield store
    store.drop_vector_indexes()
    store._run('MATCH (n {namespace:$ns}) DETACH DELETE n',ns=store.namespace)
    store._run('MATCH (n:MemoryNamespace {name:$ns}) DELETE n',ns=store.namespace)
    store.close()


def test_backup_round_trip_versions_evidence_relations_visibility_and_tombstones(scenario,restored,tmp_path):
    s=scenario
    source,first=put(s,'first',entities=[Entity(id='person',name='Person')])
    s.now=day(2);_,second=put(s,'first','updated',replaces=first)
    s.write('owner',kind='plan',due_at=day(3))
    put(s,'other')
    s.store.apply(Source(id='relation',content='relation evidence',occurred_at=s.now),Extraction(associations=[
        Association(id='link',from_id='first',to_id='other',kind='related_to',explanation='connected',basis='explicit',evidence_ids=[first])]))
    put(s,'forgotten','forgotten separate source')
    preview=forget(s.store,['forgotten']);forget(s.store,['forgotten'],preview['confirmation'])
    visibility(s.store,['first'],'withdraw')
    s.store.save_embedding(second,'test-model',[1.,0.,0.]);s.store.ensure_vector_index('test-model',3)
    path=tmp_path/'backup.json'
    exported=backup(s.store,'export',str(path))
    assert path.stat().st_mode&0o777==0o600
    assert exported['nodes']>5
    with pytest.raises(FileExistsError):backup(s.store,'export',str(path))
    checked=backup(restored,'validate',str(path));assert checked['target_empty']
    result=backup(restored,'restore',str(path),checked['confirmation'])
    assert result['verified_counts'] and result['vector_indexes_pending']==[]
    assert restored.counts()==s.store.counts()
    original=s.store.details([first,second],include_withdrawn=True)
    recovered=restored.details([first,second],include_withdrawn=True)
    for rows in (original,recovered):
        for row in rows:row['sources']=sorted(row['sources'],key=lambda source:source['id'])
    assert recovered==original, [{k:(a.get(k),b.get(k)) for k in a.keys()|b.keys() if a.get(k)!=b.get(k)} for a,b in zip(recovered,original)]
    assert restored.records(logical_ids=['first'])==[]
    visibility(restored,['first'],'restore')
    assert restored.records(logical_ids=['first'])[0]['record_id']==second
    assert restored.state('项目 A','owner')['memory']['value']=='owner'
    assert restored.association_views()[0]['explanation']=='connected'
    assert restored.vector_matches([1.,0.,0.],'test-model',restored.capture_view())[0][0]['id']==second
    with pytest.raises(ForgottenError):restored.apply(Source(id='x',content='x',occurred_at=s.now),Extraction(experiences=[Experience(id='forgotten',title='x',narrative='x')]))
    # An immutable receipt can still be replayed in the restored partition.
    receipt=restored.source(source.id);saved=json.loads(receipt['extraction'])
    result=restored.apply(source,Extraction.model_validate(saved['extraction']),context=WriteContext.model_validate(saved['context']))
    assert result['record_ids']==[first]
    with pytest.raises(ConflictError):backup(restored,'restore',str(path),checked['confirmation'])


def test_backup_corruption_and_target_binding_are_checked_before_mutation(scenario,restored,tmp_path):
    put(scenario,'first');path=tmp_path/'backup.json';backup(scenario.store,'export',str(path))
    other_target=backup(scenario.store,'validate',str(path))
    with pytest.raises(ValueError):backup(restored,'restore',str(path),other_target['confirmation'])
    envelope=json.loads(path.read_text());envelope['data']['nodes'][0]['properties']['unexpected']='tampered'
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError,match='完整性'):backup(restored,'validate',str(path))
    assert restored.counts()=={}


async def test_tools_expose_lifecycle_and_capture_context(scenario,tmp_path):
    service=MemoryService(scenario.store,scenario.settings);tools=MemoryTools(service)
    try:
        response=await tools.call('memory_write',{'source':{'id':'tool','content':'aurora','occurred_at':scenario.now.isoformat(),'conversation_id':'tool-conv'},
            'context':{'origin':'adapter','workspace':'/tools'},'experiences':[{'id':'tool','title':'aurora','narrative':'aurora'}]})
        assert response['ok']
        result=await tools.call('memory_search',{'query':'nooverlap','alternatives':['aurora'],'filters':{'workspace':'/tools'}})
        assert result['result']['results'][0]['id']=='tool'
        assert (await tools.call('memory_visibility',{'memory_ids':['tool'],'action':'withdraw'}))['ok']
        assert (await tools.call('memory_visibility',{'memory_ids':['tool'],'action':'restore'}))['ok']
        path=str(tmp_path/'tools.json')
        assert (await tools.call('memory_backup',{'action':'export','path':path}))['ok']
        preview=await tools.call('memory_forget',{'memory_ids':['tool']})
        assert preview['ok'] and preview['result']['action']=='preview'
        assert (await tools.call('memory_forget',{'memory_ids':['tool'],'confirmation':preview['result']['confirmation']}))['ok']
    finally:await service.provider.close()


def test_withdrawn_understanding_still_has_explicit_inspection_and_restore(scenario):
    s=scenario;_,record=put(s,'experience')
    belief=s.store.apply(Source(id='belief',content='belief',occurred_at=s.now),Extraction(understandings=[
        Understanding(id='belief',title='belief',statement='belief',basis='inferred',supports=[record])]))['record_ids'][0]
    visibility(s.store,['belief'],'withdraw')
    assert s.store.details([belief])==[]
    detail=s.store.details([belief],include_withdrawn=True)[0]
    assert detail['status']=='withdrawn' and detail['evidence_status']=='current'
    assert s.store.reviews()['items']==[]
    visibility(s.store,['belief'],'restore')
    assert s.store.details([belief])[0]['status']=='active'


def test_capture_metadata_backfill_and_scoped_history(scenario):
    s=scenario
    event=CaptureEvent(origin='adapter',turn_id='old-turn',workspace='/legacy',source=Source(
        id='original',content='legacy event',conversation_id='old-chat',occurred_at=s.now))
    record=archive_turn(s.store,'capture-turn:legacy',[event])
    s.store._run('MATCH (r:MemoryRecord {uid:$uid}) REMOVE r.origin,r.workspace,r.conversation_id',uid=s.store._uid(record))
    assert s.store.history(filters={'workspace':'/legacy'})['records']==[]
    s.store.backfill_context()
    assert s.store.history(filters={'workspace':'/legacy','origin':'adapter','conversation_id':'adapter:old-chat'})['records'][0]['record_id']==record


def test_cleanup_watermark_revisits_saved_rows_when_new_forgetting_occurs(scenario,tmp_path):
    s=scenario;queue=CaptureQueue(tmp_path/'queue.sqlite3')
    for number in range(2):
        queue.submit(CaptureEvent(origin='test',turn_id=str(number),source=Source(id=str(number),content='content '+str(number),conversation_id='conv',occurred_at=s.now)))
    factory=lambda settings:MemoryStore(settings,s.namespace,clock=lambda:s.now)
    assert process_pending(queue,s.settings,store_factory=factory)['saved']==2
    records=s.store.records()
    preview=forget(s.store,[records[0]['id']]);forget(s.store,[records[0]['id']],preview['confirmation'])
    assert queue.purge_forgotten(s.store)==1
    assert queue.purge_forgotten(s.store)==0
    preview=forget(s.store,[records[1]['id']]);forget(s.store,[records[1]['id']],preview['confirmation'])
    assert queue.purge_forgotten(s.store)==1
    assert queue.status()['counts']=={'forgotten':2}
