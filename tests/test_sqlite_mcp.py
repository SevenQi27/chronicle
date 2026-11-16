from datetime import datetime
from pathlib import Path
import subprocess
import sys

import pytest

from chronicle_memory import cli
from chronicle_memory.sqlite_mcp import ExperienceWrite, RecallQuery, SQLiteExperiences

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
