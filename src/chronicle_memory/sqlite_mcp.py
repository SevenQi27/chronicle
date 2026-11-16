"""SQLite and stdio MCP for writing, recalling and correcting sourced experiences."""

import asyncio
from contextlib import closing, contextmanager
from heapq import nlargest
import json
import os
import re
import sqlite3
from typing import Literal
from uuid import uuid4

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations
from pydantic import Field

from . import __version__
from .errors import ConflictError, error_result
from .models import DetailQuery, Experience, Model, Query, Source, WriteContext, stamp, utcnow


def tokens(text):
    result = re.findall(r"[a-z0-9_]+", text.lower())
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(part[i:i+2] for i in range(len(part)-1))
        if len(part)==1:
            result.append(part)
    return list(dict.fromkeys(result))


class ExperienceWrite(Model):
    source: Source
    experiences: list[Experience] = Field(min_length=1)
    context: WriteContext | None = None


class RecallQuery(Query):
    interpret_time: Literal[False] = False


SCHEMA = """
CREATE TABLE IF NOT EXISTS chronicle_source (
    namespace TEXT NOT NULL,
    id TEXT NOT NULL,
    body TEXT NOT NULL,
    request TEXT NOT NULL,
    result TEXT NOT NULL,
    PRIMARY KEY (namespace, id)
);
CREATE TABLE IF NOT EXISTS chronicle_experience (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace TEXT NOT NULL,
    record_id TEXT NOT NULL UNIQUE,
    logical_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    effective_at TEXT,
    body TEXT NOT NULL,
    terms TEXT NOT NULL,
    origin TEXT,
    workspace TEXT,
    conversation_id TEXT NOT NULL,
    evidence_ids TEXT NOT NULL,
    FOREIGN KEY (namespace, source_id) REFERENCES chronicle_source(namespace, id)
);
CREATE INDEX IF NOT EXISTS chronicle_experience_head
    ON chronicle_experience(namespace, logical_id, seq DESC);
"""


class SQLiteExperiences:
    def __init__(self, path, namespace):
        if not namespace.strip():
            raise ValueError('namespace 不能为空')
        self.path = path
        self.namespace = namespace
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            pass
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            self.initialize_search_index(conn)

    @staticmethod
    def initialize_search_index(conn):
        # Backfill once and install the insert trigger in the same transaction.
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='chronicle_experience_fts'").fetchone():
            return
        conn.execute(
            "CREATE VIRTUAL TABLE chronicle_experience_fts USING fts5("
            "terms, content='', detail=none, tokenize='unicode61 tokenchars _')"
        )
        conn.execute(
            "INSERT INTO chronicle_experience_fts(rowid, terms) "
            "SELECT seq, (SELECT group_concat(value, ' ') FROM json_each(r.terms)) "
            "FROM chronicle_experience AS r"
        )
        conn.execute("""
            CREATE TRIGGER chronicle_experience_fts_insert AFTER INSERT ON chronicle_experience
            BEGIN
                INSERT INTO chronicle_experience_fts(rowid, terms)
                VALUES (new.seq, (SELECT group_concat(value, ' ') FROM json_each(new.terms)));
            END
        """)

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys = ON')
            with conn:
                yield conn

    def write(self, request):
        source = request.source
        if source.role == 'assistant':
            raise ValueError('助手复述不能作为新经历的来源')
        payload = request.model_dump_json()
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            receipt = conn.execute(
                'SELECT request, result FROM chronicle_source WHERE namespace=? AND id=?',
                (self.namespace, source.id),
            ).fetchone()
            if receipt:
                if receipt['request'] != payload:
                    raise ConflictError('source_conflict', '相同来源 ID 已用于不同写入', source_id=source.id)
                return json.loads(receipt['result'])
            recorded_at = stamp(utcnow())
            record_ids = [str(uuid4()) for _ in request.experiences]
            result = {
                'source_id': source.id, 'event_ids': record_ids, 'record_ids': record_ids,
                'association_ids': [], 'review_ids': [], 'status': 'applied', 'recorded_at': recorded_at,
            }
            conn.execute(
                'INSERT INTO chronicle_source VALUES (?, ?, ?, ?, ?)',
                (self.namespace, source.id, source.model_dump_json(), payload, json.dumps(result)),
            )
            for experience, record_id in zip(request.experiences, record_ids):
                head = conn.execute(
                    'SELECT record_id, evidence_ids FROM chronicle_experience '
                    'WHERE namespace=? AND logical_id=? ORDER BY seq DESC LIMIT 1',
                    (self.namespace, experience.id),
                ).fetchone()
                if head and experience.replaces != head['record_id']:
                    raise ConflictError('version_conflict', '请读取最新版本再更新这条记忆',
                                        current_record_id=head['record_id'], memory_id=experience.id)
                if not head and experience.replaces:
                    raise ValueError('replaces 与此记忆 ID 不匹配')
                previous = json.loads(head['evidence_ids']) if head else []
                evidence_ids = list(dict.fromkeys([source.id, *experience.source_ids, *previous]))
                self.sources(conn, evidence_ids)
                search_text = [experience.title, experience.narrative]
                for entity in experience.entities:
                    search_text.extend([entity.id, entity.name, *entity.aliases])
                context = request.context or WriteContext()
                conn.execute(
                    'INSERT INTO chronicle_experience (namespace, record_id, logical_id, source_id, '
                    'recorded_at, effective_at, body, terms, origin, workspace, conversation_id, evidence_ids) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (self.namespace, record_id, experience.id, source.id, recorded_at,
                     stamp(experience.when.start) if experience.when.start else None,
                     experience.model_dump_json(), json.dumps(tokens(' '.join(search_text))),
                     context.origin, context.workspace, source.conversation_id, json.dumps(evidence_ids)),
                )
        return result

    def sources(self, conn, identifiers):
        rows = conn.execute(
            'SELECT id, body FROM chronicle_source WHERE namespace=? AND id IN (SELECT value FROM json_each(?))',
            (self.namespace, json.dumps(identifiers)),
        )
        by_id = {row['id']: json.loads(row['body']) for row in rows}
        if len(by_id) != len(identifiers):
            raise ValueError('记忆引用了当前分区中不存在的来源')
        return [by_id[identifier] for identifier in identifiers]

    def candidates(self, conn, match, at, known_at, filters):
        sql = """
            SELECT r.seq, r.record_id, r.source_id, r.recorded_at, r.body, r.terms
            FROM chronicle_experience_fts CROSS JOIN chronicle_experience AS r
            WHERE chronicle_experience_fts MATCH ? AND r.seq=chronicle_experience_fts.rowid
              AND r.namespace=? AND r.recorded_at<=?
              AND (r.effective_at IS NULL OR r.effective_at<=?)
              AND r.seq=(
                  SELECT newer.seq FROM chronicle_experience AS newer
                  WHERE newer.namespace=r.namespace AND newer.logical_id=r.logical_id AND newer.recorded_at<=?
                  ORDER BY newer.seq DESC LIMIT 1
              )
        """
        args = [match, self.namespace, known_at, at, known_at]
        if filters:
            for field in ('origin', 'workspace', 'conversation_id'):
                value = getattr(filters, field)
                if value is not None:
                    sql += f' AND r.{field}=?'
                    args.append(value)
            if filters.source_id:
                sql += ' AND EXISTS (SELECT 1 FROM json_each(r.evidence_ids) WHERE value=?)'
                args.append(filters.source_id)
        return conn.execute(sql, args)

    def recall(self, request):
        now = utcnow()
        at, known_at = stamp(request.at or now), stamp(request.known_at or now)
        phrases = {query: set(tokens(query)) for query in [request.query, *request.alternatives]}
        terms = set().union(*phrases.values())
        if not terms:
            return {'status': 'ok', 'at': at, 'known_at': known_at, 'results': []}
        # Each token is indexed separately. Quotes keep terms such as "or" literal.
        match = ' OR '.join(f'"{term}"' for term in sorted(terms))
        with self.connection() as conn:
            rows = self.candidates(conn, match, at, known_at, request.filters)
            ranked = nlargest(request.limit, self.score_candidates(rows, request.subject, phrases),
                              key=lambda candidate: candidate[:2])
        results = []
        for score, _, row, matched_queries in ranked:
            experience = json.loads(row['body'])
            results.append({
                'record_id': row['record_id'], 'record_kind': 'experience', 'id': experience['id'],
                'title': experience['title'], 'summary': experience['narrative'][:320],
                'status': 'active', 'when': experience['when'], 'recorded_at': row['recorded_at'],
                'source_id': row['source_id'], 'score': score, 'match_scope': 'current',
                'matched_record_ids': [row['record_id']], 'matched_queries': matched_queries,
            })
        return {'status': 'ok', 'at': at, 'known_at': known_at, 'results': results}

    @staticmethod
    def score_candidates(rows, subject, phrases):
        for row in rows:
            if subject:
                experience = json.loads(row['body'])
                if subject not in (experience['title'], experience['id']) and not any(
                    subject in (entity['id'], entity['name'], *entity['aliases'])
                    for entity in experience['entities']
                ):
                    continue
            terms = set(json.loads(row['terms']))
            matches = {query: len(words & terms) for query, words in phrases.items()}
            score = max(matches.values())
            if score:
                yield score, row['seq'], row, [query for query, count in matches.items() if count]

    def details(self, request):
        now = utcnow()
        at, known_at = stamp(request.at or now), stamp(request.known_at or now)
        result = []
        with self.connection() as conn:
            conn.execute('BEGIN')
            for record_id in dict.fromkeys(request.event_ids):
                row = conn.execute(
                    'SELECT * FROM chronicle_experience WHERE namespace=? AND record_id=? AND recorded_at<=?',
                    (self.namespace, record_id, known_at),
                ).fetchone()
                if not row:
                    continue
                head = conn.execute(
                    'SELECT record_id FROM chronicle_experience WHERE namespace=? AND logical_id=? AND recorded_at<=? '
                    'ORDER BY seq DESC LIMIT 1', (self.namespace, row['logical_id'], known_at),
                ).fetchone()
                sources = self.sources(conn, json.loads(row['evidence_ids']))
                if request.source_mode == 'summary':
                    for source in sources:
                        source['content_chars'] = len(source.pop('content'))
                        source['content_omitted'] = True
                result.append({
                    **json.loads(row['body']), 'event_id': record_id, 'record_id': record_id,
                    'record_kind': 'experience', 'source_id': row['source_id'], 'source': sources[0],
                    'sources': sources, 'source_count': len(sources), 'source_mode': request.source_mode,
                    'recorded_at': row['recorded_at'], 'at': at, 'known_at': known_at,
                    'superseded': head['record_id'] != record_id or bool(row['effective_at'] and row['effective_at'] > at),
                })
        return result


def build_server(store):
    operations = {
        'memory_write': (ExperienceWrite, store.write, '保存带来源的经历。更正时沿用经历 ID，replaces 填当前记录的 record_id。'),
        'memory_search': (RecallQuery, store.recall, '按关键词、实体名称和已保存的别名检索经历。可传 alternatives。'),
        'memory_context': (RecallQuery, store.recall, '检索相关经历的短摘要与 record_id，按需使用 memory_details。'),
        'memory_details': (DetailQuery, store.details, '按版本 ID 读取经历和来源，旧版本可独立查看。'),
    }
    async def list_tools(context, params):
        return ListToolsResult(tools=[
            Tool(name=name, description=description, inputSchema=model.model_json_schema(),
                 annotations=ToolAnnotations(readOnlyHint=name != 'memory_write', openWorldHint=False))
            for name, (model, _, description) in operations.items()
        ])

    async def call_tool(context, params):
        try:
            if params.name in operations:
                model, operation, _ = operations[params.name]
                value = await asyncio.to_thread(operation, model.model_validate(params.arguments or {}))
                result = {'ok': True, 'result': value}
            else:
                result = {'ok': False, 'error': {'code': 'unknown_tool', 'message': 'SQLite 模式未提供此工具'}}
        except (sqlite3.Error, ValueError) as error:
            result = error_result(error)
        return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                              structuredContent=result, isError=not result['ok'])

    instructions = (
        'SQLite 模式。使用 memory_write 保存带来源的经历，memory_search 或 memory_context 检索摘要，'
        'memory_details 查阅来源。更正时先查询当前记录，沿用经历 ID、为新原话提供新来源 ID，'
        '并用 replaces 引用当前记录的 record_id。'
    )
    return Server('chronicle-memory', version=__version__, instructions=instructions,
                  on_list_tools=list_tools, on_call_tool=call_tool)


async def serve(path, namespace):
    store = await asyncio.to_thread(SQLiteExperiences, path, namespace)
    server = build_server(store)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
