from conftest import day


def test_timeline_includes_clock_driven_transitions_without_new_messages(scenario):
    scenario.write("上线",key="release",kind="plan",due_at=day(5),review_at=day(3))
    history=scenario.store.timeline("项目 A","release")
    assert [s["status"] for s in history["states"]]==["scheduled","needs_review","awaiting_outcome"]
    assert len(history["events"])==1
    assert all("at" in state for state in history["states"])
