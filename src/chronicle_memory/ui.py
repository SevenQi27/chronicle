"""Loopback-only, read-only memory graph over SQLite."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import sqlite3
from urllib.parse import urlsplit

from .errors import error_result
from .models import stamp, utcnow
from .sqlite_mcp import SQLiteExperiences, WITHDRAWN_MEMORY


def graph_snapshot(store: SQLiteExperiences):
    now = stamp(utcnow())
    with store.connection() as conn:
        conn.execute('BEGIN')
        rows = conn.execute(
            'SELECT record_id, logical_id, record_kind, recorded_at, body, evidence_ids '
            f'FROM chronicle_experience AS r WHERE namespace=? AND recorded_at<=? '
            f'AND NOT {WITHDRAWN_MEMORY} ORDER BY seq DESC',
            (store.namespace, now),
        ).fetchall()
        sources = {
            row['id']: json.loads(row['body'])
            for row in conn.execute('SELECT id, body FROM chronicle_source WHERE namespace=?', (store.namespace,))
        }

    nodes, latest = {}, {}
    for row in rows:
        payload = json.loads(row['body'])
        identifier = row['record_id']
        latest.setdefault(row['logical_id'], identifier)
        nodes[identifier] = {
            'id': identifier, 'record_id': identifier, 'logical_id': row['logical_id'],
            'kind': row['record_kind'], 'title': payload['title'],
            'text': payload.get('narrative', payload.get('statement', '')),
            'recorded_at': row['recorded_at'], 'payload': payload,
        }

    edges = []
    for row in rows:
        identifier = row['record_id']
        payload = nodes[identifier]['payload']
        for source_id in json.loads(row['evidence_ids']):
            source = sources.get(source_id)
            if source is None:
                continue
            key = 'source:' + source_id
            nodes.setdefault(key, {
                'id': key, 'kind': 'source', 'payload': source, 'text': source['content'],
                'title': {'user': '用户原话', 'tool': '工具结果', 'assistant': '助手记录'}.get(source['role'], '原始来源'),
                'recorded_at': source['occurred_at'],
            })
            edges.append({'from': identifier, 'to': key, 'kind': 'EVIDENCED_BY'})
        for entity in payload.get('entities', []):
            key = 'entity:' + entity['id']
            nodes.setdefault(key, {
                'id': key, 'kind': 'entity', 'title': entity['name'], 'text': entity['name'], 'payload': entity,
            })
            edges.append({'from': identifier, 'to': key, 'kind': 'INVOLVES'})
        for reference in payload.get('support_record_ids') or payload.get('supports', []):
            target = reference if reference in nodes else latest.get(reference)
            if target in nodes:
                edges.append({'from': identifier, 'to': target, 'kind': 'SUPPORTED_BY'})
        if payload.get('replaces') in nodes:
            kind = 'CORRECTS' if payload.get('revision_kind') == 'correct' else 'REVISES'
            edges.append({'from': identifier, 'to': payload['replaces'], 'kind': kind})

    return {
        'namespace': store.namespace, 'captured_at': now, 'nodes': list(nodes.values()),
        'edges': edges, 'total_records': len(rows), 'limit': None, 'truncated': False,
    }


def make_server(store: SQLiteExperiences, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, content, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header(
                'Content-Security-Policy',
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                self.respond(403, b'Forbidden', 'text/plain; charset=utf-8')
                return
            path = urlsplit(self.path).path
            if path == '/api/graph':
                try:
                    result = {'ok': True, 'result': graph_snapshot(store)}
                    status = 200
                except (sqlite3.Error, ValueError, OSError) as error:
                    result = error_result(error)
                    status = 503 if isinstance(error, (sqlite3.Error, OSError)) else 400
                self.respond(
                    status,
                    json.dumps(result, ensure_ascii=False).encode('utf-8'),
                    'application/json; charset=utf-8',
                )
                return
            assets = {
                '/': 'graph/index.html', '/graph': 'graph/index.html',
                '/graph/app.js': 'graph/app.js', '/graph/style.css': 'graph/style.css',
            }
            if path not in assets:
                self.respond(404, b'Not found', 'text/plain; charset=utf-8')
                return
            name = assets[path]
            mime = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css'}[name.rsplit('.', 1)[1]]
            self.respond(200, files('chronicle_memory').joinpath('static', name).read_bytes(), mime + '; charset=utf-8')

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve_ui(path, namespace='default', port=8765):
    if not 0 <= port <= 65535:
        raise ValueError('--port 必须在 0–65535 之间')
    store = SQLiteExperiences(path, namespace)
    with make_server(store, port) as server:
        print(f'Chronicle UI: http://127.0.0.1:{server.server_port}/  (namespace: {store.namespace})', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
