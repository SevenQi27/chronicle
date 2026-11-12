import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from neo4j.exceptions import ServiceUnavailable

from conftest import day
from chronicle_memory.capture import CaptureEvent,CaptureQueue,process_pending
from chronicle_memory.adapters.codex import capture_hook,install_hooks
from chronicle_memory.models import Source
from chronicle_memory.store import MemoryStore


def event(identifier='prompt',role='user',text='本次验收使用蓝色记录本',origin='another-agent',turn='turn-1'):
    return CaptureEvent(origin=origin,turn_id=turn,workspace='/example/project',
        source=Source(id=identifier,conversation_id='conversation-1',role=role,content=text,occurred_at=day(1)))


def factory(scenario):
    return lambda settings:MemoryStore(settings,scenario.namespace,clock=lambda:scenario.now)


def test_generic_capture_is_durable_idempotent_and_recallable(scenario,tmp_path):
    path=tmp_path/'capture.sqlite3';queue=CaptureQueue(path)
    accepted=queue.submit(event())
    assert accepted['status']=='queued'
    assert queue.submit(event())['duplicate']
    # A different process can reopen the queue and finish its outstanding work.
    queue=CaptureQueue(path)
    result=process_pending(queue,scenario.settings,store_factory=factory(scenario))
    assert result=={'saved':1,'failed':0}
    assert queue.status()['pending']==0
    records=scenario.store.records()
    assert len(records)==1 and '蓝色记录本' in records[0]['narrative']
    assert scenario.store.lexical('蓝色记录本')
    assert queue.status()['recent'][0]['record_id']==records[0]['record_id']
    assert process_pending(queue,scenario.settings,store_factory=factory(scenario))['saved']==0
    assert scenario.store.counts()=={'experience':1}


def test_turn_archive_retains_roles_and_does_not_infer_assistant_facts(scenario,tmp_path):
    queue=CaptureQueue(tmp_path/'queue.sqlite3')
    queue.submit(event())
    process_pending(queue,scenario.settings,store_factory=factory(scenario))
    old=scenario.store.records()[0]['record_id']
    scenario.now=day(2)
    queue.submit(event('answer','assistant','我推测用户已经买了红色记录本。'))
    process_pending(queue,scenario.settings,store_factory=factory(scenario))
    records=scenario.store.records()
    assert len(records)==1 and records[0]['record_id']!=old
    assert '用户：' in records[0]['narrative'] and '助手（未经用户确认）' in records[0]['narrative']
    assert records[0]['narrative'].startswith('自动采集的对话记录')
    assert scenario.store.states()==[]
    assert scenario.store.counts()=={'experience':2}
    detail=scenario.store.details([records[0]['record_id']])[0]
    assert {'user','assistant','tool'}=={s['role'] for s in detail['sources']}
    assert len(scenario.store.history(memory_id=records[0]['id'])['records'])==2


def test_capture_retries_outage_without_advancing_saved_status(scenario,tmp_path):
    queue=CaptureQueue(tmp_path/'queue.sqlite3');queue.submit(event())
    def unavailable(_):raise ServiceUnavailable('example secret should not appear in status')
    result=process_pending(queue,scenario.settings,store_factory=unavailable)
    assert result=={'saved':0,'failed':1}
    status=queue.status()
    assert status['pending']==1 and status['recent'][0]['attempts']==1
    assert status['recent'][0]['saved_at'] is None
    assert 'secret' not in json.dumps(status)
    assert not queue.ready()
    assert process_pending(queue,scenario.settings,now=10**12,store_factory=factory(scenario))['saved']==1
    assert queue.status()['recent'][0]['last_error'] is None


def test_replay_after_database_commit_does_not_make_another_revision(scenario,tmp_path,monkeypatch):
    queue=CaptureQueue(tmp_path/'queue.sqlite3');queue.submit(event())
    done=queue.done
    def fail_once(*_):raise OSError('simulated crash before queue acknowledgement')
    monkeypatch.setattr(queue,'done',fail_once)
    assert process_pending(queue,scenario.settings,store_factory=factory(scenario))['failed']==1
    assert scenario.store.counts()=={'experience':1}
    monkeypatch.setattr(queue,'done',done)
    assert process_pending(queue,scenario.settings,now=10**12,store_factory=factory(scenario))['saved']==1
    assert scenario.store.counts()=={'experience':1}


def test_capture_privacy_redaction_and_turn_opt_out(scenario,tmp_path):
    queue=CaptureQueue(tmp_path/'queue.sqlite3')
    queue.submit(event(text='保留这一段 <private>不要保留的片段</private> API_KEY=sk-notarealcredential1234567890'))
    process_pending(queue,scenario.settings,store_factory=factory(scenario))
    text=scenario.store.records()[0]['narrative']
    assert '保留这一段' in text and '不要保留的片段' not in text and 'sk-notareal' not in text
    queue.submit(event('private',text='不要记录：这是一个私密话题',turn='private-turn'))
    queue.submit(event('private-answer',role='assistant',text='不应保存的回答',turn='private-turn'))
    assert queue.status()['counts']['private']==2
    assert queue.ready()==[]
    with queue.connect() as db:
        assert all(r['body'] is None for r in db.execute("SELECT body FROM events WHERE status='private'"))


def test_capture_rejects_changed_content_for_same_event_and_locks_workers(tmp_path):
    queue=CaptureQueue(tmp_path/'queue.sqlite3');queue.submit(event())
    with pytest.raises(ValueError,match='different content'):queue.submit(event(text='different'))
    with queue.worker_lock():
        with pytest.raises(ValueError,match='already running'):
            with queue.worker_lock():pass


def test_codex_adapter_uses_native_events_without_reading_transcript(tmp_path):
    queue=CaptureQueue(tmp_path/'queue.sqlite3')
    transcript=tmp_path/'session.jsonl'
    transcript.write_text(json.dumps({'type':'session_meta','payload':{'id':'desktop-session','thread_source':'user'}})+'\n')
    payload={'hook_event_name':'UserPromptSubmit','session_id':'desktop-session','turn_id':'desktop-turn',
             'prompt':'桌面端原生事件','cwd':'/desktop/project','transcript_path':str(transcript)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:capture_hook(payload,queue),range(4)))
    assert sum(not r['duplicate'] for r in results)==1
    capture_hook({**payload,'hook_event_name':'Stop','last_assistant_message':'最终回答'},queue)
    assert queue.status()['counts']=={'queued':2}
    rows=queue.ready();events=queue.turn(rows[0]['turn_key'])
    assert [e.source.role for e in events]==['user','assistant']
    assert all(e.origin=='codex' for e in events)
    assert capture_hook({'hook_event_name':'PreToolUse'},queue)['skipped']=='unsupported_event'
    with pytest.raises(ValueError,match='turn_id'):capture_hook({'hook_event_name':'Stop','session_id':'missing'},queue)


def test_codex_hook_install_preserves_other_hooks_and_does_not_bypass_trust(tmp_path):
    home=tmp_path/'codex';home.mkdir()
    original={'description':'existing','hooks':{'PreToolUse':[{'matcher':'Bash','hooks':[{'type':'command','command':'existing-guard'}]}]}}
    path=home/'hooks.json';path.write_text(json.dumps(original))
    config=home/'config.toml';config.write_text('[features]\nhooks = true\n')
    result=install_hooks(home,tmp_path/'queue.sqlite3')
    assert result['requires_hook_trust'] is True
    once=path.read_text();install_hooks(home,tmp_path/'queue.sqlite3')
    assert once==path.read_text()
    loaded=json.loads(once)
    assert loaded['hooks']['PreToolUse']==original['hooks']['PreToolUse']
    assert set(loaded['hooks'])=={'PreToolUse','UserPromptSubmit','Stop'}
    assert 'trusted_hash' not in once
    assert config.read_text()=='[features]\nhooks = true\n'


def test_codex_skips_background_and_unidentified_sessions(tmp_path):
    queue=CaptureQueue(tmp_path/'queue.sqlite3')
    payload={'hook_event_name':'UserPromptSubmit','session_id':'background','turn_id':'turn','prompt':'Generated background work'}
    assert capture_hook(payload,queue)['skipped']=='not_a_verified_user_session'
    path=tmp_path/'background.jsonl'
    path.write_text(json.dumps({'type':'session_meta','payload':{'id':'background','thread_source':'memory'}})+'\n')
    assert capture_hook({**payload,'transcript_path':str(path)},queue)['skipped']=='not_a_verified_user_session'
    assert queue.status()['counts']=={}


def test_codex_stop_recovers_only_current_turn_prompt_after_activation(tmp_path):
    queue=CaptureQueue(tmp_path/'queue.sqlite3')
    queue.path.with_suffix('.codex.json').write_text(json.dumps({'enabled_at':day(2).isoformat()}))
    path=tmp_path/'session.jsonl'
    def user(turn,text,when):
        return {'type':'event_msg','timestamp':when.isoformat(),'payload':{'type':'item_completed',
            'thread_id':'desktop','turn_id':turn,'item':{'type':'UserMessage','content':[{'type':'text','text':text}]}}}
    records=[{'type':'session_meta','payload':{'id':'desktop','thread_source':'user'}},
        {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'injected AGENTS rules'}]}},
        user('old','old conversation',day(3)),user('target','before activation',day(1)),
        user('target','actual first prompt\n',day(3))]
    path.write_text(''.join(json.dumps(r)+'\n' for r in records))
    payload={'hook_event_name':'Stop','session_id':'desktop','turn_id':'target','last_assistant_message':'final answer','transcript_path':str(path)}
    result=capture_hook(payload,queue)
    assert result['recovered_prompts']==1
    rows=queue.ready();events=queue.turn(rows[0]['turn_key'])
    assert [e.source.content for e in events]==['actual first prompt','final answer']
    assert capture_hook(payload,queue)['duplicate']
    assert queue.status()['counts']=={'queued':2}


def test_capture_cli_submission_and_worker_acknowledgement(scenario,tmp_path):
    import subprocess,sys
    cli=str(Path(sys.executable).with_name('chronicle'))
    queue=tmp_path/'cli.sqlite3'
    root=Path(__file__).resolve().parents[1]
    def call(*args,body=None):
        result=subprocess.run([cli,'capture',*args,'--queue',str(queue),'--env-file',str(root/'.env'),
             '--namespace',scenario.namespace],input=body,text=True,capture_output=True,timeout=20)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    submitted=call('submit',body=event().model_dump_json())
    assert submitted['result']['status']=='queued'
    assert call('status')['result']['pending']==1
    assert call('worker','--once')['result']=={'saved':1,'failed':0}
    assert call('status')['result']['counts']=={'saved':1}
