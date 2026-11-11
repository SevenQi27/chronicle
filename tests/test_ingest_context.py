from chronicle_memory.config import Settings
from chronicle_memory.models import Entity,Experience,Extraction,Source
from chronicle_memory.service import MemoryService
from conftest import day


class CaptureProvider:
    def __init__(self):self.existing=None
    async def extract(self,source,existing,previous):
        self.existing=existing
        return Extraction()
    async def close(self):pass


async def test_keyword_miss_recovers_bounded_recent_source_records(scenario,monkeypatch):
    s=scenario
    first=s.store.apply(Source(id='earlier',content='玄武岩观察',occurred_at=day(1),conversation_id='same'),
        Extraction(experiences=[Experience(id=f'account-{i}',title=f'basalt{i}',narrative='玄武岩观察记录') for i in range(20)]))
    s.store.apply(Source(id='other-conversation',content='另一个话题',occurred_at=day(1),conversation_id='other'),
        Extraction(experiences=[Experience(id='unrelated',title='unrelated',narrative='其他资料')]))
    source=Source(id='correction',content='更正上一条',occurred_at=day(1),conversation_id='same')
    assert s.store.lexical(source.content)==[]
    original=s.store._rows
    def bounded(*args,**kwargs):
        assert kwargs.get('logical_ids') is not None or len(args)>=3 and args[2] is not None
        return original(*args,**kwargs)
    monkeypatch.setattr(s.store,'_rows',bounded)
    provider=CaptureProvider();service=MemoryService(s.store,Settings(model='test'),provider)
    await service.ingest(source)
    assert len(provider.existing)==20
    assert {r['record_id'] for r in provider.existing}==set(first['record_ids'])


async def test_keyword_miss_recovers_entity_context_across_conversations(scenario):
    s=scenario
    first=s.store.apply(Source(id='old',content='旅行见闻',occurred_at=day(1),conversation_id='old'),
        Extraction(experiences=[Experience(id='trip',title='旅行见闻',narrative='旧的旅行经历',
            entities=[Entity(id='person42',name='小周',category='person')])]))
    source=Source(id='new',content='补充 person42 的一件事',occurred_at=day(1),conversation_id='new')
    assert s.store.lexical(source.content)==[]
    provider=CaptureProvider();service=MemoryService(s.store,Settings(model='test'),provider)
    await service.ingest(source)
    assert provider.existing[0]['record_id']==first['record_ids'][0]
