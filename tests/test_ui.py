import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from conftest import day
from chronicle_memory.models import Association, Entity, Experience, Extraction, Source, Understanding
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


def test_http_graph_returns_all_records_beyond_200_and_their_connections(scenario):
    s=scenario;record_ids=[]
    for batch in range(11):
        result=s.store.apply(Source(id=f'batch-{batch}',content='full graph acceptance',occurred_at=s.now),
            Extraction(experiences=[Experience(id=f'item-{batch*20+i}',title=f'item {batch*20+i}',narrative='retained record',
                entities=[Entity(id='shared-topic',name='shared topic')]) for i in range(20)]))
        record_ids.extend(result['record_ids'])
    s.store.apply(Source(id='cross-boundary-link',content='oldest and newest connected',occurred_at=s.now),
        Extraction(associations=[Association(id='first-last',from_id='item-0',to_id='item-219',kind='related_to',
            explanation='retain both ends',basis='explicit',evidence_ids=['item-0'])]))
    with make_server(s.store,0) as server:
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/graph') as response:
                graph=json.load(response)['result']
            records={n['record_id'] for n in graph['nodes'] if n.get('record_id')}
            assert records==set(record_ids) and graph['total_records']==220
            assert graph['limit'] is None and not graph['truncated']
            assert len([e for e in graph['edges'] if e['kind']=='EVIDENCED_BY'])==220
            assert len([e for e in graph['edges'] if e['kind']=='INVOLVES'])==220
            assert any(e['kind']=='RELATED_TO' and e['from']==s.store._uid(record_ids[0])
                       and e['to']==s.store._uid(record_ids[-1]) for e in graph['edges'])
        finally:
            server.shutdown();thread.join(timeout=5)
