from chronicle_memory.models import Entity,Experience,ExperienceTime,Extraction,Source,Understanding,Query
from chronicle_memory.config import Settings
from chronicle_memory.service import MemoryService
from conftest import day


def write(s,source_id,**items):
    return s.store.apply(Source(id=source_id,content=source_id,occurred_at=s.now),Extraction(**items))


def episode(identifier,entities,replaces=None):
    return Experience(id=identifier,title=identifier,narrative='一段相关经历',entities=entities,
        when=ExperienceTime(start=day(1),precision='day'),replaces=replaces)


def test_many_old_versions_do_not_hide_current_entity_neighbors(scenario):
    s=scenario;person=Entity(id='friend',name='同伴',category='person')
    initial=write(s,'initial',experiences=[episode('seed',[person]),episode('a-history',[person]),episode('z-other',[person])])
    seed,first,other=initial['record_ids'];snapshot=s.store.capture_view()
    current=first
    for i in range(105):
        current=write(s,f'update-{i}',experiences=[episode('a-history',[person],current)])['record_ids'][0]
    result=s.store.neighbors(seed)
    assert [r['record']['record_id'] for r in result['neighbors']]==[current,other]
    assert result['truncated'] is False
    assert s.store.neighbors(seed,view=snapshot)['neighbors'][0]['record']['record_id']==first
    # A corrected account that no longer involves this person must not be
    # reintroduced by mapping an old relationship straight to the newest ID.
    write(s,'removed-person',experiences=[episode('a-history',[],current)])
    assert [r['record']['record_id'] for r in s.store.neighbors(seed)['neighbors']]==[other]


async def test_neighbor_order_limits_and_recall_truncation(scenario):
    s=scenario;person=Entity(id='friend',name='共同朋友')
    ids=write(s,'many',experiences=[episode('seed',[person])]+[
        episode(f'item-{i:02}',[person]) for i in range(16)])['record_ids']
    first=s.store.neighbors(ids[0],limit=3)
    again=s.store.neighbors(ids[0],limit=3)
    assert first==again and first['truncated']
    assert [r['record']['id'] for r in first['neighbors']]==['item-00','item-01','item-02']
    assert [r['key'] for r in s.store.entity_candidates('共同朋友',s.store.capture_view(),3)]==['item-00','item-01','item-02']
    service=MemoryService(s.store,Settings())
    try:
        result=await service.context(Query(query='seed',limit=1))
        assert result['related_truncated'] is True
        assert result['related_memories']
    finally:
        await service.provider.close()


def test_evidence_neighbors_select_latest_understanding_before_limit(scenario):
    s=scenario
    seed=write(s,'seed',experiences=[episode('seed',[])])['record_ids'][0]
    current=None
    for i in range(105):
        current=write(s,f'belief-{i}',understandings=[Understanding(id='belief',title='对经历的认识',
            statement=f'认识版本 {i}',basis='inferred',supports=[seed],replaces=current)])['record_ids'][0]
    result=s.store.neighbors(seed)
    assert [r['record']['record_id'] for r in result['neighbors']]==[current]
    assert result['truncated'] is False


def test_current_neighbor_resolution_respects_corrections_and_time(scenario):
    s=scenario
    first=write(s,'first',experiences=[episode('seed',[])],understandings=[Understanding(
        id='belief',title='认识',statement='最初认识',basis='inferred',supports=['seed'],valid_from=day(1))])
    seed,belief=first['record_ids']
    s.now=day(10)
    write(s,'correct',understandings=[Understanding(id='belief',title='认识',statement='适用时间更正',
        basis='inferred',supports=[seed],valid_from=day(5),valid_to=day(8),replaces=belief,revision_kind='correct')])
    assert s.store.neighbors(seed,at=day(2))['neighbors']==[]
    assert s.store.neighbors(seed,at=day(9))['neighbors']==[]
    assert s.store.neighbors(seed,at=day(6))['neighbors']
    assert s.store.neighbors(seed,at=day(2),known_at=day(2))['neighbors'][0]['record']['record_id']==belief
