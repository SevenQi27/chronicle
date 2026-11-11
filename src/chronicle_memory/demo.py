"""Generic episodic-memory scenario on the real configured Neo4j database."""
from datetime import datetime
from uuid import uuid4
from .config import Settings
from .models import Source,Extraction,Experience,ExperienceTime,Entity,Understanding,Association
from .store import MemoryStore


def run_demo(settings=None):
    def day(n):
        return datetime.fromisoformat(f'2026-11-{n:02d}T09:00:00+08:00')
    now=day(1)
    namespace='demo-'+uuid4().hex[:10]
    store=MemoryStore(settings or Settings.from_env(),namespace,clock=lambda:now)
    try:
        friend=Entity(id='friend-lin',name='小林',category='person')
        first=store.apply(Source(id='chat-1',content='我和小林露营遇雨，虽然提前回家，处理意外的过程却很开心。',occurred_at=now),Extraction(
            experiences=[Experience(id='camping',title='遇雨的露营',narrative='与小林露营遇雨并提前回家，但一起处理意外的过程很开心。',entities=[friend],when=ExperienceTime(precision='approximate',description='上个月某个周末，日期不详'))],
            understandings=[Understanding(id='company',title='对此次体验的理解',statement='这次愉快感与共同应对意外有关，不能推断用户普遍喜欢恶劣天气。',basis='inferred',supports=['camping'])]))
        now=day(3)
        store.apply(Source(id='chat-2',content='今天小林邀请我去读书会，我想到上次一起处理露营意外的经历，愿意再试一次。',occurred_at=now),Extraction(
            experiences=[Experience(id='book-club',title='接受读书会邀请',narrative='小林邀请参加读书会，用户明确提起之前共同应对露营意外的经历，并愿意再尝试。',entities=[friend],when=ExperienceTime(start=now,precision='exact'))],
            associations=[Association(id='connection',from_id='book-club',to_id='camping',kind='related_to',explanation='用户明确将本次邀请与上次共同经历联系起来。',basis='explicit',evidence_ids=['chat-2'])]))
        related=store.neighbors(first['record_ids'][0])['neighbors']
        print('真实 Neo4j 演示（模拟日期，不调用模型）')
        print('找到的相关记忆：',[(n['record']['title'],[r['reason'] for r in n['reasons']]) for n in related])
        now=day(5)
        store.apply(Source(id='chat-3',content='更正一下，那次其实没觉得开心，只是我当时为了安慰朋友那么说。',occurred_at=now),Extraction(
            experiences=[Experience(id='camping',title='露营感受的更正',narrative='用户澄清当时并不觉得开心；之前的积极表达是为了安慰朋友。',entities=[friend],when=ExperienceTime(precision='approximate',description='上个月某个周末'),replaces=first['record_ids'][0],revision_kind='correct')]))
        current=next(r for r in store.records() if r['record_kind']=='understanding')
        past=next(r for r in store.records(known_at=day(2)) if r['record_kind']=='understanding')
        print('依据被纠正后，认识的状态：',current['status'])
        print('回到第 2 日当时的认识：',past['status'],past['statement'])
        print('模糊时间保留在未定日期列表：',len(store.history(entity_id='friend-lin')['undated']))
        print('演示使用独立分区，结束后清理该分区。')
    finally:
        store._run('MATCH (n {namespace:$ns}) DETACH DELETE n',ns=namespace)
        store._run('MATCH (n:MemoryNamespace {name:$ns}) DELETE n',ns=namespace)
        store.close()
