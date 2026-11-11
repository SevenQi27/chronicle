import json
from pathlib import Path
import subprocess
import sys
import tomllib
from uuid import uuid4
from chronicle_memory.config import Settings
from chronicle_memory.store import MemoryStore

import pytest
from mcp.client import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def connection_env():
    settings=Settings.from_env()
    return {'MEMORY_NEO4J_URI':settings.neo4j_uri,'MEMORY_NEO4J_USER':settings.neo4j_user,
            'MEMORY_NEO4J_PASSWORD':settings.neo4j_password,'MEMORY_NEO4J_DATABASE':settings.neo4j_database,
            'MEMORY_LLM_MODEL':'','MEMORY_EMBEDDING_MODEL':''}


@pytest.mark.asyncio
async def test_native_mcp_handshake_write_and_cross_session_recall(mcp_namespace):
    command=str(Path(sys.executable).with_name("chronicle"))
    namespace=mcp_namespace
    env_file=str(Path(__file__).resolve().parents[1]/".env")
    params=StdioServerParameters(command=command,args=["mcp","--env-file",env_file,"--namespace",namespace],
        env=connection_env())
    # A real child process and JSON-RPC stdio transport, with no model API.
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=15) as client:
            initialized=await client.initialize()
            assert "通用经历记忆" in initialized.instructions
            catalog=await client.list_tools()
            names={tool.name for tool in catalog.tools}
            assert "memory_write" in names and "memory_context" in names
            assert "memory_ingest" not in names
            now=(await client.call_tool("memory_now",{})).structured_content["result"]["now"]
            saved=await client.call_tool("memory_write",{
                "source":{"id":"local:codex-session:turn-1","content":"我喜欢简短回答","occurred_at":now},
                "memories":[{"subject":"用户","key":"response_style","kind":"fact","text":"用户喜欢简短回答",
                             "value":"简短","effective_at":now,"time_basis":"observed"}],
            })
            assert not saved.is_error
            assert saved.structured_content["result"]["event_ids"]
            failed=await client.call_tool("memory_state",{"subject":"用户","key":"response_style","namespace":"other"})
            assert failed.is_error
    # Another host session opens the same store and can retrieve the persisted memory.
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=15) as client:
            await client.initialize()
            found=await client.call_tool("memory_context",{"query":"回答偏好","subject":"用户"})
            assert not found.is_error
            assert found.structured_content["result"]["results"][0]["items"][0]["value"]=="简短"


@pytest.fixture
def mcp_namespace():
    namespace='test-mcp-'+uuid4().hex
    yield namespace
    store=MemoryStore(Settings.from_env(),namespace)
    store.drop_vector_indexes()
    store._run('MATCH (n {namespace:$ns}) DETACH DELETE n',ns=namespace)
    store._run('MATCH (n:MemoryNamespace {name:$ns}) DELETE n',ns=namespace)
    store.close()


async def test_native_mcp_generic_graph_tools(mcp_namespace):
    command=str(Path(sys.executable).with_name('chronicle'))
    params=StdioServerParameters(command=command,args=['mcp','--env-file',str(Path(__file__).resolve().parents[1]/'.env'),'--namespace',mcp_namespace],env=connection_env())
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write,read_timeout_seconds=15) as client:
            await client.initialize()
            now=(await client.call_tool('memory_now',{})).structured_content['result']['now']
            saved=await client.call_tool('memory_write',{
                'source':{'id':'generic','content':'散步时和朋友聊书很开心','occurred_at':now},
                'experiences':[{'id':'walk','title':'散步聊书','narrative':'和朋友散步时聊书，很开心。','when':{'precision':'unknown'}}],
                'understandings':[{'id':'this-time','title':'此次感受','statement':'这次交流很愉快，不直接推断长期偏好。','basis':'explicit','supports':['walk']}],
            })
            assert not saved.is_error
            ids=saved.structured_content['result']['record_ids']
            related=await client.call_tool('memory_neighbors',{'record_id':ids[0]})
            assert related.structured_content['result']['neighbors'][0]['record']['record_kind']=='understanding'
            found=await client.call_tool('memory_context',{'query':'散步聊书'})
            assert found.structured_content['result']['results'][0]['record_kind']=='experience'


@pytest.mark.parametrize("agent",["codex","claude","claude-desktop"])
def test_generated_host_configuration_is_valid_and_uses_absolute_paths(tmp_path,agent):
    command=str(Path(sys.executable).with_name("chronicle"))
    result=subprocess.run([command,"config",agent,"--env-file",str(tmp_path/".env")],
                          check=True,capture_output=True,text=True)
    data=tomllib.loads(result.stdout) if agent=="codex" else json.loads(result.stdout)
    entry=data["mcp_servers" if agent=="codex" else "mcpServers"]["chronicle"]
    assert Path(entry["command"]).is_absolute()
    assert entry["args"][0]=="mcp"
    assert Path(entry["args"][2]).is_absolute()
