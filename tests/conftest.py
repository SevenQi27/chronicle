from datetime import datetime
import pytest

from chronicle_memory.config import Settings
from uuid import uuid4
from chronicle_memory.models import Extraction, MemoryChange, Source
from chronicle_memory.store import MemoryStore


@pytest.fixture(scope='session',autouse=True)
def initialized_graph():
    store=MemoryStore(Settings.from_env(),require_initialized=False)
    store.initialize()
    store.close()


def day(number):
    return datetime.fromisoformat(f"2026-11-{number:02d}T09:00:00+08:00")


class Scenario:
    def __init__(self):
        self.now = day(1)
        self.settings = Settings.from_env()
        self.namespace = "test-"+uuid4().hex
        self.store = MemoryStore(self.settings,self.namespace,clock=lambda:self.now)
        self.sequence = 0

    def write(self,value="张三",effective=1,**overrides):
        self.sequence += 1
        change = MemoryChange(**{
            "subject":"项目 A","key":"owner","kind":"fact","value":value,
            "text":f"项目 A 负责人是 {value}","effective_at":day(effective),**overrides,
        })
        source = Source(id=f"message:{self.sequence}",content=change.text,occurred_at=self.now)
        result = self.store.apply(source,Extraction(memories=[change]))
        return result["event_ids"][0]

    def state(self,at=None,known=None,key="owner"):
        return self.store.state("项目 A",key,day(at) if at else None,day(known) if known else None)


@pytest.fixture
def scenario():
    item = Scenario()
    yield item
    item.store.drop_vector_indexes()
    item.store._run("MATCH (n {namespace:$ns}) DETACH DELETE n",ns=item.namespace)
    item.store._run("MATCH (n:MemoryNamespace {name:$ns}) DELETE n",ns=item.namespace)
    item.store.close()
