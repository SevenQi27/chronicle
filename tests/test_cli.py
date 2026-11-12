import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

from conftest import day


CLI=str(Path(sys.executable).with_name('chronicle'))
ROOT=Path(__file__).resolve().parents[1]


def call(scenario,*args,body=None,raw=None):
    env=os.environ|{'MEMORY_LLM_MODEL':'','MEMORY_EMBEDDING_MODEL':''}
    result=subprocess.run([CLI,'--namespace',scenario.namespace,'--env-file',str(ROOT/'.env'),*args],
        input=json.dumps(body,ensure_ascii=False) if body is not None else raw,
        text=True,capture_output=True,env=env,timeout=30)
    return result,json.loads(result.stdout)


def payload(source='cli-1',replaces=None):
    experience={'id':'walk','title':'散步聊天','narrative':'和朋友散步聊书，感觉轻松。',
                'when':{'start':day(1).isoformat(),'precision':'day'}}
    if replaces:experience.update(replaces=replaces,narrative='补充新的认识',revision_kind='correct')
    return {'source':{'id':source,'content':experience['narrative'],'occurred_at':day(1).isoformat()},
            'experiences':[experience]}


def test_cli_remember_recall_show_history_and_conflict(scenario):
    process,written=call(scenario,'remember',body=payload())
    assert process.returncode==0 and written['ok']
    original=written['result']['record_ids'][0]
    process,found=call(scenario,'recall','散步聊天')
    assert process.returncode==0
    assert found['result']['results'][0]['record_id']==original
    assert 'revision' in found['result']
    assert call(scenario,'show',original)[1]['result'][0]['source']['id']=='cli-1'
    assert call(scenario,'history','--memory-id','walk')[1]['result']['records']
    winner=call(scenario,'remember',body=payload('cli-2',original))[1]['result']['record_ids'][0]
    process,conflict=call(scenario,'remember',body=payload('cli-3',original))
    assert process.returncode==3
    assert conflict['error']['code']=='version_conflict'
    assert conflict['error']['current_record_id']==winner


def test_cli_file_input_and_invalid_input(scenario,tmp_path):
    source=tmp_path/'input.json';source.write_text(json.dumps(payload(),ensure_ascii=False))
    assert call(scenario,'remember','--input',str(source))[0].returncode==0
    result,body=call(scenario,'remember',raw='{broken')
    assert result.returncode==2 and body['error']['code']=='invalid_arguments'
    result,body=call(scenario,'serve')
    assert result.returncode==2
    assert 'serve' in body['error']['message']
    assert 'Traceback' not in result.stderr


def test_two_cli_processes_share_idempotent_writes(scenario):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:call(scenario,'remember',body=payload('same-cli-message')),range(2)))
    assert all(process.returncode==0 for process,_ in results)
    assert results[0][1]['result']==results[1][1]['result']
    assert scenario.store.counts()=={'experience':1}


def test_generated_mcp_config_uses_the_same_namespace_as_cli(tmp_path):
    env_file=tmp_path/'.env';env_file.write_text('MEMORY_NAMESPACE=shared-space\n')
    env={k:v for k,v in os.environ.items() if k!='MEMORY_NAMESPACE'}
    result=subprocess.run([CLI,'config','claude','--env-file',str(env_file)],capture_output=True,text=True,env=env,check=True)
    entry=json.loads(result.stdout)['mcpServers']['chronicle']
    assert entry['args'][-2:]==['--namespace','shared-space']


def test_worker_is_a_separate_process_and_stops_cleanly(scenario):
    # Dates precede real wall time; a standalone worker must process them even
    # when no MCP session or recall command is running.
    scenario.write('一次安排',kind='plan',key='plan',due_at=day(2))
    env=os.environ|{'MEMORY_LLM_MODEL':'','MEMORY_EMBEDDING_MODEL':''}
    process=subprocess.Popen([CLI,'worker','--namespace',scenario.namespace,'--env-file',str(ROOT/'.env'),
                              '--interval','0.1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    try:
        ready,_,_=select.select([process.stdout],[],[],15)
        assert ready
        first=json.loads(process.stdout.readline())
        assert first['result']['processed']
        process.send_signal(signal.SIGTERM)
        stdout,stderr=process.communicate(timeout=15)
        assert process.returncode==0,stderr
        assert json.loads(stdout.splitlines()[-1])['result']['stopped']
        assert all(row.get('processed_at') for row in scenario.store.checks()['items'])
    finally:
        if process.poll() is None:
            process.kill();process.communicate(timeout=5)
