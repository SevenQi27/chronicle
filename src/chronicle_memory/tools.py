"""Framework-neutral tools: the host owns the conversation and binds namespace."""

from .models import BackupRequest, DetailQuery, ForgetRequest, HistoryQuery, Model, NeighborsQuery, Query, ResolveReviewTool, ReviewsQuery, Source, StateQuery, TimelineQuery, VisibilityRequest, WriteRequest


class Empty(Model):
    pass


TOOLS = {
    "memory_visibility": (VisibilityRequest, "撤回或恢复指定逻辑记忆的全部版本。withdraw 从普通检索和详情中隐藏，restore 恢复；不删除原文。"),
    "memory_forget": (ForgetRequest, "永久遗忘：先省略 confirmation 预览共享来源、依赖认识等影响范围，获得用户对范围的明确授权后提交 token。删除全部版本和来源，保留哈希防止重放；外部备份及宿主聊天不在范围内。"),
    "memory_backup": (BackupRequest, "导出、校验或恢复当前分区。path 是服务端本地文件；export 不覆盖已有文件，validate 校验完整性并返回恢复 token，restore 仅写入空分区。包含原文、全部版本和关系，不包含采集队列或数据库凭据。"),
    "memory_ingest": (Source, "从新用户消息或工具结果自动提取记忆。传入真实消息 ID 和时间；助手复述不是新事实。"),
    "memory_write": (WriteRequest, "用 experiences 保存完整经历、entities 连接人物主题；understandings 保存有经历依据的认识并区分原话与推测；associations 保存有证据和时间的关联。更新通过 replaces 引用最新版本。memories 用于精确属性、规则和计划。"),
    "memory_search": (Query, "搜索简短记忆索引。at 指事情时间；known_at 限制当时已知信息。可启用 interpret_time 解析时间。"),
    "memory_context": (Query, "回答前回忆相关经历与认识，附关联理由和时间视角；需要原文时调用 memory_details。"),
    "memory_state": (StateQuery, "精确查询某主体属性在指定时间的状态。conflict 和 unknown 不得当作确定事实。"),
    "memory_timeline": (TimelineQuery, "查看单值属性的变化与纠错过程；经历与认识的历史使用 memory_history。"),
    "memory_details": (DetailQuery, "按 ID 读取完整记忆及复核状态；source_mode=summary 先查看来源标识和长度，需要来源正文时用 full（默认）。历史查询沿用 at 和 known_at。"),
    "memory_tick": (Empty, "处理当前已到期的检查，不执行外部操作、不自动将计划标成完成。"),
    "memory_now": (Empty, "读取当前时间、时区与绑定的记忆分区，用于解释今天、明天和安排的日期。"),
    "memory_reviews": (ReviewsQuery, "分页列出待确认候选和待复核认识。candidate 用 memory_resolve_review 处理；understanding 用 memory_write 更新认识及支持证据，不使用候选 review_id。返回 items、truncated 和 next_offset。"),
    "memory_resolve_review": (ResolveReviewTool, "根据新证据提交已明确的经历或认识来解决待确认项；不提供记忆内容表示放弃。"),
    "memory_neighbors": (NeighborsQuery, "沿真实图关系找相关经历和认识，返回关联理由；同一人物或相似主题不等于因果。"),
    "memory_history": (HistoryQuery, "按记忆或人物主题回看经历与认识的历史，支持发生时间范围和获知截止时间；模糊日期单独返回。"),
}


class MemoryTools:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def definitions():
        return [{"name":name,"description":description,"parameters":model.model_json_schema()}
                for name,(model,description) in TOOLS.items()]

    async def call(self, name, arguments):
        from .provider import ProviderError
        from neo4j.exceptions import Neo4jError, DriverError
        from .errors import error_result
        if name not in TOOLS:
            return {"ok":False,"error":{"code":"unknown_tool","message":"未知记忆工具"}}
        try:
            parsed = TOOLS[name][0].model_validate(arguments)
            store = self.service.store
            if name == "memory_ingest":
                result = await self.service.ingest(parsed)
            elif name == "memory_write":
                result = await self.service.database(store.apply,parsed.source,parsed.extraction(),context=parsed.context)
            elif name == "memory_visibility":
                from .maintenance import visibility
                result = await self.service.database(visibility,store,**parsed.model_dump())
            elif name == "memory_forget":
                from .maintenance import forget
                result = await self.service.database(forget,store,**parsed.model_dump())
            elif name == "memory_backup":
                from .backup import backup
                result = await self.service.database(backup,store,**parsed.model_dump())
            elif name == "memory_search":
                result = await self.service.search(parsed)
            elif name == "memory_context":
                result = await self.service.context(parsed)
            elif name == "memory_state":
                result = await self.service.database(store.state,**parsed.model_dump())
            elif name == "memory_timeline":
                result = await self.service.database(store.timeline,**parsed.model_dump())
            elif name == "memory_details":
                result = await self.service.database(store.details,**parsed.model_dump())
            elif name == "memory_neighbors":
                result = await self.service.database(store.neighbors,**parsed.model_dump())
            elif name == "memory_history":
                result = await self.service.database(store.history,**parsed.model_dump())
            elif name == "memory_now":
                from zoneinfo import ZoneInfo
                result = {"now":store.clock().astimezone(ZoneInfo(self.service.settings.timezone)).isoformat(),
                          "timezone":self.service.settings.timezone,"namespace":store.namespace}
            elif name == "memory_reviews":
                result = await self.service.database(store.reviews,**parsed.model_dump())
            elif name == "memory_resolve_review":
                result = await self.service.database(store.apply,parsed.source,parsed.extraction(),
                                     review_id=parsed.review_id)
            else:
                result = await self.service.database(store.tick)
            return {"ok":True,"result":result}
        except (ValueError,OSError,ProviderError,Neo4jError,DriverError) as error:
            return error_result(error)
