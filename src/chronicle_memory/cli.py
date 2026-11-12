"""JSON-first command-line adapter over the same operations used by MCP."""
import argparse
import asyncio
import json
from pathlib import Path
import signal
import sqlite3
import sys
import time

from neo4j.exceptions import DriverError,Neo4jError,ServiceUnavailable

from . import __version__
from .config import Settings
from .errors import EXIT_CODES,NotInitializedError,error_result
from .provider import ProviderError
from .models import PageQuery
from .service import MemoryService
from .store import MemoryStore
from .tools import MemoryTools


class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise ValueError(message)


def emit(value):
    print(json.dumps(value,ensure_ascii=False),flush=True)


def parser():
    shared=argparse.ArgumentParser(add_help=False)
    shared.add_argument('--env-file',type=Path,default=argparse.SUPPRESS)
    shared.add_argument('--namespace',default=argparse.SUPPRESS)
    root=Parser(description='Chronicle 通用记忆 CLI',parents=[shared])
    root.add_argument('--version',action='version',version=__version__)
    sub=root.add_subparsers(dest='command',required=True)
    for name,help_text in [('demo','运行独立分区的结构化演示'),('tools','输出 MCP 工具定义'),
                           ('check','检查连接与配置'),('tick','处理一次到期检查'),
                           ('reindex','建立或补齐 Neo4j 向量索引'),
                           ('mcp','启动 stdio MCP 服务'),('now','读取当前时间')]:
        sub.add_parser(name,help=help_text,parents=[shared])
    for name in ('checks','reviews'):
        page=sub.add_parser(name,help='分页查看检查记录' if name=='checks' else '分页查看候选和待复核认识',parents=[shared])
        page.add_argument('--limit',type=int,default=50)
        page.add_argument('--offset',type=int,default=0)
        if name=='reviews':
            page.add_argument('--kind',choices=['all','candidate','understanding'],default='all')
            page.add_argument('--at');page.add_argument('--known-at')
    init=sub.add_parser('init',help='显式初始化约束和索引',parents=[shared])
    init.add_argument('--wait',type=float,default=30,help='等待数据库就绪的秒数')
    worker=sub.add_parser('worker',help='持续处理时间检查，支持 SIGINT/SIGTERM 停止',parents=[shared])
    worker.add_argument('--interval',type=float)
    ui=sub.add_parser('ui',help='启动本地只读 3D 记忆观测台',parents=[shared])
    ui.add_argument('--port',type=int,default=8765)
    capture=sub.add_parser('capture',help='通用对话采集队列：提交、后台归档和查看状态',parents=[shared])
    capture.add_argument('action',choices=['submit','worker','status'])
    capture.add_argument('--queue',type=Path,default=Path('data/capture/events.sqlite3'))
    capture.add_argument('--input',default='-')
    capture.add_argument('--interval',type=float,default=2)
    capture.add_argument('--once',action='store_true')
    config=sub.add_parser('config',help='打印客户端配置片段',parents=[shared])
    config.add_argument('agent',choices=['codex','claude','claude-desktop'])
    for name in ('remember','ingest','resolve'):
        command=sub.add_parser(name,help={'remember':'写入结构化记忆','ingest':'通过可选模型提取消息','resolve':'解决待确认项'}[name],parents=[shared])
        if name=='resolve':
            command.add_argument('review_id')
        command.add_argument('--input',default='-',help='JSON 文件路径，- 表示标准输入')
    for name in ('recall','search'):
        command=sub.add_parser(name,help='查询相关记忆与上下文' if name=='recall' else '搜索记忆短索引',parents=[shared])
        command.add_argument('query')
        command.add_argument('--subject')
        command.add_argument('--at')
        command.add_argument('--known-at')
        command.add_argument('--limit',type=int,default=8)
        command.add_argument('--include-inactive',action='store_true')
        command.add_argument('--interpret-time',action='store_true')
    show=sub.add_parser('show',help='批量读取记忆与证据',parents=[shared])
    show.add_argument('event_ids',nargs='+')
    show.add_argument('--at')
    show.add_argument('--known-at')
    show.add_argument('--source-mode',choices=['full','summary'],default='full')
    history=sub.add_parser('history',help='按记忆或实体查询历史',parents=[shared])
    for option in ('memory-id','entity-id','start','end','known-at'):
        history.add_argument('--'+option)
    history.add_argument('--limit',type=int,default=30)
    neighbors=sub.add_parser('neighbors',help='查看相关经历和认识',parents=[shared])
    neighbors.add_argument('record_id')
    neighbors.add_argument('--at');neighbors.add_argument('--known-at')
    neighbors.add_argument('--limit',type=int,default=12)
    for name in ('state','timeline'):
        command=sub.add_parser(name,help='查询精确属性的状态或变化',parents=[shared])
        command.add_argument('subject');command.add_argument('key')
        if name=='state':
            command.add_argument('--at')
        command.add_argument('--known-at')
    return root


COMMANDS={
    'remember':('memory_write',()),'ingest':('memory_ingest',()),'resolve':('memory_resolve_review',()),
    'recall':('memory_context',('query','subject','at','known_at','limit','include_inactive','interpret_time')),
    'search':('memory_search',('query','subject','at','known_at','limit','include_inactive','interpret_time')),
    'show':('memory_details',('event_ids','at','known_at','source_mode')),
    'history':('memory_history',('memory_id','entity_id','start','end','known_at','limit')),
    'neighbors':('memory_neighbors',('record_id','at','known_at','limit')),
    'state':('memory_state',('subject','key','at','known_at')),
    'timeline':('memory_timeline',('subject','key','known_at')),
    'now':('memory_now',()),'reviews':('memory_reviews',('kind','limit','offset','at','known_at')),'tick':('memory_tick',()),
}


def arguments(args):
    if args.command in ('remember','ingest','resolve'):
        if args.input=='-':
            if sys.stdin.isatty():
                raise ValueError('请通过标准输入或 --input 文件传入 JSON')
            text=sys.stdin.read()
        else:
            text=Path(args.input).read_text(encoding='utf-8')
        body=json.loads(text)
        if not isinstance(body,dict):
            raise ValueError('输入必须是 JSON 对象')
        if args.command=='resolve':
            body['review_id']=args.review_id
        return body
    return {field:getattr(args,field) for field in COMMANDS[args.command][1] if getattr(args,field) is not None}


async def run(args,settings):
    payload=arguments(args) if args.command in COMMANDS else None
    if args.command=='init':
        if args.wait<0:
            raise ValueError('--wait 不能为负数')
        deadline=time.monotonic()+args.wait
        while True:
            try:
                store=await asyncio.to_thread(MemoryStore,settings,require_initialized=False)
                break
            except ServiceUnavailable:
                if time.monotonic()>=deadline:
                    raise
                await asyncio.sleep(1)
        try:
            return {'ok':True,'result':await asyncio.to_thread(store.initialize)}
        finally:
            await asyncio.to_thread(store.close)
    if args.command=='mcp':
        from .mcp_server import serve
        await serve(settings)
        return None
    service=MemoryService(await asyncio.to_thread(MemoryStore,settings),settings)
    try:
        if args.command in COMMANDS:
            return await MemoryTools(service).call(COMMANDS[args.command][0],payload)
        if args.command=='reindex':
            return {'ok':True,'result':await service.reindex()}
        if args.command=='checks':
            page=PageQuery(limit=args.limit,offset=args.offset)
            return {'ok':True,'result':await service.database(service.store.checks,**page.model_dump())}
        if args.command=='check':
            return {'ok':True,'result':{'backend':'Neo4j','connected':True,'database':settings.neo4j_database,
                'namespace':settings.namespace,'timezone':settings.timezone,'chat_model':settings.model or None,
                'embedding_model':settings.embedding_model or None,'records':await service.database(service.store.counts)}}
        if args.command=='worker':
            from .worker import run_worker
            stop=asyncio.Event();loop=asyncio.get_running_loop()
            handlers=[]
            for sig in (signal.SIGINT,signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig,stop.set);handlers.append(sig)
                except NotImplementedError:
                    pass
            try:
                await run_worker(service,args.interval if args.interval is not None else settings.tick_seconds,stop,emit)
            finally:
                for sig in handlers:
                    loop.remove_signal_handler(sig)
            return {'ok':True,'result':{'stopped':True}}
    finally:
        await service.close()


def main():
    args=None
    try:
        args=parser().parse_args()
        if args.command=='tools':
            emit(MemoryTools.definitions());return
        if args.command=='config':
            command=str(Path(sys.executable).parent/'chronicle')
            env_file=getattr(args,'env_file',Path('.env')).resolve()
            settings=Settings.from_env(env_file)
            argv=['mcp','--env-file',str(env_file),'--namespace',getattr(args,'namespace',settings.namespace)]
            if args.agent=='codex':
                print('[mcp_servers.chronicle]\ncommand = '+json.dumps(command)+'\nargs = '+json.dumps(argv,ensure_ascii=False)+'\nstartup_timeout_sec = 15')
            else:
                entry={'command':command,'args':argv}
                if args.agent=='claude':entry['type']='stdio'
                emit({'mcpServers':{'chronicle':entry}})
            return
        env_file=getattr(args,'env_file',None if args.command=='mcp' else Path('.env'))
        if args.command=='mcp' and env_file is not None and not env_file.is_absolute():
            raise ValueError('MCP --env-file 必须是绝对路径')
        settings=Settings.from_env(env_file)
        if hasattr(args,'namespace'):settings.namespace=args.namespace
        if args.command=='capture':
            from .capture import CaptureEvent,CaptureQueue,run_worker
            queue=CaptureQueue(args.queue)
            if args.action=='submit':
                value=json.load(sys.stdin) if args.input=='-' else json.loads(Path(args.input).read_text())
                result=queue.submit(CaptureEvent.model_validate(value))
            elif args.action=='status':
                result=queue.status()
            else:
                result=run_worker(queue,settings,args.interval,args.once)
            emit({'ok':True,'result':result});return
        if args.command=='ui':
            from .ui import serve
            serve(settings,args.port)
            return
        if args.command=='demo':
            from .demo import run_demo
            run_demo(settings);return
        result=asyncio.run(run(args,settings))
        if result is None:return
    except (ValueError,OSError,sqlite3.Error,ProviderError,NotInitializedError,DriverError,Neo4jError) as error:
        result=error_result(error)
    except KeyboardInterrupt:
        result={'ok':False,'error':{'code':'interrupted','message':'操作已中断'}}
    if args is not None and args.command=='mcp':
        print(json.dumps(result,ensure_ascii=False),file=sys.stderr)
    else:
        emit(result)
    if not result['ok']:
        raise SystemExit(130 if result['error']['code']=='interrupted' else EXIT_CODES.get(result['error']['code'],1))
