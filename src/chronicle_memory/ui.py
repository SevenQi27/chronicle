"""Loopback-only, read-only memory ledger & time-machine viewer over SQLite."""

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import sqlite3
from urllib.parse import parse_qs, urlsplit

from .errors import error_result
from .models import stamp, utcnow
from .sqlite_mcp import SQLiteExperiences


def ledger_snapshot(store: SQLiteExperiences, known_at=None, at=None):
    """Build a complete ledger snapshot over SQLite with revision chains, entities, and sources."""
    now = stamp(utcnow())
    known_cutoff = stamp(datetime.fromisoformat(known_at)) if known_at else now
    effective_cutoff = stamp(datetime.fromisoformat(at)) if at else now
    with store.connection() as conn:
        all_rows = conn.execute(
            'SELECT seq, record_id, logical_id, source_id, recorded_at, effective_at, valid_to, body, '
            'origin, workspace, conversation_id, evidence_ids, record_kind '
            'FROM chronicle_experience WHERE namespace=? ORDER BY seq ASC',
            (store.namespace,),
        ).fetchall()
        source_rows = conn.execute(
            'SELECT id, body FROM chronicle_source WHERE namespace=?',
            (store.namespace,),
        ).fetchall()

    sources_by_id = {row['id']: json.loads(row['body']) for row in source_rows}
    checkpoints = sorted({row['recorded_at'] for row in all_rows})

    visible_rows = [row for row in all_rows if row['recorded_at'] <= known_cutoff]
    by_logical = {}
    records_by_id = {}

    for row in visible_rows:
        payload = json.loads(row['body'])
        record_id = row['record_id']
        logical_id = row['logical_id']
        kind = row['record_kind']
        evidence_ids = json.loads(row['evidence_ids'])
        sources = [sources_by_id[sid] for sid in evidence_ids if sid in sources_by_id]
        valid_to = row['valid_to']
        expired = valid_to is not None and valid_to <= effective_cutoff
        is_effective = (
            (row['effective_at'] is None or row['effective_at'] <= effective_cutoff)
            and not expired
        )
        item = {
            'seq': row['seq'],
            'record_id': record_id,
            'logical_id': logical_id,
            'record_kind': kind,
            'title': payload['title'],
            'summary': payload.get('narrative', payload.get('statement', '')),
            'narrative': payload.get('narrative', payload.get('statement', '')),
            'when': payload.get('when', {'precision': 'unknown', 'description': ''}),
            'basis': payload.get('basis'),
            'supports': payload.get('supports', []),
            'entities': payload.get('entities', []),
            'replaces': payload.get('replaces'),
            'revision_kind': payload.get('revision_kind', 'update'),
            'recorded_at': row['recorded_at'],
            'effective_at': row['effective_at'],
            'valid_to': valid_to,
            'effective': is_effective,
            'expired': expired,
            'origin': row['origin'],
            'workspace': row['workspace'],
            'conversation_id': row['conversation_id'],
            'source_id': row['source_id'],
            'evidence_ids': evidence_ids,
            'sources': sources,
        }
        by_logical.setdefault(logical_id, []).append(item)
        records_by_id[record_id] = item

    memories = []
    entity_map = {}
    workspaces = set()
    origins = set()
    conversations = set()

    for logical_id, versions in by_logical.items():
        for idx, ver in enumerate(versions, 1):
            ver['version_number'] = idx
            ver['superseded'] = idx < len(versions)
        head = versions[-1]
        if head['workspace']:
            workspaces.add(head['workspace'])
        if head['origin']:
            origins.add(head['origin'])
        if head['conversation_id']:
            conversations.add(head['conversation_id'])

        for ent in head.get('entities', []):
            eid = ent['id']
            entry = entity_map.setdefault(eid, {
                'id': eid,
                'name': ent['name'],
                'category': ent.get('category', 'topic'),
                'aliases': [],
                'count': 0,
            })
            entry['name'] = ent['name']
            entry['category'] = ent.get('category', entry['category'])
            for alias in ent.get('aliases', []):
                if alias not in entry['aliases']:
                    entry['aliases'].append(alias)
            entry['count'] += 1

        # Determine if an understanding's supporting experience was updated/corrected
        needs_review = False
        if head['record_kind'] == 'understanding':
            for sup_ref in head.get('supports', []):
                if sup_ref in records_by_id and records_by_id[sup_ref]['superseded']:
                    needs_review = True

        memories.append({
            **head,
            'version_count': len(versions),
            'has_correction': any(v['revision_kind'] == 'correct' for v in versions[1:]),
            'needs_review': needs_review,
            'revisions': versions,
        })

    # Compute 1-hop neighbors (shared entities or evidence support links)
    for mem in memories:
        mem_entity_ids = {e['id'] for e in mem.get('entities', [])}
        neighbors = []
        for other in memories:
            if other['logical_id'] == mem['logical_id']:
                continue
            reasons = []
            shared = [e['name'] for e in other.get('entities', []) if e['id'] in mem_entity_ids]
            if shared:
                reasons.append({'kind': 'shared_entity', 'label': '共同实体: ' + '、'.join(shared)})
            if other['record_id'] in mem.get('supports', []) or other['logical_id'] in mem.get('supports', []):
                reasons.append({'kind': 'supports', 'label': '支撑经历'})
            if mem['record_id'] in other.get('supports', []) or mem['logical_id'] in other.get('supports', []):
                reasons.append({'kind': 'supported_by', 'label': '衍生认识'})
            if reasons:
                neighbors.append({
                    'logical_id': other['logical_id'],
                    'record_id': other['record_id'],
                    'title': other['title'],
                    'record_kind': other['record_kind'],
                    'reasons': reasons,
                })
        mem['neighbors'] = neighbors[:8]

    memories.sort(key=lambda m: (m['recorded_at'], m['seq']), reverse=True)
    entities = sorted(entity_map.values(), key=lambda e: (-e['count'], e['name']))

    return {
        'namespace': store.namespace,
        'captured_at': now,
        'known_at': known_cutoff,
        'at': effective_cutoff,
        'total_versions': len(all_rows),
        'visible_versions': len(visible_rows),
        'logical_count': len(memories),
        'checkpoints': checkpoints,
        'facets': {
            'workspaces': sorted(workspaces),
            'origins': sorted(origins),
            'conversations': sorted(conversations),
            'entities': entities,
        },
        'memories': memories,
    }


def graph_snapshot(store: SQLiteExperiences):
    now = stamp(utcnow())
    with store.connection() as conn:
        conn.execute('BEGIN')
        rows = conn.execute(
            'SELECT record_id, logical_id, record_kind, recorded_at, body, evidence_ids '
            'FROM chronicle_experience WHERE namespace=? AND recorded_at<=? ORDER BY seq DESC',
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
            parsed = urlsplit(self.path)
            path = parsed.path
            if path in ('/api/ledger', '/api/graph'):
                params = parse_qs(parsed.query)
                known_at = params.get('known_at', [None])[0]
                at = params.get('at', [None])[0]
                try:
                    snapshot = (
                        graph_snapshot(store) if path == '/api/graph'
                        else ledger_snapshot(store, known_at=known_at, at=at)
                    )
                    result = {'ok': True, 'result': snapshot}
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
                '/ledger': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css',
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
