import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from conftest import day
from chronicle_memory.models import Entity, Experience, Extraction, Source, Understanding
from chronicle_memory.store import MemoryStore
from chronicle_memory.ui import graph_snapshot, make_server


def test_graph_preserves_revision_evidence_and_namespace(scenario):
    source=Source(id='first',content='<script>not executable</script>',occurred_at=day(1))
    first=scenario.store.apply(source,Extraction(
        experiences=[Experience(id='walk',title='散步',narrative='与小林聊书',
            entities=[Entity(id='lin',name='小林',category='person')])],
        understandings=[Understanding(id='insight',title='认识',statement='这次交流愉快',basis='inferred',supports=['walk'])],
    ))['record_ids'][0]
    scenario.now=day(2)
    second=scenario.store.apply(Source(id='second',content='更正',occurred_at=day(2)),Extraction(
        experiences=[Experience(id='walk',title='更正散步记录',narrative='补充原始背景',replaces=first,revision_kind='correct')]
    ))['record_ids'][0]
    before=scenario.store.counts()
    graph=graph_snapshot(scenario.store)
    assert graph['total_records']==3 and not graph['truncated']
    by_id={n['id']:n for n in graph['nodes']}
    assert any(e['kind']=='CORRECTS' and by_id[e['from']]['record_id']==second
               and by_id[e['to']]['record_id']==first for e in graph['edges'])
    assert any(e['kind']=='SUPPORTED_BY' and by_id[e['to']]['record_id']==first for e in graph['edges'])
    assert any(n['kind']=='source' and n['text']==source.content for n in graph['nodes'])
    assert any(n['kind']=='entity' and n['title']=='小林' for n in graph['nodes'])
    assert all(n['id'].startswith(scenario.namespace+'::') for n in graph['nodes'])
    assert scenario.store.counts()==before
    other=MemoryStore(scenario.settings,scenario.namespace+'-empty')
    try:
        assert graph_snapshot(other)['nodes']==[]
    finally:
        other.close()
    limited=graph_snapshot(scenario.store,limit=1)
    assert limited['truncated'] and limited['total_records']==3
    assert sum(n['kind'] in ('experience','understanding','state') for n in limited['nodes'])==1
    ids={n['id'] for n in limited['nodes']}
    assert all(e['from'] in ids and e['to'] in ids for e in limited['edges'])


def test_graph_includes_exact_state(scenario):
    event=scenario.write('张三')
    graph=graph_snapshot(scenario.store)
    record=next(n for n in graph['nodes'] if n['kind']=='state')
    assert record['record_id']==event
    assert record['payload']['value']=='张三'
    assert record['title']=='项目 A · owner'


def test_http_is_local_read_only_and_serves_packaged_assets(scenario):
    with make_server(scenario.store,0) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            for path,expected in (('/','text/html'),('/app.js','text/javascript'),('/style.css','text/css')):
                with urlopen(base+path) as response:
                    assert response.status==200
                    assert response.headers['Content-Type'].startswith(expected)
                    assert response.read()
                    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
            with urlopen(base+'/api/graph') as response:
                assert json.load(response)['result']['namespace']==scenario.namespace
                assert response.headers['Access-Control-Allow-Origin'] is None
            for request,status in ((Request(base+'/api/graph',headers={'Host':'evil.example'}),403),
                                   (Request(base+'/.env'),404),(Request(base+'/api/graph',method='POST',data=b'{}'),501)):
                with pytest.raises(HTTPError) as error:urlopen(request)
                assert error.value.code==status
            assert scenario.store.counts()=={}
        finally:
            server.shutdown();thread.join(timeout=5)
