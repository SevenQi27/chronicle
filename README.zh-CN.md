# Chronicle Memory

**面向 Agent 的记忆服务，保留经历、证据，以及认识随时间发生的变化。**

[English](README.md) · 简体中文

Chronicle Memory 使用 Neo4j，将经历、人物、主题和有证据支撑的认识组织成带版本的记忆图，通过 JSON CLI 和 MCP 提供给 AI Agent 使用。

Agent 可以回忆发生过什么、追溯某项认识的依据，并区分现在掌握的信息与过去某一时刻已知的信息。宿主 Agent 负责理解对话，Chronicle 负责保存结果，并携带来源和时间背景进行检索。

仓库以 [英文 README](README.md) 为主，中文版本同步维护。

## 能做什么

- **保留背景**：保存完整叙述、原始消息或工具结果、涉及的实体，以及明确或模糊的发生时间。
- **关联回忆**：沿共同人物、主题、显式关联和证据关系找到相关记忆，并返回关联理由。
- **让认识有据可查**：区分用户明确表达与 Agent 推测，引用支撑它们的具体经历版本。
- **保留变化历史**：补充信息或纠正错误时生成新版本，保留此前已知的内容。
- **提示重新审视**：引用的经历版本发生变化时，将相关认识标记为 `needs_review`，补充和更正都会触发。它表示需要重新核实，不表示认识已经被判定为错误。
- **跟踪时间状态**：查询指定时点的事实、规则和计划；计划到期不等于已经完成。

例如，Agent 可以通过同一个人，将“与小林露营遇雨”和“后来收到小林的读书会邀请”关联起来。“与小林相处比具体活动更重要”这项认识必须引用相应经历。之后如果更正经历，旧版本仍会保留，受影响的认识也会提示重新审视。

## 快速开始

需要 **Python 3.11+**、**uv** 和 **Neo4j**。项目自带的安装脚本支持 macOS/Linux，安装 Neo4j Community **5.26.33**，需要 **Java 21**。

```sh
git clone https://github.com/SevenQi27/chronicle.git
cd chronicle
uv sync --locked

# 安装并启动项目本地的 Neo4j。
uv run python scripts/setup_neo4j.py
uv run chronicle init
uv run chronicle check
uv run chronicle demo
```

安装脚本核对官方发行包的 SHA-256，在 `data/runtime/` 下安装服务，并将连接凭据写入权限为 `600` 的 `.env`。下载临时文件会自动清理。Neo4j 本地连接地址为 `bolt://127.0.0.1:7687`，Browser 地址为 <http://127.0.0.1:7474>。

**已有 Neo4j？** 跳过安装脚本，参照 [`.env.example`](.env.example) 在自己的 `.env` 中配置数据库连接，再执行 `init` 和 `check`。初始化会创建约束与索引；普通 CLI/MCP 启动只检查是否已初始化。

`demo` 使用真实数据库、模拟日期和独立临时 namespace，运行结束后清理演示数据。结构化写入以及关键词、实体和图关联检索不需要额外的模型 API。

### 写入并回忆第一段经历

初始化后，在仓库目录运行：

```sh
uv run chronicle remember --namespace tutorial --input - <<'JSON'
{
  "source": {
    "id": "tutorial:message-1",
    "content": "上个月我和小林露营遇雨，提前回家，不过我们配合得很好。",
    "role": "user",
    "occurred_at": "2026-11-11T09:00:00+08:00"
  },
  "experiences": [{
    "id": "camping-with-lin",
    "title": "与小林露营遇雨",
    "narrative": "用户与小林露营时因下雨提前回家，用户认为两人配合得很好。",
    "when": {"precision": "approximate", "description": "2026 年 10 月，具体日期不详"},
    "entities": [{"id": "friend-lin", "name": "小林", "category": "person"}]
  }]
}
JSON

uv run chronicle recall "露营" --namespace tutorial
uv run chronicle history --entity-id friend-lin --namespace tutorial
```

此示例在 `tutorial` 中保存持久数据。重复提交相同来源和内容是幂等的。查看完整记忆及证据时，运行 `uv run chronicle show RECORD_ID --namespace tutorial`，将 `RECORD_ID` 替换为返回的版本 ID。

同时写入多段经历及有据可查的认识，参见 [`examples/experience.json`](examples/experience.json)。

来源较长时，可用 `chronicle show RECORD_ID --source-mode summary`：记忆正文仍完整，所有来源的 ID、角色、时间和字符数都保留，并以 `content_omitted=true` 明示省略来源正文。需要原文时使用 `--source-mode full`，默认仍是 full。摘要模式不等于整个响应有固定大小上限。

## 探索记忆图谱

启动本地只读的 **Chronicle 记忆观测台**：

```sh
uv run chronicle ui --namespace personal --port 8765
```

打开 <http://127.0.0.1:8765>，namespace 与 Agent 保持一致。页面连接已配置的 Neo4j，展示真实记忆版本、实体、来源及其关系，支持 3D 旋转视图、时间布局、文字与类型筛选，以及正文和原始证据详情。拖拽旋转、滚轮缩放，也可通过侧栏选择节点。点击刷新加载新写入的记忆。

时间滑轨筛选的是**记忆被获知的时间**，不是经历的发生时间；范围为最近加载的 200 个记忆版本及其连接，不等于完整的历史状态回放。显式关联使用当前关联视角，超出数量时页面会提示。服务仅监听 `127.0.0.1`，不提供写入接口，静态资源全部在本地，不需要 CDN 或额外前端依赖。使用页面时保持命令运行；Ctrl+C 只停止页面服务，不停止 Neo4j 或 MCP。

## 接入 Agent

有 shell 权限的 Agent 可以直接执行 CLI，也可以通过 **stdio MCP 服务**调用同一组操作。同一用户或工作空间使用一致的 namespace，以便跨会话、跨 Agent 共享记忆。

生成客户端配置片段：

```sh
uv run chronicle config codex --env-file /absolute/path/chronicle/.env --namespace personal
uv run chronicle config claude --env-file /absolute/path/chronicle/.env --namespace personal
uv run chronicle config claude-desktop --env-file /absolute/path/chronicle/.env --namespace personal
```

运行对应客户端的命令，将输出合并到其配置中。配置包含已安装的可执行文件和 `.env` 的绝对路径，凭据由服务进程读取。修改配置或工具定义后，重新加载 MCP 连接。

可以向宿主说明使用习惯：

> 使用 Chronicle 保留有背景的经历，区分我的明确表达和你的推测。回答前先回忆相关内容，必要时查看来源；谨慎处理模糊日期和临时感受。回答历史问题时，检索和详情读取使用一致的获知时间截止点。

MCP 初始化信息也包含使用指引。模型主动存取依赖宿主选择调用工具；需要自动采集对话时，启用下面的独立适配器。仅连接 MCP 不会启用自动采集。

## 自动采集对话

采集核心不依赖任何宿主，接收统一的 `CaptureEvent` JSON：`origin`、`turn_id`、`source`（消息 ID、会话 ID、角色、正文、带时区时间），以及可选的 `workspace`、`private` 字段。本地 SQLite 队列先可靠接收，worker 再将每轮对话归档为带版本的经历，保留说话者和原始证据。它**不会把助手回答自动归纳成已核实事实或长期认识**，也不需要额外模型 API。

新采集快照保存来源引用清单和内容哈希，不再嵌入另一份完整消息正文。原始消息仍以不可变来源保存，完整详情会返回它们；既有快照不重写，旧格式未完成快照仍可重试。

```sh
# 任意应用均可提交相同的事件格式。
uv run chronicle capture submit --input event.json --queue data/capture/events.sqlite3
uv run chronicle capture worker --namespace personal --queue data/capture/events.sqlite3
uv run chronicle capture status --queue data/capture/events.sqlite3
```

`accepted` / `queued` 表示队列已提交，`saved` 才表示 Neo4j 归档已提交。状态提供待处理数量、重试次数、安全错误码及已保存版本 ID。数据库不可用时，事件留在队列并按退避间隔重试；数据库提交成功、队列确认前中断，重放也不会增加重复版本。每个队列只运行一个 worker，由进程锁保证；worker 当前支持 macOS/Linux。持续运行时交给操作系统服务管理器管理；`--once` 处理一批后退出。

### 可选的 Codex 适配器

适配器独立位于 `chronicle_memory.adapters.codex`，通用采集与存储核心不导入它。在仓库环境中安装 hook：

```sh
uv run python -m chronicle_memory.adapters.codex install \
  --queue "$(pwd)/data/capture/events.sqlite3"
```

安装保留现有 hook，在 `~/.codex/hooks.json` 中增加 `UserPromptSubmit`、`Stop`。需在 Codex 的 hook 管理入口审核并信任新定义；安装器不会修改信任哈希或绕过审核。使用相同的绝对队列路径启动 worker，namespace 与 MCP 保持一致；安装 hook 不会自动启动 worker。

适配器先检查会话文件头是否明确标记为用户会话，排除后台、系统及身份不明的会话。桌面端实测使用运行时 `0.167.0-alpha.4.1`。若新聊天首条输入没有通过 `UserPromptSubmit` 到达，`Stop` 可从该会话中补齐**当前轮次、启用时间之后**的类型化 `UserMessage`。不会导入 `response_item` 中注入的指令、推理或其他轮次。这个有限的格式依赖只存在于 Codex 适配器，宿主格式变化后需重新验证。不会回填启用前的旧聊天，继续旧聊天产生的新轮次可以采集。

队列旁的 `.codex.json` 文件记录启用时间；`codex-hooks.jsonl` 只记事件标识和处理结果，不记录正文。显式私密事件、`<private>…</private>` 内容以及支持识别的凭据格式，在入队前排除或脱敏；用户要求不记录某轮时，其后续回答也不归档。禁用两个 Chronicle hook 可停止新采集；停止 worker 只暂停归档，待处理队列保留，两者均不影响 Neo4j 和 MCP。

## CLI 与 MCP 参考

CLI 与 MCP 共用输入模型、业务操作和存储逻辑。记忆命令从文件（`--input file.json`）或标准输入（`--input -`）读取 JSON，并输出 JSON。

| 用途 | CLI | MCP 工具 |
| --- | --- | --- |
| 原子保存来源、经历、认识及显式关联 | `remember` | `memory_write` |
| 返回记忆短索引、关联理由和时间状态 | `recall` | `memory_context` |
| 关键词、实体名称/别名及可选向量检索 | `search` | `memory_search` |
| 读取完整记忆与原始证据 | `show` | `memory_details` |
| 探索图中的相邻记忆 | `neighbors` | `memory_neighbors` |
| 查询记忆或实体的历史 | `history` | `memory_history` |
| 获取当前时间、时区和 namespace | `now` | `memory_now` |
| 查看和处理待确认内容 | `reviews` / `resolve` | `memory_reviews` / `memory_resolve_review` |
| 查询精确状态及变化历史 | `state` / `timeline` | `memory_state` / `memory_timeline` |
| 处理到期检查 | `tick` | `memory_tick` |
| 通过可选独立模型提取记忆 | `ingest` | `memory_ingest` |

```sh
uv run chronicle search "露营" --namespace tutorial
uv run chronicle neighbors RECORD_ID --namespace tutorial
uv run chronicle recall "露营" --namespace tutorial \
  --at 2026-10-15T00:00:00+08:00 --known-at 2026-11-12T00:00:00+08:00

# 精确状态与待确认流程；替换为自己的主体、属性和待确认项 ID。
uv run chronicle state "用户" "偏好"
uv run chronicle timeline "用户" "偏好"
uv run chronicle reviews
uv run chronicle reviews --kind understanding --limit 50 --offset 0
uv run chronicle resolve REVIEW_ID --input resolution.json
uv run chronicle checks --limit 50 --offset 0
```

`--env-file` 和 `--namespace` 可写在子命令前或后。运行前替换大写占位符。解决待确认项时，可以提交补充来源和已明确的记忆；省略记忆内容表示放弃候选。通过 `chronicle tools` 查看工具参数结构，通过 `chronicle COMMAND --help` 查看命令选项。

成功返回 `{"ok": true, "result": ...}`，失败返回 `{"ok": false, "error": {"code": ..., "message": ...}}`。版本冲突还包含 `current_record_id`，调用方应读取最新版本再决定如何修改。

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `2` | 输入、参数或命令无效 |
| `3` | 来源或版本冲突 |
| `4` | 存储、初始化或模型服务不可用 |
| `130` | 操作被中断 |

`config`、`tools`、`demo` 和 `--help` 分别输出配置片段、工具定义、演示结果和帮助文本。

`reviews` / `memory_reviews` 和 `checks` 返回含 `items`、`truncated`、`next_offset` 的分页对象，默认 50 条、最多 100 条。此前按裸列表读取的调用方需改为读取 `result.items`。复核项通过 `review_kind` 区分：`candidate` 使用 `memory_resolve_review`；`understanding` 重新核实后用 `memory_write` 更新认识和支撑证据，不能将认识 ID 当作候选 review ID。复核列表支持 `at`、`known_at`，翻阅历史结果时沿用相同时间视角。

## 记忆模型与写入约定

| 对象 | 保存内容 | 主要关系 |
| --- | --- | --- |
| `EvidenceSource` | 原始消息或工具结果、角色和时间 | `EVIDENCED_BY` |
| `Experience` | 叙述、背景、实体与时间精度 | `INVOLVES`、`REVISES`、`CORRECTS` |
| `Entity` | 稳定 ID、名称、类别和别名 | 连接涉及同一实体的经历 |
| `Understanding` | 认识内容、明确表达/推测依据与适用时间 | `SUPPORTED_BY` 指向经历版本 |
| 显式关联 | 解释、证据与适用时间 | `RELATED_TO`、`CONTINUES`、`CONTRADICTS`、`CAUSED_BY`、`REVISES` |

`Memory` 节点保存稳定的逻辑身份，通过 `HAS_REVISION` 连接各版本。正文保存在节点属性中，时间采用 Neo4j 原生 DateTime 属性，完整记录也保存为 JSON。共同实体提供回忆线索，因果关联需要单独的证据。

- 同一经历或认识复用稳定 `id`。更新时提交完整内容，并以 `replaces` 引用最新的 `record_id`。
- `revision_kind=update` 表示补充或变化，`correct` 表示更正此前错误。
- `when.precision` 支持 `exact`、`day`、`range`、`approximate` 和 `unknown`；日期不确定时保留文字描述。
- `understandings.supports` 引用经历版本 ID，或本次写入中的经历 ID。`basis=explicit` 表示用户明确表达，`inferred` 表示 Agent 归纳。
- 助手可以引用已有经历保存 inferred 认识。新经历需要用户消息或实际工具结果作为来源。
- 显式关联需要解释、依据和证据；存在适用时间时一并填写。
- 相同内容重试时复用 `source.id`；修改已保存内容时使用新的来源 ID。
- 精确事实、规则和计划通过 `memory_write.memories` 保存。
- 实体资料默认 `operation=mention`，保留规范名并追加别名。只有明确更正资料时使用 `operation=correct`，提交完整名称、类别和别名列表。这会修改规范名称查询；历史经历正文中的原始实体描述仍保留。
- 助手来源可以保存 inferred 认识及关联，不能将二者标为 explicit。

精确状态 ID 使用无歧义的主体/属性编码。旧记录仍按实际 subject/key 字段读取，无需批量重写 ID；更正会核对目标记录的两个字段。

来源、记忆版本、关系和检查项通过 Neo4j 事务提交。处理失败后，已接收的原始消息可用于重试。

## 时间与版本

Chronicle 区分三类时间：

| 时间 | 字段 | 回答的问题 |
| --- | --- | --- |
| 发生时间 | `when.start/end`、精度、描述 | 事情是什么时候发生的？ |
| 获知时间 | 服务生成的 `recorded_at` | Chronicle 什么时候知道这件事？ |
| 适用时间 | `valid_from/valid_to` | 这项认识或关联在什么时间适用？ |

`at` 指定查询的发生或适用时点；`known_at` 将可使用的信息限制在该截止时间之前。例如，11 月收到的更正可以改变现在对 10 月经历的描述，同时保留“10 月时已知什么”的查询视角。检索、上下文与详情读取应使用相同的 `at` 和 `known_at`（`show --at ... --known-at ...`）。详情会返回证据状态和认识的复核状态。

缺少可排序日期的记忆单独返回在 `memory_history.undated` 中。更正证据后，依赖它的认识会显示相应状态。规则过期后不再适用；计划到期后等待结果证据。适用性在查询时计算，到期检查记录由独立 worker 更新。

历史查询按认识的有效区间、经历的时间区间判断重叠。没有结束时间的 day 按一天处理，exact 按单点处理；缺少范围结束时间不表示事件无限持续。按实体查询历史会包含引用相关经历的认识。关联历史保留旧版本，并返回 `superseded`、`current_version_id`；关联数量也计入截断标记。

### 运行到期检查

```sh
uv run chronicle worker --namespace personal --interval 30

# 一次性补处理，也可交给外部定时器调用。
uv run chronicle tick --namespace personal
```

worker 每批最多处理 100 项检查，批次之间释放写锁，有积压时继续处理。处理到期事项时输出 JSON 行，诊断信息写入标准错误。收到 SIGINT/SIGTERM 后，完成已开始的数据库操作再退出。

CLI 查询和 MCP 服务不会启动 worker。多个 worker 共用 namespace 时，通过数据库锁和已处理标记协调。

检查结果为 conflict 且已处理，表示这次检查执行过，不表示冲突已解决；不会反复将同一检查重新入队，状态与上下文查询仍会返回冲突。upcoming 也会包含已生效、到期日在未来的未完成计划。

## 检索、并发与可选模型

检索先融合全文候选、实体匹配和可选 Neo4j 向量检索，再读取候选的历史与证据。结果返回 `candidate_count` 和 `candidate_budget`。单段记忆的版本或证据较多时，仍可能增加读取成本。

经历和认识的当前版本先在 Neo4j 中选定，再把正文返回 Python；历史接口仍保留全部版本。这减少的是传输的版本数据，不表示数据库内部工作量完全不随历史增长。对已经成功的来源重试，会只读校验原始来源和提取回执，不再进入写事务。

- 人物和证据邻居先解析查询视角下的有效版本，再按逻辑记忆排序和限量。`neighbors.truncated` 和 `recall.related_truncated` 表示未完全展开。
- 证据邻居会跨版本追溯同一逻辑经历，同时保留实际引用的版本 ID，不会将旧认识的证据边擅自移到新版。待复核认识出现在关联上下文、attention 和 `reviews --kind understanding` 中。
- 向量索引按 namespace 和模型分别建立，保留历史版本；召回按逻辑记忆去重并在预算内扩大候选窗口，预算耗尽时返回说明。选定的当前版本单独评分，避免旧版本占据其评分名额；查询指定对象历史时使用 `history`。
- 仅通过其他版本找到的经历或认识会返回 `match_scope=historical`、`matched_record_ids`。正文仍是查询时间视角下选定的版本，旧命中只作为回忆线索，不能当作当前事实；精确状态仍要求其选定状态的证据匹配查询。
- 中文关键词使用二字切分。短实体名称使用精确名称查询或显式 `--subject`，避免普通问句中的子串被误当成人名。
- 同一 namespace 的写事务通过 Neo4j 锁串行化。过期版本更新会返回冲突，不自动合并不同 Agent 的修改。
- 一次检索固定提交序号和时间视角。数据库操作在线程中执行，关闭时等待已开始的操作结束。

可选的独立提取与嵌入模型使用可配置的 Chat Completions / Embeddings 兼容服务，配置见 [`.env.example`](.env.example)。

```sh
# source.json 包含交给已配置提取模型的来源数据。
uv run chronicle ingest --input source.json

# 配置嵌入模型后，建立或补齐向量索引。
uv run chronicle reindex
```

宿主 Agent 自行提取时，直接通过 `remember` 写入，无需独立提取模型。模型服务故障时保留已提交记忆，检索返回降级说明。独立提取器合并关键词、实体与同会话来源，按逻辑记忆去重，最多 20 条；使用一致读取视角和最新可写版本 ID，包括未来才适用的版本。长全文查询按 Lucene 安全子句数量分批，保留完整来源。上下文检索异常也纳入提取失败处理，可用同一来源 ID 重试。

## 运行与当前边界

本地 Neo4j 可通过 `data/runtime/neo4j-community-5.26.33/bin/neo4j status` 和 `stop` 管理，需要使用兼容的 Java 环境。macOS 下，服务未运行时可以前台启动：

```sh
JAVA_HOME=$(/usr/libexec/java_home -v 21) \
  data/runtime/neo4j-community-5.26.33/bin/neo4j console
```

Neo4j 使用数据库账号认证，连接凭据保存在 `.env`。namespace 用于数据分区，**不是授权边界**；持有数据库凭据的客户端具有该账号授予的权限。当前服务适用于本地或受控环境。

宿主模型负责理解经历和形成认识。定期自主总结、长期压缩与遗忘、自动实体消歧尚未实现，大规模性能尚未得到验证。CLI 帮助、部分诊断信息和详细测试指南目前仍使用中文。

## 开发

更新已有安装后，运行 `uv run chronicle init` 补充状态身份索引与关联属性索引；操作幂等并保留数据。已连接的 MCP 需重新加载，以使用新的工具参数和实现。

实现位于 [`src/chronicle_memory/`](src/chronicle_memory/)：

| 文件 | 职责 |
| --- | --- |
| `store.py` | Neo4j 事务、记忆图、版本及证据 |
| `models.py` | 输入与时间约束 |
| `service.py` | 检索融合与关联上下文 |
| `cli.py` / `mcp_server.py` / `tools.py` | CLI、MCP 与共享操作分发 |
| `worker.py` | 到期检查循环 |
| `state_projection.py` | 精确状态的时间投影 |

```sh
uv run pytest -q
```

测试需要可用且已初始化的 Neo4j，使用独立 namespace 并清理自身数据。GitHub Actions 初始化独立 Neo4j 服务、执行测试并构建包。覆盖范围和宿主 Agent 验收步骤见 [测试指南](docs/testing.md)；协议检查与模型对话质量分别验证。

存取基线可用 `uv run python scripts/benchmark_memory.py --output data/benchmarks/current.json` 重跑，无需模型。它记录质量场景、读取数据量、重复写事务及本机暖缓存耗时。[优化前](docs/benchmarks/memory-before.json)与[优化后](docs/benchmarks/memory-after.json)使用相同数据集，保留同样的质量结果。这是小规模合成本机测量，不是生产规模性能结论；无词面重叠的同义改写仍缺少语义召回信号。

## 许可证

[Apache License 2.0](LICENSE)。
