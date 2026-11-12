import pytest

from chronicle_memory.models import Association, Entity, Experience, ExperienceTime, Extraction, Source, Understanding, Query
from chronicle_memory.service import MemoryService
from chronicle_memory.config import Settings
from conftest import day


def write(s,identifier,**kwargs):
    return s.store.apply(Source(id=identifier,content='本次真实来源：'+identifier,occurred_at=s.now),Extraction(**kwargs))


def episode(identifier,title,narrative,*,entities=None,when=None,**kwargs):
    return Experience(id=identifier,title=title,narrative=narrative,
                      entities=entities or [],when=when or ExperienceTime(start=day(1),precision='day'),**kwargs)


def test_experience_preserves_context_and_unknown_time_in_real_graph(scenario):
    s=scenario
    result=write(s,'trip',experiences=[episode('camping','一次露营','遇雨提前返回，但和朋友一起处理意外很开心。',
        when=ExperienceTime(precision='approximate',description='去年春天，日期不详'),
        entities=[Entity(id='friend-lin',name='小林',category='person')])])
    detail=s.store.details(result['record_ids'])[0]
    assert detail['narrative']=='遇雨提前返回，但和朋友一起处理意外很开心。'
    assert detail['when']['start'] is None
    assert detail['sources'][0]['id']=='trip'
    graph=s.store._run('MATCH (e:Experience {namespace:$ns})-[:INVOLVES]->(p:Entity) '
                      'MATCH (e)-[:EVIDENCED_BY]->(s:EvidenceSource) RETURN p.name AS name,s.id AS id',ns=s.namespace)
    assert graph==[{'name':'小林','id':'trip'}]
    assert len(s.store.history(start=day(1),end=day(3))['undated'])==1


def test_understanding_requires_experience_evidence_and_preserves_inference(scenario):
    s=scenario
    result=write(s,'reading',experiences=[episode('reading','阅读安排','连续阅读两小时后感到疲惫，短休息后更容易继续。')],
        understandings=[Understanding(id='reading-rhythm',title='阅读节奏',statement='短休息可能帮助维持阅读状态，仍需更多经历验证。',basis='inferred',supports=['reading'])])
    records=s.store.records()
    insight=next(r for r in records if r['record_kind']=='understanding')
    assert insight['basis']=='inferred' and insight['evidence_status']=='current'
    assert insight['support_record_ids']==[result['record_ids'][0]]
    with pytest.raises(ValueError,match='真实经历'):
        write(s,'circular',understandings=[Understanding(id='circular',title='循环证据',statement='不应被保存',basis='inferred',supports=[insight['record_id']])])
    assert len(s.store.records())==2


def test_corrected_experience_marks_derived_understanding_for_review(scenario):
    s=scenario
    initial=write(s,'initial',experiences=[episode('weekend','周末经历','我以为这次感到疲惫是因为步行太久。')],
        understandings=[Understanding(id='walking',title='步行感受',statement='长时间步行可能让用户疲惫。',basis='inferred',supports=['weekend'])])
    s.now=day(5)
    write(s,'correction',experiences=[episode('weekend','周末经历更正','用户澄清是当天没睡好，并非步行让他疲惫。',
        replaces=initial['record_ids'][0],revision_kind='correct')])
    current=next(r for r in s.store.records() if r['record_kind']=='understanding')
    past=next(r for r in s.store.records(known_at=day(2)) if r['record_kind']=='understanding')
    assert current['status']=='needs_review' and current['evidence_status']=='changed'
    assert past['status']=='active' and past['evidence_status']=='current'
    assert s.store.details([initial['record_ids'][0]])[0]['superseded'] is True
    assert s.store.details([initial['record_ids'][0]],known_at=day(2))[0]['superseded'] is False


def test_understanding_revision_preserves_past_view(scenario):
    s=scenario
    initial=write(s,'first',experiences=[episode('e1','第一次交流','用户说早起感觉不好。')],
        understandings=[Understanding(id='morning',title='早起感受',statement='这次早起体验不佳。',basis='explicit',supports=['e1'])])
    s.now=day(5)
    write(s,'later',experiences=[episode('e2','后来的交流','用户说明睡够时很喜欢早起。',when=ExperienceTime(start=day(5),precision='day'))],
        understandings=[Understanding(id='morning',title='对早起的新理解',statement='早起的感受取决于是否睡够，不能一概认定不喜欢早起。',
            basis='inferred',supports=['e1','e2'],replaces=initial['record_ids'][1],valid_from=day(5))])
    now=next(r for r in s.store.records() if r['record_kind']=='understanding')
    old=next(r for r in s.store.records(at=day(2),known_at=day(2)) if r['record_kind']=='understanding')
    assert '睡够' in now['statement'] and old['statement']=='这次早起体验不佳。'
    assert len(s.store.history(memory_id='morning')['undated'])==1


def test_shared_entity_is_an_explained_association_not_causality(scenario):
    s=scenario
    entity=Entity(id='friend-zhou',name='小周',category='person',aliases=['周同学'])
    first=write(s,'first',experiences=[episode('walk','和小周散步','散步时聊起一本书。',entities=[entity])])
    second=write(s,'second',experiences=[episode('book','收到小周送的书','收到一本小说，用户说很惊喜。',entities=[entity])])
    neighbors=s.store.neighbors(first['record_ids'][0])['neighbors']
    assert neighbors[0]['record']['record_id']==second['record_ids'][0]
    assert neighbors[0]['reasons'][0]['reason']=='shared_entity'
    assert neighbors[0]['reasons'][0]['via']['entity_id']=='friend-zhou'
    assert second['record_ids'][0] in s.store.entity_matches('周同学送的是什么')


def test_explicit_association_has_evidence_and_two_time_filters(scenario):
    s=scenario
    first=write(s,'evidence',experiences=[episode('a','一次经历','学会了一种新的表达。'),episode('b','另一次经历','尝试把表达用在交流中。')])
    s.now=day(5)
    linked=write(s,'link',associations=[Association(id='practice',from_id='a',to_id='b',kind='continues',
        explanation='用户明确说后一次是对前一次的继续尝试。',basis='explicit',evidence_ids=['link'],valid_from=day(3),valid_to=day(8))])
    assert s.store.association_views(at=day(4),known_at=day(2))==[]
    assert s.store.association_views(at=day(2))==[]
    assert s.store.association_views(at=day(9))==[]
    match=s.store.neighbors(first['record_ids'][0],at=day(4))['neighbors'][0]
    assert match['reasons'][0]['reason']=='continues'
    assert match['reasons'][0]['via']['id']==linked['association_ids'][0]


def test_missing_reference_rolls_back_entire_graph_write(scenario):
    s=scenario
    with pytest.raises(ValueError,match='不存在'):
        write(s,'bad',experiences=[episode('a','经历','保留完整内容。')],
              understandings=[Understanding(id='u',title='认识',statement='没有依据',basis='inferred',supports=['missing'])])
    assert s.store.records()==[]
    assert s.store.source('bad') is not None


def test_assistant_can_reflect_but_not_invent_experiences(scenario):
    s=scenario
    result=write(s,'user',experiences=[episode('e','用户经历','连续几次都表示喜欢自主安排。')])
    reflection=Source(id='reflection',content='根据已有经历归纳',role='assistant',occurred_at=s.now)
    s.store.apply(reflection,Extraction(understandings=[Understanding(id='autonomy',title='可能的倾向',statement='可能更偏好自主安排。',basis='inferred',supports=result['record_ids'])]))
    with pytest.raises(ValueError,match='助手复述'):
        s.store.apply(Source(id='fabrication',content='虚构故事',role='assistant',occurred_at=s.now),Extraction(experiences=[episode('fake','虚构','不应存入')]))


async def test_context_recalls_episodes_and_related_understanding(scenario):
    s=scenario
    write(s,'music',experiences=[episode('music','第一次听现场演出','朋友带我去听现场爵士，虽然不认识曲子，但即兴部分让我印象很深。',
        entities=[Entity(id='jazz',name='爵士乐',aliases=['Jazz'])])],
        understandings=[Understanding(id='improv',title='可能喜欢即兴',statement='这次对即兴印象深刻，尚不足以代表稳定的音乐偏好。',basis='inferred',supports=['music'])])
    service=MemoryService(s.store,Settings())
    try:
        result=await service.context(Query(query='Jazz 演出'))
        assert result['results'][0]['record_kind']=='experience'
        assert any(r['record_kind']=='understanding' for r in result['related_memories'])
        assert result['related_memories'][0]['basis']=='inferred'
    finally:
        await service.provider.close()


def test_expired_understanding_and_future_experience_do_not_leak(scenario):
    s=scenario
    write(s,'times',experiences=[episode('present','当前经历','最近喜欢安静。'),episode('future','未来记录','以后才发生的经历。',when=ExperienceTime(start=day(20),precision='exact'))],
        understandings=[Understanding(id='temporary',title='阶段状态',statement='这段时间偏好安静。',basis='explicit',supports=['present'],valid_from=day(1),valid_to=day(5))])
    s.now=day(10)
    assert [r['id'] for r in s.store.records()]==['present']
    assert any(r['status']=='expired' for r in s.store.records(include_inactive=True))
    assert any(r['id']=='future' for r in s.store.records(at=day(21)))


async def test_generic_review_resolution_uses_experience_model(scenario):
    from chronicle_memory.models import ReviewCandidate
    from chronicle_memory.tools import MemoryTools
    s=scenario
    pending=write(s,'unclear',review=[ReviewCandidate(text='上次和他聊起旅行',reason='他指的是谁尚不明确')])
    service=MemoryService(s.store,Settings())
    try:
        result=await MemoryTools(service).call('memory_resolve_review',{
            'review_id':pending['review_ids'][0],
            'source':{'id':'clarification','content':'他指小林','occurred_at':s.now.isoformat()},
            'experiences':[{'id':'conversation','title':'与小林聊旅行','narrative':'用户澄清，上次是与小林聊旅行。',
                'source_ids':['unclear'],'when':{'precision':'unknown'}}],
        })
        assert result['ok'] and len(result['result']['record_ids'])==1
        assert s.store.reviews()['items']==[]
        assert len(s.store.details(result['result']['record_ids'])[0]['sources'])==2
    finally:
        await service.provider.close()
