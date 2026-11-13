"""Boundary and lifecycle regressions from the four-item follow-up review."""
from datetime import timedelta,timezone

import pytest

from conftest import day
from chronicle_memory.config import Settings
from chronicle_memory.maintenance import forget,visibility
from chronicle_memory.models import Association,Experience,Extraction,MemoryChange,Source,Understanding
from chronicle_memory.service import MemoryService


def plan(s,identifier,effective,due,**extra):
    return s.store.apply(Source(id=identifier,content=identifier,occurred_at=s.now),Extraction(memories=[
        MemoryChange(subject='project',key=extra.pop('key','release'),kind='plan',text=identifier,value=identifier,
                     effective_at=effective,due_at=due,**extra)]))['event_ids'][0]


@pytest.mark.parametrize('effective',['2026-11-01T01:00:00Z','2026-11-01T01:00:00+00:00','2026-11-01T09:00:00+08:00'])
def test_upcoming_at_effective_boundary_has_status_and_due_order(scenario,effective):
    s=scenario
    late=plan(s,'late',effective,day(9),key='late')
    early=plan(s,'early',effective,day(3),key='early')
    for offset in (0,1,999999,1000000):
        at=s.now+timedelta(microseconds=offset)
        upcoming=s.store.upcoming({'project'},at,at)
        assert all(row.get('status')=='scheduled' for row in upcoming)
        assert [row['event_id'] for row in upcoming]==[early,late]


def test_upcoming_excludes_same_second_superseded_plan(scenario):
    s=scenario
    plan(s,'old',day(1).astimezone(timezone.utc),day(3))
    s.now+=timedelta(microseconds=100)
    current=plan(s,'new',s.now,day(5))
    upcoming=s.store.upcoming({'project'},s.now,s.now)
    assert [row['event_id'] for row in upcoming]==[current]
    assert upcoming[0]['status']=='scheduled'


def test_upcoming_future_candidate_has_status_and_corrected_version(scenario):
    s=scenario
    old=plan(s,'old',day(5),day(7))
    new=plan(s,'corrected',day(5),day(8),operation='correct',corrects=old)
    future=s.store.upcoming({'project'},s.now,s.now)
    assert [row['event_id'] for row in future]==[new]
    assert future[0]['status']=='scheduled'


async def test_ingest_keeps_shared_source_but_not_withdrawn_memory(scenario):
    s=scenario
    source=Source(id='shared',content='first account and second account',occurred_at=s.now,conversation_id='same')
    ids=s.store.apply(source,Extraction(experiences=[Experience(id=k,title=k,narrative=k) for k in ('first','second')]))['record_ids']
    visibility(s.store,[ids[0]],'withdraw')
    previous=s.store.recent_sources('same','next')
    assert [row['id'] for row in previous]==['shared']
    assert previous[0]['content']==source.content
    class Provider:
        async def extract(self,source,existing,previous):
            self.existing=existing;self.previous=previous
            return Extraction()
        async def close(self):pass
    provider=Provider();service=MemoryService(s.store,Settings(model='test'),provider)
    await service.ingest(Source(id='next',content='unmatchedword',occurred_at=s.now,conversation_id='same'))
    assert [row['id'] for row in provider.previous]==['shared']
    assert [row['record_id'] for row in provider.existing]==[ids[1]]
    visibility(s.store,[ids[1]],'withdraw')
    assert s.store.recent_sources('same','next')==[]
    visibility(s.store,[ids[0]],'restore')
    assert [row['id'] for row in s.store.recent_sources('same','next')]==['shared']


@pytest.mark.parametrize('kind',['plan','understanding'])
def test_withdrawn_due_check_remains_pending_then_fires_once_after_restore(scenario,kind):
    s=scenario
    if kind=='plan':
        record=plan(s,'plan',day(1),day(3));trigger='deadline'
    else:
        record=s.store.apply(Source(id='belief',content='belief',occurred_at=s.now),Extraction(
            experiences=[Experience(id='experience',title='event',narrative='event')],
            understandings=[Understanding(id='belief',title='belief',statement='belief',basis='inferred',supports=['experience'],review_at=day(3))]))['record_ids'][-1]
        trigger='review'
    s.store.tick()  # Any activation due before withdrawal is already processed.
    visibility(s.store,[record],'withdraw')
    s.now=day(4)
    assert s.store.tick()==[]
    pending=next(row for row in s.store.checks()['items'] if row['event_id']==record and row['trigger']==trigger)
    assert pending.get('processed_at') is None and pending.get('outcome') is None
    visibility(s.store,[record],'restore')
    results=s.store.tick()
    assert [(row['event_id'],row['trigger'],row['outcome']) for row in results]==[(record,trigger,'awaiting_outcome' if kind=='plan' else 'needs_review')]
    assert s.store.tick()==[]
    visibility(s.store,[record],'withdraw');visibility(s.store,[record],'restore')
    assert s.store.tick()==[]


def test_withdrawn_checks_do_not_consume_worker_batch(scenario):
    s=scenario
    changes=[MemoryChange(subject='project',key=f'paused-{i}',kind='plan',value=i,text='paused',effective_at=day(1),due_at=day(2)) for i in range(40)]
    # Three batches of input stay within the existing write request size limits.
    records=[]
    for i in range(3):
        shifted=[c.model_copy(update={'key':f'{c.key}-{i}'}) for c in changes]
        records+=s.store.apply(Source(id=f'batch-{i}',content='batch',occurred_at=s.now),Extraction(memories=shifted))['event_ids']
    visibility(s.store,records,'withdraw')
    live=plan(s,'live',day(1),day(3),key='live')
    s.now=day(4)
    result=s.store.tick()
    assert result and {row['event_id'] for row in result}=={live}
    assert all(row.get('processed_at') is None for row in s.store.checks(limit=200)['items'] if row['event_id'] in records)


def test_forget_association_delete_uses_relationship_property_indexes(scenario,monkeypatch):
    s=scenario
    s.store.apply(Source(id='items',content='items',occurred_at=s.now),Extraction(experiences=[Experience(id=k,title=k,narrative=k) for k in ('a','b')]))
    s.store.apply(Source(id='relation',content='relation',occurred_at=s.now),Extraction(associations=[
        Association(id='link',from_id='a',to_id='b',kind='related_to',explanation='link',basis='explicit',evidence_ids=['a'])]))
    preview=forget(s.store,['a']);queries=[];original=s.store._write
    class Tx:
        def __init__(self,tx):self.tx=tx
        def run(self,query,**params):
            if 'DELETE r' in query and 'association' in query:queries.append((query,params))
            return self.tx.run(query,**params)
    monkeypatch.setattr(s.store,'_write',lambda callback:original(lambda tx:callback(Tx(tx))))
    forget(s.store,['a'],preview['confirmation'])
    assert s.store.association_views()==[] and queries
    def operators(p):return [p['operatorType']]+[o for child in p.get('children',[]) for o in operators(child)]
    with s.store.driver.session(database=s.settings.neo4j_database) as session:
        for query,params in queries:
            names=operators(session.run('EXPLAIN '+query,**params).consume().plan)
            assert not any('AllRelationshipsScan' in name for name in names)
            assert any('RelationshipIndexSeek' in name for name in names)
