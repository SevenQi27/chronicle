from chronicle_memory.config import Settings
from chronicle_memory.models import Source,Extraction,Experience,ExperienceTime,Entity,Query
from chronicle_memory.service import MemoryService
from chronicle_memory.store import MemoryStore
from conftest import day


def test_client_startup_does_not_create_or_wait_for_indexes(scenario,monkeypatch):
    queries=[];original=MemoryStore._run
    def run(self,query,**params):
        queries.append(query)
        return original(self,query,**params)
    monkeypatch.setattr(MemoryStore,'_run',run)
    other=MemoryStore(scenario.settings,scenario.namespace)
    other.close()
    assert queries
    assert not any('CREATE ' in q or 'awaitIndex' in q for q in queries)


def test_short_entity_names_require_exact_or_explicit_reference(scenario):
    store=scenario.store
    store.apply(Source(id='names',content='谈到林、安宁和 Ann',occurred_at=day(1)),Extraction(experiences=[
        Experience(id='names',title='人物',narrative='一次交流',entities=[
            Entity(id='lin',name='林'),Entity(id='anning',name='安宁'),Entity(id='ann',name='Ann')])]))
    assert store.entity_matches('森林里的安宁和 planning')==[]
    assert store.entity_matches('安宁')
    assert store.entity_matches('Ask Ann about it')
    assert store.entity_candidates('想了解这个人',store.capture_view(),subject='lin')


async def test_index_candidates_limit_python_projection(scenario,monkeypatch):
    store=scenario.store
    for batch in range(6):
        store.apply(Source(id=f'batch-{batch}',content='批量独立经历',occurred_at=day(1)),Extraction(experiences=[
            Experience(id=f'item-{batch}-{i}',title=f'共同主题 {batch}-{i}',narrative='共同主题下的一段独立经历',
                       when=ExperienceTime(start=day(1),precision='day')) for i in range(20)]))
    reads=[];original=store._rows
    def rows(*args,**kwargs):
        result=original(*args,**kwargs);reads.append(len(result));return result
    monkeypatch.setattr(store,'_rows',rows)
    service=MemoryService(store,Settings())
    try:
        result=await service.context(Query(query='共同主题',limit=3))
        assert len(result['results'])==3
        assert result['candidate_count']<=result['candidate_budget']==64
        assert max(reads)<=64  # 120 total records; no full-namespace projection.
    finally:
        await service.provider.close()


def test_native_vector_index_is_used(scenario,monkeypatch):
    store=scenario.store
    eid=scenario.write('简短')
    store.ensure_vector_index('test-native-vector',2)
    store.save_embedding(eid,'test-native-vector',[1.0,0.0])
    queries=[];original=store._run
    def run(query,**params):
        queries.append(query);return original(query,**params)
    monkeypatch.setattr(store,'_run',run)
    found,warning=store.vector_matches([1.0,0.0],'test-native-vector',store.capture_view())
    assert warning is None and found[0]['id']==eid
    assert any('db.index.vector.queryNodes' in q for q in queries)
