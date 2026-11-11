import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

from chronicle_memory.config import Settings
from chronicle_memory.errors import ConflictError
from chronicle_memory.models import Source,Extraction,Experience,ExperienceTime,MemoryChange,Query
from chronicle_memory.provider import ProviderError
from chronicle_memory.service import MemoryService
from chronicle_memory.store import MemoryStore
from chronicle_memory.tools import MemoryTools
from conftest import day


def story(text='第一次经历',replaces=None):
    return Experience(id='story',title='共同经历',narrative=text,
        when=ExperienceTime(start=day(1),precision='day'),replaces=replaces,revision_kind='correct' if replaces else 'update')


def test_parallel_duplicate_writes_commit_once(scenario):
    store=scenario.store
    source=Source(id='same-message',content='同一条消息',occurred_at=day(1))
    payload=Extraction(experiences=[story()])
    barrier=threading.Barrier(8)
    def write(_):
        barrier.wait(timeout=10)
        return store.apply(source,payload)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(write,range(8)))
    assert all(r==results[0] for r in results)
    assert store.counts()=={'experience':1}


def test_parallel_revisions_return_conflict_with_winning_version(scenario):
    store=scenario.store
    first=store.apply(Source(id='first',content='第一次经历',occurred_at=day(1)),Extraction(experiences=[story()]))
    initial=first['record_ids'][0]
    barrier=threading.Barrier(2)
    def write(number):
        barrier.wait(timeout=10)
        try:
            return store.apply(Source(id=f'update-{number}',content=f'更正 {number}',occurred_at=day(1)),
                               Extraction(experiences=[story(f'更正 {number}',initial)]))
        except ConflictError as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(write,range(2)))
    winner=next(r for r in results if isinstance(r,dict))
    loser=next(r for r in results if isinstance(r,ConflictError))
    assert loser.code=='version_conflict'
    assert loser.details['current_record_id']==winner['record_ids'][0]
    assert store.counts()=={'experience':2}


async def test_losing_ingest_cannot_mark_successful_source_failed(scenario):
    stored=threading.Event()
    first=threading.Event()
    source=Source(id='shared-ingest',content='同一份资料',occurred_at=day(1))
    original=scenario.store.apply
    def apply(*args,**kwargs):
        result=original(*args,**kwargs)
        stored.set()
        return result
    scenario.store.apply=apply
    class Success:
        async def extract(self,*_):
            await asyncio.to_thread(first.wait,10)
            return Extraction(experiences=[story()])
        async def close(self):pass
    class Failure:
        async def extract(self,*_):
            first.set()
            assert await asyncio.to_thread(stored.wait,10)
            raise ProviderError('第二个提取器失败')
        async def close(self):pass
    other=MemoryStore(scenario.settings,scenario.namespace,clock=lambda:scenario.now)
    a=MemoryService(scenario.store,Settings(model='test'),Success())
    b=MemoryService(other,Settings(model='test'),Failure())
    try:
        left,right=await asyncio.gather(a.ingest(source),b.ingest(source))
        assert left['record_ids']==right['record_ids']
        assert scenario.store.source(source.id)['status']=='applied'
        assert scenario.store.counts()=={'experience':1}
    finally:
        await b.close()


def test_parallel_workers_do_not_repeat_due_checks(scenario):
    scenario.write('上线',key='release',kind='plan',due_at=day(2))
    scenario.now=day(3)
    barrier=threading.Barrier(4)
    def tick(_):
        barrier.wait(timeout=10)
        return scenario.store.tick()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(tick,range(4)))
    processed=[(r['event_id'],r['trigger']) for batch in results for r in batch]
    assert len(processed)==len(set(processed))==2
    assert scenario.store.tick()==[]


def test_due_backlog_is_processed_in_bounded_batches(scenario):
    for batch in range(3):
        scenario.store.apply(Source(id=f'due-{batch}',content='需要记录的状态',occurred_at=day(1)),
            Extraction(memories=[MemoryChange(subject='主体',key=f'value-{batch}-{i}',kind='fact',
                text='一个状态',value=i,effective_at=day(1)) for i in range(40)]))
    first=scenario.store.tick();second=scenario.store.tick()
    assert len(first)==100 and len(second)==20
    assert len({(r['event_id'],r['trigger']) for r in first+second})==120
    assert scenario.store.tick()==[]


async def test_read_view_is_stable_when_a_revision_commits_during_recall(scenario,monkeypatch):
    store=scenario.store
    first=store.apply(Source(id='before',content='第一次经历',occurred_at=day(1)),Extraction(experiences=[story()]))
    original=store.keyword_matches
    changed=False
    def matches(*args,**kwargs):
        nonlocal changed
        if not changed:
            changed=True
            # Both writes deliberately have the same recorded_at. A timestamp
            # cutoff alone cannot exclude this newly committed version.
            store.apply(Source(id='after',content='后来更正',occurred_at=day(1)),
                        Extraction(experiences=[story('后来更正',first['record_ids'][0])]))
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'keyword_matches',matches)
    service=MemoryService(store,Settings())
    try:
        result=await service.context(Query(query='共同经历'))
        assert result['results'][0]['record_id']==first['record_ids'][0]
        assert store.records()[0]['record_id']!=first['record_ids'][0]
    finally:
        await service.provider.close()


async def test_slow_database_call_does_not_block_other_async_work(scenario,monkeypatch):
    entered=threading.Event();release=threading.Event()
    def slow_tick():
        entered.set()
        assert release.wait(timeout=2)
        return []
    monkeypatch.setattr(scenario.store,'tick',slow_tick)
    service=MemoryService(scenario.store,Settings())
    task=asyncio.create_task(MemoryTools(service).call('memory_tick',{}))
    try:
        assert await asyncio.to_thread(entered.wait,2)
        await asyncio.sleep(0)
        release.set()
        assert (await task)['ok']
    finally:
        release.set()
        await service.provider.close()
