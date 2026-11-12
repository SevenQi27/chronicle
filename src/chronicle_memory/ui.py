"""Loopback-only, read-only graph viewer over the existing memory store."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from urllib.parse import urlsplit

from neo4j.exceptions import DriverError, Neo4jError

from .errors import error_result
from .models import stamp, state_key
from .store import MemoryStore


def graph_snapshot(store, limit=200):
    """Keep actual revision IDs and evidence edges; the slider filters knowledge time."""
    view=store.capture_view()
    params={'ns':store.namespace,'revision':view.revision,'known':stamp(view.known_at)}
    total=store._run('MATCH (r:MemoryRecord {namespace:$ns}) '
        'WHERE r.seq<=$revision AND r.recorded_at<=datetime($known) '
        'RETURN count(r) AS total',**params)[0]['total']
    rows=store._run('MATCH (r:MemoryRecord {namespace:$ns}) '
        'WHERE r.seq<=$revision AND r.recorded_at<=datetime($known) '
        'RETURN properties(r) AS r ORDER BY r.seq DESC LIMIT $limit',limit=limit,**params)
    nodes={}; latest={}; uids=[]
    for item in rows:
        r=item['r']; payload=json.loads(r['body']); uid=r['uid']; uids.append(uid)
        logical=state_key(r['subject'],r['key']) if r['record_kind']=='state' else r['logical_id']
        latest.setdefault(logical,uid)
        nodes[uid]={'id':uid,'record_id':r['event_id'],'logical_id':logical,
            'kind':r['record_kind'],'title':payload.get('title') or f"{payload.get('subject','')} · {payload.get('key','')}",
            'text':r['text'],'recorded_at':stamp(r['recorded_at'].to_native()),
            'payload':payload}
    edges=[]
    if uids:
        links=store._run('UNWIND $uids AS uid MATCH (a:MemoryRecord {uid:uid})'
            '-[e:EVIDENCED_BY|INVOLVES|SUPPORTED_BY|REVISES|CORRECTS]->(b) '
            'WHERE b.namespace=$ns AND (NOT b:MemoryRecord OR b.uid IN $uids) '
            'RETURN a.uid AS from_id,b.uid AS to_id,type(e) AS kind,properties(b) AS target',
            uids=uids,ns=store.namespace)
        for link in links:
            r=link['target']; uid=link['to_id']
            if uid not in nodes:
                if link['kind']=='INVOLVES':
                    nodes[uid]={'id':uid,'kind':'entity','title':r['name'],
                        'text':r['name'],'payload':{'category':r['category'],'aliases':r.get('aliases',[])}}
                else:
                    source=json.loads(r['body'])
                    nodes[uid]={'id':uid,'kind':'source','title':{'user':'用户原话','tool':'工具结果','assistant':'助手记录'}.get(source['role'],'原始来源'),
                        'text':source['content'],'payload':source,
                        'recorded_at':stamp(r['recorded_at'].to_native())}
            edges.append({'from':link['from_id'],'to':uid,'kind':link['kind']})
        # Explicit associations belong to logical memories; attach to their latest
        # loaded revisions and retain their recorded time for the history filter.
        for link in store.association_views(view=view,memory_keys=list(latest)):
            a=latest.get(link['from_id']); b=latest.get(link['to_id'])
            if a and b:
                edges.append({'from':a,'to':b,'kind':link['kind'].upper(),
                    'explanation':link['explanation'],'recorded_at':link['recorded_at']})
    return {'namespace':store.namespace,'timezone':store.settings.timezone,'revision':view.revision,
        'captured_at':stamp(view.known_at),'nodes':list(nodes.values()),'edges':edges,
        'total_records':total,'limit':limit,'truncated':total>len(rows)}


def make_server(store, port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, content, content_type):
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(content)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            # No CORS and a strict Host check keep local memory out of arbitrary
            # websites, including domains whose DNS resolves to loopback.
            allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                self.respond(403,b'Forbidden','text/plain; charset=utf-8');return
            path=urlsplit(self.path).path
            if path=='/api/graph':
                try:
                    result={'ok':True,'result':graph_snapshot(store)}
                    status=200
                except (DriverError,Neo4jError) as error:
                    result=error_result(error);status=503
                self.respond(status,json.dumps(result,ensure_ascii=False).encode(),'application/json; charset=utf-8')
                return
            assets={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
            if path not in assets:
                self.respond(404,b'Not found','text/plain; charset=utf-8');return
            name=assets[path]
            mime={'html':'text/html','js':'text/javascript','css':'text/css'}[name.rsplit('.',1)[1]]
            self.respond(200,files('chronicle_memory').joinpath('static',name).read_bytes(),mime+'; charset=utf-8')

    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def serve(settings, port=8765):
    if not 0<=port<=65535:
        raise ValueError('--port 必须在 0–65535 之间')
    store=MemoryStore(settings)
    try:
        with make_server(store,port) as server:
            print(f'Chronicle Observatory: http://127.0.0.1:{server.server_port}  (namespace: {store.namespace})',flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        store.close()
