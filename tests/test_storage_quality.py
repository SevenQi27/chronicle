"""Observable storage/read budgets; no wall-clock thresholds or external models."""
from datetime import timedelta
import hashlib
import json

import pytest

from conftest import day
from chronicle_memory.capture import CaptureEvent,archive_turn,digest
from chronicle_memory.errors import ConflictError
from chronicle_memory.models import Entity,Experience,ExperienceTime,Extraction,Source,Understanding
from chronicle_memory.service import MemoryService
from chronicle_memory.tools import MemoryTools


def test_current_reads_do_not_return_every_revision(scenario,monkeypatch):
    s=scenario;head=None;versions=[]
    for i in range(80):
        s.now=day(1)+timedelta(seconds=i)
        head=s.store.apply(Source(id=f'v-{i}',content=f'evidence {i}',occurred_at=s.now),Extraction(experiences=[
            Experience(id='history',title='History',narrative='Long retained narrative. '*100,
                       replaces=head)]))['record_ids'][0]
        versions.append(head)
    rows=[];original=s.store._run
    def trace(*args,**kwargs):
        result=original(*args,**kwargs);rows.extend(result);return result
    monkeypatch.setattr(s.store,'_run',trace)
    current=s.store.records(logical_ids=['history'])
    assert current[0]['record_id']==head and len(rows)==2
    rows.clear()
    past=s.store.records(logical_ids=['history'],known_at=day(1)+timedelta(seconds=10))
    assert past[0]['record_id']==versions[10] and len(rows)==2
    # The history endpoint still exposes the old revisions; none were deleted.
    assert len(s.store.history(memory_id='history',limit=100)['undated'])==80


def test_head_selection_preserves_correction_future_and_expiry_semantics(scenario):
    s=scenario
    seed=s.store.apply(Source(id='seed',content='seed',occurred_at=s.now),Extraction(
        experiences=[Experience(id='experience',title='old',narrative='old')],
        understandings=[Understanding(id='belief',title='old belief',statement='old belief',basis='inferred',supports=['experience'],valid_from=day(1))]))['record_ids']
    s.now=day(5)
    future=s.store.apply(Source(id='future',content='future',occurred_at=s.now),Extraction(understandings=[
        Understanding(id='belief',title='future belief',statement='future belief',basis='inferred',supports=[seed[0]],
                      replaces=seed[1],valid_from=day(10),valid_to=day(12))]))['record_ids'][0]
    assert s.store.records(at=day(7),logical_ids=['belief'])[0]['record_id']==seed[1]
    assert s.store.records(at=day(11),logical_ids=['belief'])[0]['record_id']==future
    assert s.store.records(at=day(12),logical_ids=['belief'])==[]
    assert s.store.records(at=day(12),logical_ids=['belief'],include_inactive=True)[0]['status']=='expired'
    s.now=day(6)
    corrected=s.store.apply(Source(id='corrected',content='corrected',occurred_at=s.now),Extraction(understandings=[
        Understanding(id='belief',title='corrected belief',statement='corrected belief',basis='inferred',supports=[seed[0]],
            replaces=future,revision_kind='correct',valid_from=day(15))]))['record_ids'][0]
    assert s.store.records(at=day(11),logical_ids=['belief'])[0]['record_id']==seed[1]
    assert s.store.records(at=day(16),logical_ids=['belief'])[0]['record_id']==corrected
    assert s.store.records(at=day(11),known_at=day(5),logical_ids=['belief'])[0]['record_id']==future
    changed=s.store.apply(Source(id='move-event',content='move-event',occurred_at=s.now),Extraction(experiences=[
        Experience(id='experience',title='moved',narrative='moved',replaces=seed[0],revision_kind='correct',
                   when=ExperienceTime(start=day(20),precision='day'))]))['record_ids'][0]
    assert s.store.records(at=day(10),logical_ids=['experience'])==[]
    assert s.store.records(at=day(20),logical_ids=['experience'])[0]['record_id']==changed


def test_applied_retry_is_read_only_but_still_validates_payload(scenario,monkeypatch):
    s=scenario;source=Source(id='retry',content='original',occurred_at=s.now)
    extraction=Extraction(experiences=[Experience(id='item',title='item',narrative='original')])
    first=s.store.apply(source,extraction)
    def forbidden(*args,**kwargs):raise AssertionError('Applied retries must not enter a write transaction')
    monkeypatch.setattr(s.store,'_write',forbidden)
    assert s.store.apply(source,extraction)==first
    with pytest.raises(ConflictError):s.store.apply(source.model_copy(update={'content':'changed'}),extraction)
    with pytest.raises(ConflictError):s.store.apply(source,Extraction(experiences=[Experience(id='item',title='item',narrative='changed')]))


async def test_source_summary_is_opt_in_and_preserves_evidence_inventory(scenario):
    s=scenario
    record=s.store.apply(Source(id='large',content='中文😀正文'*3000,occurred_at=s.now),Extraction(experiences=[
        Experience(id='story',title='story',narrative='完整记忆正文')]))['record_ids'][0]
    full=s.store.details([record])[0]
    compact=s.store.details([record],source_mode='summary')[0]
    assert full['source_mode']=='full' and 'content' in full['source']
    assert compact['narrative']==full['narrative']
    assert {x['id'] for x in compact['sources']}=={x['id'] for x in full['sources']}
    assert compact['source_count']==full['source_count']==1
    assert compact['source']['content_omitted'] is True and 'content' not in compact['source']
    assert compact['source']['content_chars']==len(full['source']['content'])
    assert len(json.dumps(compact,ensure_ascii=False))<len(json.dumps(full,ensure_ascii=False))/10
    service=MemoryService(s.store,s.settings)
    try:
        result=await MemoryTools(service).call('memory_details',{'event_ids':[record],'source_mode':'summary'})
        assert result['ok'] and result['result'][0]['source']['content_omitted']
    finally:await service.provider.close()


def test_capture_manifests_keep_original_messages_without_snapshot_copies(scenario):
    s=scenario;events=[]
    for i in range(6):
        events.append(CaptureEvent(origin='test',turn_id='turn',source=Source(id=f'm-{i}',conversation_id='conv',
            role='user' if i%2==0 else 'assistant',content=f'message {i} '+('独立原始来源。'*100),occurred_at=s.now)))
        head=archive_turn(s.store,'capture-turn:test',events)
    detail=s.store.details([head])[0];manifest=json.loads(detail['source']['content'])
    assert manifest['format']=='source-references-v1'
    assert len(manifest['messages'])==len(events)
    sources={source['id']:source for source in detail['sources']}
    for event,reference in zip(events,manifest['messages']):
        assert 'content' not in reference
        source=sources[reference['source_id']]
        assert source['content']==event.source.content and source['role']==event.source.role
        assert reference['content_sha256']==hashlib.sha256(source['content'].encode()).hexdigest()
    assert len(s.store.history(memory_id='capture-turn:test')['records'])==6
    before=s.store.counts()
    assert archive_turn(s.store,'capture-turn:test',events)==head
    assert s.store.counts()==before


def test_pending_legacy_capture_snapshot_is_retried_without_rewriting_source(scenario):
    s=scenario
    event=CaptureEvent(origin='legacy',turn_id='turn',source=Source(id='message',conversation_id='conv',role='user',content='old saved source',occurred_at=s.now))
    envelope={'capture_kind':'conversation_record','origin':'legacy','conversation_id':'conv','turn_id':'turn','messages':[event.model_dump(mode='json')]}
    source=Source(id='capture-snapshot:'+digest(envelope),content=json.dumps(envelope,ensure_ascii=False,sort_keys=True),role='tool',occurred_at=s.now,conversation_id='legacy:conv')
    s.store.receive(source)
    head=archive_turn(s.store,'capture-turn:legacy',[event])
    assert s.store.details([head])[0]['source']['content']==source.content
    assert archive_turn(s.store,'capture-turn:legacy',[event])==head
