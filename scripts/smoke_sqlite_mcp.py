"""Exercise SQLite memory over a real stdio MCP connection."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@asynccontextmanager
async def client(launcher, database):
    params = StdioServerParameters(
        command=launcher[0], args=[*launcher[1:], 'mcp', '--sqlite', str(database)],
        cwd=str(database.parent.parent),
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
            await session.initialize()
            yield session


async def call(session, name, arguments, error=None):
    reply = await session.call_tool(name, arguments)
    response = reply.structured_content
    if error:
        assert reply.is_error and response['error']['code'] == error, (name, response)
        return response['error']
    assert not reply.is_error and response['ok'], (name, response)
    return response['result']


def write_request(source_id, content, room, **version):
    return {
        'source': {'id': source_id, 'content': content, 'occurred_at': datetime.now(timezone.utc).isoformat(),
                   'conversation_id': 'project-aurora'},
        'context': {'workspace': 'aurora'},
        'experiences': [{'id': 'aurora-review', 'title': '极光评审安排', 'narrative': room,
                         'entities': [{'id': 'aurora-review-topic', 'name': '极光评审', 'aliases': ['北辰碰头']}],
                         **version}],
    }


async def smoke(launcher, database):
    original = write_request('message-1', '极光评审地点定在三楼会议室。', '极光评审在三楼会议室。')
    lexical_question = {'query': '极光评审在哪开？', 'filters': {'workspace': 'aurora'}}
    alias_question = {'query': '北辰碰头在哪开？', 'filters': {'workspace': 'aurora'}}
    old_only = {'query': '三楼'}
    async with client(launcher, database) as session:
        assert {tool.name for tool in (await session.list_tools()).tools} == {
            'memory_write', 'memory_search', 'memory_context', 'memory_details',
        }
        saved = await call(session, 'memory_write', original)
        first = saved['record_ids'][0]
        assert await call(session, 'memory_write', original) == saved
        assert database.is_file()
        print('PASS 首次启动自动建库，MCP 写入和重复提交返回同一版本')

        lexical = (await call(session, 'memory_context', lexical_question))['results']
        alias = (await call(session, 'memory_search', alias_question))['results']
        assert lexical[0]['record_id'] == alias[0]['record_id'] == first
        assert '三楼' in lexical[0]['summary']
        assert (await call(session, 'memory_search', {'query': '土星环的组成'}))['results'] == []
        alternative = await call(session, 'memory_search', {
            'query': '土星环的组成', 'alternatives': ['北辰碰头'],
            'filters': {'source_id': original['source']['id']},
        })
        assert alternative['results'][0]['matched_queries'] == ['北辰碰头']
        assert alternative['results'][0]['record_id'] == first
        print('PASS 改写问法与别名问法找回三楼，独立无关问法无命中')

        correction = write_request('message-2', '更正，极光评审实际在五楼会议室。', '极光评审在五楼会议室。',
                                   replaces=first, revision_kind='correct')
        updated = await call(session, 'memory_write', correction)
        second = updated['record_ids'][0]
        assert second != first
        stale = write_request('message-3', '旧版本尝试改成六楼。', '极光评审在六楼会议室。', replaces=first)
        conflict = await call(session, 'memory_write', stale, error='version_conflict')
        assert conflict['current_record_id'] == second
        current = (await call(session, 'memory_context', lexical_question))['results']
        assert [row['record_id'] for row in current] == [second] and '五楼' in current[0]['summary']
        assert not (await call(session, 'memory_search', old_only))['results']
        assert not (await call(session, 'memory_search', {**old_only, 'at': saved['recorded_at']}))['results']
        historic = await call(session, 'memory_search', {**old_only, 'known_at': saved['recorded_at']})
        assert [row['record_id'] for row in historic['results']] == [first]
        print('PASS 更正后命中五楼，旧版本写入被拦截并返回最新版本 ID')

    async with client(launcher, database) as session:
        current = (await call(session, 'memory_search', alias_question))['results']
        assert [row['record_id'] for row in current] == [second] and '五楼' in current[0]['summary']
        historic = (await call(session, 'memory_search', {**alias_question, 'known_at': saved['recorded_at']}))['results']
        assert [row['record_id'] for row in historic] == [first] and '三楼' in historic[0]['summary']
        old, new = await call(session, 'memory_details', {'event_ids': [first, second]})
        assert old['superseded'] and not new['superseded'] and new['replaces'] == first
        assert old['source']['content'] == original['source']['content']
        assert [source['id'] for source in new['sources']] == ['message-2', 'message-1']
        print('PASS 新 MCP 进程复用 SQLite，当前五楼，历史仍可查到三楼和原始来源')

        insight_source = {
            'id': 'insight-message', 'content': '开会地点要以最新确认的消息为准。',
            'occurred_at': datetime.now(timezone.utc).isoformat(),
        }
        insight = {
            'id': 'meeting-guideline', 'title': '会前确认地点', 'statement': '会议地点以最新确认的消息为准。',
            'basis': 'inferred', 'supports': ['missing-experience'],
            'valid_to': '2099-01-01T09:00:00+08:00',
        }
        await call(session, 'memory_write', {
            'source': insight_source, 'understandings': [insight],
        }, error='invalid_arguments')
        write_insight = {
            'source': insight_source, 'understandings': [{**insight, 'supports': [first, 'aurora-review']}],
        }
        written = await call(session, 'memory_write', write_insight)
        assert await call(session, 'memory_write', write_insight) == written
        insight_record, = written['record_ids']
        await call(session, 'memory_write', {
            'source': {**insight_source, 'id': 'wrong-support-message'},
            'understandings': [{**insight, 'id': 'wrong-support', 'supports': [insight_record]}],
        }, error='invalid_arguments')

        query = {'query': '会前确认地点', 'subject': 'meeting-guideline'}
        entry, = (await call(session, 'memory_search', query))['results']
        assert (entry['record_id'], entry['record_kind'], entry['basis']) == (insight_record, 'understanding', 'inferred')
        detail, = await call(session, 'memory_details', {'event_ids': [insight_record]})
        assert detail['support_record_ids'] == [first, second]
        assert detail['source']['content'] == insight_source['content']
        supporting = await call(session, 'memory_details', {'event_ids': detail['support_record_ids']})
        assert [r['record_kind'] for r in supporting] == ['experience', 'experience']
        assert [r['source']['content'] for r in supporting] == [original['source']['content'], correction['source']['content']]
        assert not (await call(session, 'memory_search', {**query, 'at': '2099-01-01T01:00:00Z'}))['results']
        print('PASS 认识通过全文检索，能沿两个支撑版本查到原文，拒绝无效支撑并按 UTC 到期')

        effective_at = '2099-01-01T00:00:00+00:00'
        future = write_request(
            'message-4', '未来安排改到七楼。', '极光评审在七楼会议室。',
            replaces=second, when={'start': effective_at, 'precision': 'exact'},
        )
        future_id = (await call(session, 'memory_write', future))['record_ids'][0]
        assert not (await call(session, 'memory_search', alias_question))['results']
        future_results = await call(session, 'memory_search', {**alias_question, 'at': effective_at})
        assert [row['record_id'] for row in future_results['results']] == [future_id]
        before_change = await call(session, 'memory_search', {**alias_question, 'known_at': updated['recorded_at']})
        assert [row['record_id'] for row in before_change['results']] == [second]
        print('PASS at 和 known_at 分别控制生效与获知视角')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('launcher', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    launcher = args.launcher or [str(Path(sys.executable).with_name('chronicle'))]
    if launcher[0] == '--':
        launcher = launcher[1:]
    with TemporaryDirectory(prefix='chronicle-sqlite-') as tmp:
        asyncio.run(smoke(launcher, Path(tmp) / 'data' / 'memory.sqlite3'))


if __name__ == '__main__':
    main()
