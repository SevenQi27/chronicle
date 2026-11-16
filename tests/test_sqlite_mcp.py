from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from chronicle_memory import cli
from chronicle_memory.models import DetailQuery, VisibilityRequest
from chronicle_memory.sqlite_mcp import SCHEMA, ExperienceWrite, RecallQuery, SQLiteExperiences
from chronicle_memory.ui import graph_snapshot, make_server

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_stdio_trial():
    subprocess.run([sys.executable, str(ROOT / 'scripts/smoke_sqlite_mcp.py')],
                   cwd=ROOT, check=True, timeout=45)


@pytest.mark.parametrize('sqlite_option', [False, True])
def test_mcp_path_defaults_and_override(tmp_path, monkeypatch, sqlite_option):
    async def serve(path, namespace):
        expected = tmp_path / 'chosen.sqlite3' if sqlite_option else tmp_path / '.local/share/chronicle/memory.sqlite3'
        assert (path, namespace) == (expected.resolve(), 'local')

    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(cli, 'serve', serve)
    args = ['chronicle', 'mcp', '--namespace', 'local']
    if sqlite_option:
        args.extend(['--sqlite', str(tmp_path / 'chosen.sqlite3')])
    monkeypatch.setattr(sys, 'argv', args)
    cli.main()


def test_ui_uses_effective_mcp_path_and_namespace(tmp_path, monkeypatch):
    def codex_lookup(command, **kwargs):
        assert command == ['codex', 'mcp', 'get', 'chronicle', '--json']
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
            'enabled': True,
            'transport': {'type': 'stdio', 'args': ['mcp', '--sqlite', 'db.sqlite3', '--namespace=earnbyai'],
                          'cwd': str(tmp_path)},
        }))

    started = []
    monkeypatch.setattr(cli.subprocess, 'run', codex_lookup)
    monkeypatch.setattr(cli, 'serve_ui', lambda path, namespace, port: started.append((path, namespace, port)))
    monkeypatch.setattr(sys, 'argv', ['chronicle', 'ui'])
    cli.main()
    monkeypatch.setattr(sys, 'argv', ['chronicle', 'ui', '--namespace', 'personal'])
    cli.main()
    assert started == [(tmp_path / 'db.sqlite3', 'earnbyai', 8765), (tmp_path / 'db.sqlite3', 'personal', 8765)]


def test_ui_explicit_connection_skips_config_lookup(tmp_path, monkeypatch):
    def fail_if_read():
        pytest.fail('显式指定库和分区时无需读取 MCP 配置')

    started = []
    path = tmp_path / 'other.sqlite3'
    monkeypatch.setattr(cli, 'mcp_transport', fail_if_read)
    monkeypatch.setattr(cli, 'serve_ui', lambda path, namespace, port: started.append((path, namespace, port)))
    monkeypatch.setattr(sys, 'argv', ['chronicle', 'ui', '--sqlite', str(path), '--namespace', 'other'])
    cli.main()
    assert started == [(path, 'other', 8765)]


def test_existing_database_indexes_both_versions(tmp_path):
    path = tmp_path / 'memory.sqlite3'
    store = SQLiteExperiences(path, 'test')

    def write(source_id, room, **version):
        return store.write(ExperienceWrite.model_validate({
            'source': {'id': source_id, 'content': room, 'occurred_at': '2026-11-16T00:00:00Z'},
            'experiences': [{'id': 'meeting', 'title': '极光评审', 'narrative': room, **version}],
        }))

    first = write('original', '地点在三楼')
    second = write('correction', '地点在五楼', replaces=first['record_ids'][0], revision_kind='correct')
    with store.connection() as conn:
        conn.execute('DROP TRIGGER chronicle_experience_fts_insert')
        conn.execute('DROP TABLE chronicle_experience_fts')

    store = SQLiteExperiences(path, 'test')
    current = store.recall(RecallQuery(query='五楼'))['results']
    historical = store.recall(RecallQuery(query='三楼', known_at=datetime.fromisoformat(first['recorded_at'])))['results']
    assert [item['record_id'] for item in current] == second['record_ids']
    assert [item['record_id'] for item in historical] == first['record_ids']
    assert store.recall(RecallQuery(query='三楼'))['results'] == []


def test_understanding_cannot_reference_another_namespace(tmp_path):
    path = tmp_path / 'memory.sqlite3'
    foreign = SQLiteExperiences(path, 'other')
    external = foreign.write(ExperienceWrite.model_validate({
        'source': {'id': 'original', 'content': '项目开会', 'occurred_at': '2026-11-16T00:00:00Z'},
        'experiences': [{'id': 'meeting', 'title': '项目会议', 'narrative': '讨论会议安排'}],
    }))
    local = SQLiteExperiences(path, 'local')
    for reference in [external['record_ids'][0], 'meeting']:
        request = ExperienceWrite.model_validate({
            'source': {'id': 'conclusion', 'content': '明确议程', 'occurred_at': '2026-11-16T00:00:00Z'},
            'understandings': [{'id': 'insight', 'title': '会议议程', 'statement': '开会前先确认议程',
                               'basis': 'inferred', 'supports': [reference]}],
        })
        with pytest.raises(ValueError, match='supports 必须引用当前分区中已有的经历'):
            local.write(request)
    with local.connection() as conn:
        assert conn.execute("SELECT id FROM chronicle_source WHERE namespace='local'").fetchall() == []
    assert local.recall(RecallQuery(query='会议议程'))['results'] == []


def test_withdrawal_hides_new_versions_and_preserves_shared_evidence(tmp_path):
    store = SQLiteExperiences(tmp_path / 'memory.sqlite3', 'test')

    def write(source_id, logical_id, title, **fields):
        result = store.write(ExperienceWrite.model_validate({
            'source': {'id': source_id, 'content': title, 'occurred_at': '2026-11-16T00:00:00Z'},
            'experiences': [{'id': logical_id, 'title': title, 'narrative': title, **fields}],
        }))
        return result['record_ids'][0]

    first = write('shared-source', 'plan', '会议安排')
    other = write('other-source', 'notes', '会议纪要', source_ids=['shared-source'])
    store.visibility(VisibilityRequest(action='withdraw', memory_ids=[first]))
    revision = write('revision-source', 'plan', '会议安排已更新', replaces=first)

    assert store.details(DetailQuery(event_ids=[first, revision])) == []
    assert all(item['withdrawn'] for item in store.details(DetailQuery(
        event_ids=[first, revision], include_withdrawn=True,
    )))
    assert [item['record_id'] for item in store.recall(RecallQuery(query='会议'))['results']] == [other]
    graph = graph_snapshot(store)
    graph_ids = {node['id'] for node in graph['nodes']}
    assert graph_ids.isdisjoint({first, revision})
    assert {other, 'source:shared-source'} <= graph_ids
    assert 'source:revision-source' not in graph_ids

    store.visibility(VisibilityRequest(action='restore', memory_ids=['plan']))
    recovered = store.recall(RecallQuery(query='会议安排', subject='plan'))['results']
    assert [item['record_id'] for item in recovered] == [revision]
    restored = store.details(DetailQuery(event_ids=[first, revision]))
    assert [item['source']['content'] for item in restored] == ['会议安排', '会议安排已更新']
    assert not any(item['withdrawn'] for item in restored)


@pytest.mark.parametrize('expiry', [
    '2026-11-16T09:00:00+08:00',
    '2026-11-16T01:00:00Z',
    '2026-11-15T20:00:00-05:00',
    None,
])
def test_migrated_understanding_expiry_respects_the_same_instant(tmp_path, expiry):
    path = tmp_path / 'old.sqlite3'
    body = json.dumps({
        'id': 'insight', 'title': '会议安排', 'statement': '议题尚未确认',
        'basis': 'inferred', 'valid_to': expiry,
    }, ensure_ascii=False)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("ALTER TABLE chronicle_experience ADD COLUMN record_kind TEXT NOT NULL DEFAULT 'experience'")
        conn.execute("INSERT INTO chronicle_source VALUES ('test','s1','{}','{}','{}')")
        conn.execute(
            "INSERT INTO chronicle_experience(namespace, record_id, logical_id, source_id, recorded_at, "
            "body, terms, conversation_id, evidence_ids, record_kind) "
            "VALUES ('test','version-1','insight','s1','2026-11-15T00:00:00.000000+00:00',"
            "?,?,'default','[\"s1\"]','understanding')",
            (body, json.dumps(['会议', '安排'])),
        )

    store = SQLiteExperiences(path, 'test')
    for at, visible in [
        ('2026-11-16T00:59:59.999999+00:00', True),
        ('2026-11-16T01:00:00+00:00', expiry is None),
        ('2026-11-16T01:00:00.000001+00:00', expiry is None),
    ]:
        results = store.recall(RecallQuery(query='会议安排', at=datetime.fromisoformat(at)))['results']
        assert [r['record_id'] for r in results] == (['version-1'] if visible else [])

    reopened = SQLiteExperiences(path, 'test')
    with reopened.connection() as conn:
        row = conn.execute("SELECT valid_to, body FROM chronicle_experience WHERE record_id='version-1'").fetchone()
        assert row['valid_to'] == ('2026-11-16T01:00:00.000000+00:00' if expiry else None)
        assert row['body'] == body


def test_graph_ui_and_http_server(tmp_path):
    path = tmp_path / 'ui.sqlite3'
    store = SQLiteExperiences(path, 'personal')

    first = store.write(ExperienceWrite.model_validate({
        'source': {'id': 'msg-1', 'content': '极光评审定在三楼', 'occurred_at': '2026-11-12T08:00:00Z',
                   'conversation_id': 'conv-1'},
        'context': {'workspace': '/repo/aurora', 'origin': 'codex'},
        'experiences': [{
            'id': 'aurora-meeting', 'title': '极光评审安排', 'narrative': '极光评审在三楼会议室。',
            'entities': [{'id': 'aurora', 'name': '极光评审', 'aliases': ['北辰碰头']}],
        }],
    }))
    second = store.write(ExperienceWrite.model_validate({
        'source': {'id': 'msg-2', 'content': '更正：改到五楼', 'occurred_at': '2026-11-16T08:00:00Z',
                   'conversation_id': 'conv-1'},
        'context': {'workspace': '/repo/aurora', 'origin': 'codex'},
        'experiences': [{
            'id': 'aurora-meeting', 'title': '极光评审变更', 'narrative': '极光评审改在五楼会议室。',
            'replaces': first['record_ids'][0], 'revision_kind': 'correct',
            'entities': [{'id': 'aurora', 'name': '极光评审', 'aliases': ['北辰碰头']}],
        }],
    }))

    with make_server(store, 0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            for route, mime in (('/', 'text/html'), ('/graph/app.js', 'text/javascript'), ('/graph/style.css', 'text/css')):
                with urlopen(base + route) as resp:
                    assert resp.status == 200
                    assert resp.headers['Content-Type'].startswith(mime)
                    assert resp.read()
            with urlopen(base + '/') as resp:
                assert b'<canvas id="graph"' in resp.read()
            for removed in ('/ledger', '/api/ledger', '/app.js', '/style.css'):
                with pytest.raises(HTTPError) as exc:
                    urlopen(base + removed)
                assert exc.value.code == 404
            with urlopen(base + '/api/graph') as resp:
                data = json.load(resp)
                assert data['ok'] is True
                graph = data['result']
                assert graph['namespace'] == 'personal'
                assert graph['total_records'] == 2 and not graph['truncated']
                assert {first['record_ids'][0], second['record_ids'][0]} <= {node['id'] for node in graph['nodes']}
                assert {'from': second['record_ids'][0], 'to': first['record_ids'][0], 'kind': 'CORRECTS'} in graph['edges']
                assert {'from': first['record_ids'][0], 'to': 'source:msg-1', 'kind': 'EVIDENCED_BY'} in graph['edges']
                entity = next(node for node in graph['nodes'] if node['id'] == 'entity:aurora')
                assert entity['payload']['aliases'] == ['北辰碰头']
            with pytest.raises(HTTPError) as exc:
                urlopen(Request(base + '/api/graph', headers={'Host': 'evil.example'}))
            assert exc.value.code == 403
        finally:
            server.shutdown()
            thread.join(timeout=5)
