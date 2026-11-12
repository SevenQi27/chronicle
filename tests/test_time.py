from datetime import datetime

import pytest

from chronicle_memory.models import Extraction, MemoryChange, ReviewCandidate, Source
from chronicle_memory.store import MemoryStore
from conftest import day


def test_late_message_preserves_historical_knowledge(scenario):
    s = scenario
    s.write()
    s.now=day(10)
    s.write("李四",effective=5)
    assert s.state(at=3)["memory"]["value"]=="张三"
    assert s.state(at=7)["memory"]["value"]=="李四"
    assert s.state(at=7,known=7)["memory"]["value"]=="张三"


def test_corrections_do_not_invent_changes_and_keep_future(scenario):
    s=scenario
    first=s.write()
    s.write("王五",effective=20)
    s.now=day(10)
    second=s.write("李四",operation="correct",corrects=first)
    assert s.state(at=3)["memory"]["value"]=="李四"
    assert s.state(at=3,known=3)["memory"]["value"]=="张三"
    assert s.state(at=21)["memory"]["value"]=="王五"
    s.now=day(12)
    with pytest.raises(ValueError,match="已被更正"):
        s.write("赵六",operation="correct",corrects=first)
    s.write("赵六",operation="correct",corrects=second)
    assert s.state(at=3,known=11)["memory"]["value"]=="李四"
    assert s.state(at=3)["memory"]["value"]=="赵六"


def test_correction_can_change_start_time(scenario):
    s=scenario
    s.write()
    s.now=day(10)
    wrong=s.write("李四",effective=5)
    s.now=day(12)
    s.write("李四",effective=8,operation="correct",corrects=wrong)
    assert s.state(at=6)["memory"]["value"]=="张三"
    assert s.state(at=6,known=11)["memory"]["value"]=="李四"


def test_future_rule_and_expiry_do_not_depend_on_worker(scenario):
    s=scenario
    s.write(100,key="price",kind="rule")
    s.write(150,key="price",kind="rule",effective=10,expires_at=day(20))
    assert s.state(at=9,key="price")["memory"]["value"]==100
    assert s.state(at=10,key="price")["memory"]["value"]==150
    assert s.state(at=20,key="price")["status"]=="expired"
    assert not any(c.get("processed_at") for c in s.store.checks()['items'])


def test_plan_is_not_completed_by_time_and_tick_is_idempotent(scenario):
    s=scenario
    s.write("上线",key="release-v1",kind="plan",due_at=day(5),review_at=day(3))
    assert s.state(key="release-v1")["status"]=="scheduled"
    s.now=day(3)
    assert s.state(key="release-v1")["status"]=="needs_review"
    s.store.tick()
    s.now=day(6)
    assert s.state(key="release-v1")["status"]=="awaiting_outcome"
    checks=s.store.tick()
    assert any(c["outcome"]=="awaiting_outcome" for c in checks)
    assert s.store.tick()==[]


@pytest.mark.parametrize("status",["completed","cancelled"])
def test_closed_plan_cancels_old_checks(scenario,status):
    s=scenario
    original=s.write("上线",key="release",kind="plan",due_at=day(10),review_at=day(5))
    s.now=day(3)
    s.write("上线",key="release",kind="plan",effective=3,plan_status=status,due_at=day(10))
    s.now=day(11)
    results=s.store.tick()
    assert all(r["outcome"]=="superseded" for r in results if r["event_id"]==original)
    assert s.state(key="release")["status"]==status
    assert s.state(at=2,key="release")["status"]=="scheduled"


def test_postponement_and_restart(scenario):
    s=scenario
    original=s.write("上线",key="release",kind="plan",due_at=day(5))
    s.now=day(3)
    s.write("上线",key="release",kind="plan",effective=3,due_at=day(15))
    s.now=day(10)
    with_store=MemoryStore(s.settings,s.namespace,clock=lambda:s.now)
    try:
        assert with_store.state("项目 A","release")["status"]=="scheduled"
        results=with_store.tick()
        assert all(r["outcome"]=="superseded" for r in results if r["event_id"]==original)
        assert with_store.tick()==[]
    finally:
        with_store.close()


def test_conflicting_states_are_not_silently_overwritten(scenario):
    s=scenario
    s.write()
    s.now=day(2)
    wrong=s.write("李四")
    assert s.state()["status"]=="conflict"
    assert "memory" not in s.state()
    s.now=day(3)
    s.write("张三",operation="correct",corrects=wrong)
    assert s.state()["status"]=="active"
    assert len(s.state()["candidates"][0]["evidence"])==2
    assert s.state(known=2)["status"]=="conflict"


def test_expired_candidate_does_not_override_live_evidence(scenario):
    s=scenario
    s.write("张三",expires_at=day(3))
    s.write("李四")
    assert s.state(at=2)["status"]=="conflict"
    assert s.state(at=3)["memory"]["value"]=="李四"


def test_source_id_is_idempotent_and_different_payload_is_rejected(scenario):
    s=scenario
    source=Source(id="retry",content="负责人张三",occurred_at=day(1))
    change=MemoryChange(subject="项目 A",key="owner",kind="fact",text="负责人张三",value="张三",effective_at=day(1))
    first=s.store.apply(source,Extraction(memories=[change]))
    s.now=day(10)
    assert first==s.store.apply(source,Extraction(memories=[change]))
    assert len(s.store.timeline("项目 A","owner")["events"])==1
    with pytest.raises(ValueError,match="另一份提取"):
        s.store.apply(source,Extraction(memories=[change.model_copy(update={"value":"李四"})]))
    with pytest.raises(ValueError,match="内容或时间"):
        s.store.receive(source.model_copy(update={"content":"另一条消息"}))


def test_batch_rollback_keeps_raw_source_but_no_partial_facts(scenario):
    s=scenario
    source=Source(id="batch",content="两条记忆",occurred_at=day(1))
    good=MemoryChange(subject="项目 A",key="owner",kind="fact",text="负责人张三",value="张三",effective_at=day(1))
    bad=good.model_copy(update={"operation":"correct","corrects":"missing"})
    with pytest.raises(ValueError):
        s.store.apply(source,Extraction(memories=[good,bad]))
    assert s.store.source("batch") is not None
    assert s.state()["status"]=="unknown"
    assert s.store.checks()['items']==[]
    s.store.apply(source,Extraction(memories=[good]))
    assert s.state()["status"]=="active"


def test_namespace_isolation_for_state_search_sources_and_correction(scenario):
    s=scenario
    eid=s.write()
    other=MemoryStore(s.settings,"other-"+s.namespace,clock=lambda:s.now)
    try:
        assert other.state("项目 A","owner")["status"]=="unknown"
        assert other.lexical("负责人")==[]
        assert other.details([eid])==[]
        assert other.source("message:1") is None
        with pytest.raises(ValueError):
            other.apply(Source(id="attempt",content="更正",occurred_at=day(1)),Extraction(memories=[
                MemoryChange(subject="项目 A",key="owner",kind="fact",text="更正",value="李四",effective_at=day(1),operation="correct",corrects=eid)]))
    finally:
        other._run('MATCH (n {namespace:$ns}) DETACH DELETE n',ns=other.namespace)
        other._run('MATCH (n:MemoryNamespace {name:$ns}) DELETE n',ns=other.namespace)
        other.close()


def test_timeline_details_and_timezone_boundary(scenario):
    s=scenario
    first=s.write()
    s.now=day(10)
    second=s.write("李四",effective=5)
    assert s.store.details([second],known_at=day(5))==[]
    assert len(s.store.timeline("项目 A","owner",known_at=day(5))["events"])==1
    detail=s.store.details([first])[0]
    assert detail["source"]["content"]=="项目 A 负责人是 张三"
    at=datetime.fromisoformat("2026-11-05T01:00:00+00:00")
    assert s.store.state("项目 A","owner",at=at)["memory"]["value"]=="李四"


def test_review_resolution_is_atomic_and_linked(scenario):
    s=scenario
    source=Source(id="unclear",content="过阵子可能换人",occurred_at=day(1))
    result=s.store.apply(source,Extraction(review=[ReviewCandidate(text=source.content,reason="日期与决定未明确")]))
    rid=result["review_ids"][0]
    assert s.state()["status"]=="unknown"
    s.now=day(3)
    confirmation=Source(id="clear",content="确认从今天起李四负责",occurred_at=s.now)
    change=MemoryChange(subject="项目 A",key="owner",kind="fact",text=confirmation.content,value="李四",effective_at=s.now)
    s.store.apply(confirmation,Extraction(memories=[change]),review_id=rid)
    assert s.store.reviews()['items']==[]
    assert s.state()["memory"]["value"]=="李四"
    assert s.store.details([s.state()["memory"]["event_id"]])[0]["source"]["id"]=="clear"


@pytest.mark.parametrize("overrides",[
    {"effective_at":"2026-11-01T09:00:00"},
    {"expires_at":day(1)},
    {"kind":"plan","due_at":None},
    {"kind":"plan","due_at":day(5),"expires_at":day(6)},
    {"plan_status":"completed"},
    {"due_at":day(5)},
    {"operation":"correct"},
    {"corrects":"unexpected"},
    {"review_at":datetime.fromisoformat("2026-10-01T00:00:00Z")},
])
def test_invalid_time_semantics_rejected(overrides):
    with pytest.raises(ValueError):
        MemoryChange(**{"subject":"A","key":"owner","kind":"fact","text":"x","value":"x","effective_at":day(1),**overrides})
