from datetime import datetime
import json
from threading import Thread
from urllib.parse import urlencode
from urllib.request import urlopen

from chronicle_memory.sqlite_mcp import RecallQuery, SQLiteExperiences, tokens
from chronicle_memory.ui import make_server


def test_ledger_history_matches_mcp_at_expiry(tmp_path):
    store = SQLiteExperiences(tmp_path / 'ledger.sqlite3', 'test')
    body = json.dumps({
        'id': 'insight', 'title': '会议安排', 'statement': '议题尚未确认',
        'basis': 'inferred', 'valid_to': '2026-11-16T09:00:00+08:00',
    }, ensure_ascii=False)
    source = json.dumps({'id': 's1', 'content': '原始来源', 'role': 'tool',
                         'occurred_at': '2026-11-16T00:30:00Z'})
    expires_utc = '2026-11-16T01:00:00.000000+00:00'
    with store.connection() as conn:
        conn.execute("INSERT INTO chronicle_source VALUES ('test','s1',?,'{}','{}')", (source,))
        conn.executemany(
            "INSERT INTO chronicle_experience(namespace,record_id,logical_id,source_id,recorded_at,"
            "body,terms,conversation_id,evidence_ids,record_kind,valid_to) "
            "VALUES ('test',?,'insight','s1',?,?,?,'default','[\"s1\"]','understanding',?)",
            [
                ('v1', '2026-11-16T00:30:00.000000+00:00', body, json.dumps(tokens('会议安排')), expires_utc),
                ('v2', '2026-11-16T00:45:00.000000+00:00', body, json.dumps(tokens('会议安排')), expires_utc),
            ],
        )

    known_at = '2026-11-16T08:30:00+08:00'
    with make_server(store, 0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for at, effective in (
                ('2026-11-16T00:59:59.999999Z', True),
                ('2026-11-16T09:00:00+08:00', False),
                ('2026-11-16T01:00:00Z', False),
                ('2026-11-15T20:00:00.000001-05:00', False),
            ):
                query = urlencode({'known_at': known_at, 'at': at})
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/ledger?{query}', timeout=5) as response:
                    result = json.load(response)['result']
                item, = result['memories']
                assert item['record_id'] == 'v1'  # The later revision was not yet known.
                assert item['effective'] is effective
                assert item['expired'] is not effective
                assert item['valid_to'] == expires_utc
                assert item['revisions'][0]['expired'] is not effective

                recall = store.recall(RecallQuery(
                    query='会议安排', at=datetime.fromisoformat(at), known_at=datetime.fromisoformat(known_at),
                ))['results']
                assert [record['record_id'] for record in recall] == (['v1'] if effective else [])
        finally:
            server.shutdown()
            thread.join(timeout=5)
