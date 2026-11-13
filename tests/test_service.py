import json

import httpx
import pytest

from chronicle_memory.config import Settings
from chronicle_memory.models import Query, Source
from chronicle_memory.provider import CompatibleProvider, ProviderError
from chronicle_memory.service import MemoryService
from chronicle_memory.tools import MemoryTools
from conftest import day


def response(payload):
    return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":json.dumps(payload)}}]})


def memory(value="李四",**extra):
    return {"subject":"项目 A","key":"owner","kind":"fact","text":f"项目 A 负责人 {value}",
            "value":value,"effective_at":day(5).isoformat(),**extra}


async def test_chat_extraction_real_wire_adapter_and_retry_idempotency(scenario):
    s=scenario
    s.write()
    s.now=day(10)
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        assert request.url.path=="/v1/chat/completions"
        return response({"memories":[memory()],"review":[]})
    settings=Settings(model="test-chat",api_key="test-only-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider=CompatibleProvider(settings,client)
        service=MemoryService(s.store,settings,provider)
        source=Source(id="chat:10",content="李四从 5 日接手项目 A",occurred_at=s.now)
        result=await service.ingest(source)
        repeated=await service.ingest(source)
        assert result["event_ids"]==repeated["event_ids"]
        assert len(calls)==1
        assert calls[0]["response_format"]=={"type":"json_object"}
        prompt=json.loads(calls[0]["messages"][1]["content"])
        assert prompt["current_source"]["id"]=="chat:10"
        assert prompt["existing_memories"]
        assert s.state(at=7)["memory"]["value"]=="李四"
        assert s.state(at=7,known=7)["memory"]["value"]=="张三"


async def test_assistant_reply_is_not_reextracted(scenario):
    settings=Settings(model="test-chat")
    def forbidden(request):
        pytest.fail("助手复述不应调用模型提取")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        service=MemoryService(scenario.store,settings,CompatibleProvider(settings,client))
        result=await service.ingest(Source(id="assistant:1",content="我猜负责人是张三",role="assistant",occurred_at=day(1)))
        assert result["event_ids"]==[]
        assert scenario.store.states()==[]


async def test_invalid_extraction_does_not_write_and_can_retry(scenario):
    settings=Settings(model="test-chat")
    results=[{"memories":[memory(effective_at="tomorrow")],"review":[]},
             {"memories":[],"review":[{"text":"近期交接","reason":"日期不明确"}]}]
    def handle(request):
        return response(results.pop(0))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service=MemoryService(scenario.store,settings,CompatibleProvider(settings,client))
        source=Source(id="unclear",content="近期可能交接",occurred_at=day(1))
        with pytest.raises(ProviderError):
            await service.ingest(source)
        assert scenario.store.states()==[]
        assert scenario.store.source(source.id)["status"]=="failed"
        result=await service.ingest(source)
        assert len(result["review_ids"])==1
        assert scenario.store.states()==[]


@pytest.mark.parametrize("provider_response",[
    httpx.Response(401,json={"error":"secret-provider-body"}),
    httpx.Response(200,json={"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}),
    httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"refusal":"no","content":None}}]}),
])
async def test_provider_failure_returns_safe_error(provider_response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:provider_response)) as client:
        provider=CompatibleProvider(Settings(model="test"),client)
        with pytest.raises(ProviderError) as error:
            await provider.extract(Source(id="x",content="消息",occurred_at=day(1)),[],[])
        assert "secret-provider-body" not in str(error.value)


async def test_missing_configuration_keeps_source_without_fake_memory(scenario):
    service=MemoryService(scenario.store,Settings())
    try:
        with pytest.raises(ProviderError,match="已保存"):
            await service.ingest(Source(id="no-model",content="负责人李四",occurred_at=day(1)))
        assert scenario.store.source("no-model") is not None
        assert scenario.store.states()==[]
    finally:
        await service.provider.close()


async def test_chinese_search_filters_history_before_limiting(scenario):
    s=scenario
    s.write("张三")
    s.now=day(10)
    s.write("李四",effective=5)
    s.write(100,key="quote",kind="rule",text="项目 A 报价 100 元",expires_at=day(9))
    service=MemoryService(s.store,Settings())
    try:
        current=await service.search(Query(query="负责人",limit=1))
        assert current["results"][0]["items"][0]["value"]=="李四"
        old=await service.search(Query(query="负责人",at=day(7),known_at=day(7),limit=1))
        assert old["results"][0]["items"][0]["value"]=="张三"
        expired=await service.search(Query(query="报价"))
        assert expired["results"]==[]
        history=await service.search(Query(query="报价",include_inactive=True))
        assert history["results"][0]["status"]=="expired"
    finally:
        await service.provider.close()


async def test_embedding_retrieval_finds_different_wording(scenario):
    s=scenario
    s.write("简短",subject="用户",key="response_style",text="用户希望回答简短")
    s.write("青岛",subject="用户",key="city",text="用户位于青岛")
    def handle(request):
        body=json.loads(request.content)
        assert request.url.path=="/v1/embeddings"
        vectors=[[1,0] if "简短" in text or "精炼" in text else [0,1] for text in body["input"]]
        return httpx.Response(200,json={"data":[{"index":i,"embedding":v} for i,v in enumerate(vectors)]})
    settings=Settings(embedding_model="test-embed")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service=MemoryService(s.store,settings,CompatibleProvider(settings,client))
        assert (await service.reindex())["indexed"]==2
        assert (await service.reindex())["indexed"]==0
        result=await service.search(Query(query="精炼",limit=1))
        assert result["results"][0]["key"]=="response_style"


async def test_embedding_failure_does_not_lose_ingested_fact(scenario):
    settings=Settings(model="test-chat",embedding_model="test-embed")
    def handle(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(503)
        return response({"memories":[memory(effective_at=day(1).isoformat())],"review":[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        service=MemoryService(scenario.store,settings,CompatibleProvider(settings,client))
        result=await service.ingest(Source(id="new",content="李四负责",occurred_at=day(1)))
        assert result["event_ids"]
        assert result["warnings"]
        assert scenario.state()["memory"]["value"]=="李四"
        assert (await service.search(Query(query="负责人")))["results"]


async def test_context_does_not_leak_current_attention_into_past(scenario):
    s=scenario
    s.write("上线",key="release",kind="plan",text="项目 A 计划上线",due_at=day(5))
    s.now=day(10)
    service=MemoryService(s.store,Settings())
    try:
        current=await service.context(Query(query="项目 A",subject="项目 A"))
        assert current["attention"][0]["status"]=="awaiting_outcome"
        past=await service.context(Query(query="项目 A",subject="项目 A",at=day(2),known_at=day(2)))
        assert past["attention"]==[]
        assert past["results"][0]["status"]=="scheduled"
    finally:
        await service.provider.close()


async def test_natural_query_time_and_unsupported_ranges(scenario):
    s=scenario
    s.write()
    s.now=day(10)
    s.write("李四",effective=5)
    replies=[{"at":day(7).isoformat(),"known_at":day(7).isoformat(),"include_inactive":False},
             {"clarification":"请使用 timeline 查看整个期间"}]
    settings=Settings(model="test-chat")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:response(replies.pop(0)))) as client:
        service=MemoryService(s.store,settings,CompatibleProvider(settings,client))
        result=await service.search(Query(query="7 日当时认为负责人是谁",interpret_time=True))
        assert result["results"][0]["items"][0]["value"]=="张三"
        result=await service.search(Query(query="整个月有哪些变化",interpret_time=True))
        assert result["status"]=="needs_clarification"


async def test_framework_neutral_tools_validate_scope_and_parameters(scenario):
    scenario.write()
    service=MemoryService(scenario.store,Settings())
    try:
        tools=MemoryTools(service)
        result=await tools.call("memory_state",{"subject":"项目 A","key":"owner"})
        assert result["ok"]
        assert result["result"]["memory"]["value"]=="张三"
        assert not (await tools.call("memory_state",{"subject":"项目 A","key":"owner","namespace":"other"}))["ok"]
        assert not (await tools.call("nonexistent",{}))["ok"]
        assert {"memory_visibility","memory_forget","memory_backup"} <= {d["name"] for d in tools.definitions()}
    finally:
        await service.provider.close()
