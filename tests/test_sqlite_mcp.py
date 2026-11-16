from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from chronicle_memory import cli
from chronicle_memory.sqlite_mcp import SCHEMA, ExperienceWrite, RecallQuery, SQLiteExperiences

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
