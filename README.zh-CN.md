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
- **提示重新审视**：支撑经历被更正后，将相关认识标记为 `needs_review`。
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

MCP 初始化信息也包含使用指引。自动存取依赖宿主模型选择调用工具；需要每轮强制采集时，应由宿主生命周期接口触发。

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
uv run chronicle resolve REVIEW_ID --input resolution.json
uv run chronicle checks
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

来源、记忆版本、关系和检查项通过 Neo4j 事务提交。处理失败后，已接收的原始消息可用于重试。

## 时间与版本

Chronicle 区分三类时间：

| 时间 | 字段 | 回答的问题 |
| --- | --- | --- |
| 发生时间 | `when.start/end`、精度、描述 | 事情是什么时候发生的？ |
| 获知时间 | 服务生成的 `recorded_at` | Chronicle 什么时候知道这件事？ |
| 适用时间 | `valid_from/valid_to` | 这项认识或关联在什么时间适用？ |

`at` 指定查询的发生或适用时点；`known_at` 将可使用的信息限制在该截止时间之前。例如，11 月收到的更正可以改变现在对 10 月经历的描述，同时保留“10 月时已知什么”的查询视角。检索、上下文与详情读取应使用相同的 `known_at`。

缺少可排序日期的记忆单独返回在 `memory_history.undated` 中。更正证据后，依赖它的认识会显示相应状态。规则过期后不再适用；计划到期后等待结果证据。适用性在查询时计算，到期检查记录由独立 worker 更新。

### 运行到期检查

```sh
uv run chronicle worker --namespace personal --interval 30

# 一次性补处理，也可交给外部定时器调用。
uv run chronicle tick --namespace personal
```

worker 每批最多处理 100 项检查，批次之间释放写锁，有积压时继续处理。处理到期事项时输出 JSON 行，诊断信息写入标准错误。收到 SIGINT/SIGTERM 后，完成已开始的数据库操作再退出。

CLI 查询和 MCP 服务不会启动 worker。多个 worker 共用 namespace 时，通过数据库锁和已处理标记协调。

## 检索、并发与可选模型

检索先融合全文候选、实体匹配和可选 Neo4j 向量检索，再读取候选的历史与证据。结果返回 `candidate_count` 和 `candidate_budget`。单段记忆的版本或证据较多时，仍可能增加读取成本。

- 人物和证据邻居先解析查询视角下的有效版本，再按逻辑记忆排序和限量。`neighbors.truncated` 和 `recall.related_truncated` 表示未完全展开。
- 向量索引按 namespace 和模型分别建立。近似检索有候选预算，时间筛选可能减少结果；查询指定对象历史时使用 `history`。
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

宿主 Agent 自行提取时，直接通过 `remember` 写入，无需独立提取模型。模型服务故障时保留已提交记忆，检索返回降级说明。独立提取器未命中关键词时，会补充实体关联和同一会话近期来源对应的结构化记录，合计最多 20 条，使用一致的读取视角。

## 运行与当前边界

本地 Neo4j 可通过 `data/runtime/neo4j-community-5.26.33/bin/neo4j status` 和 `stop` 管理，需要使用兼容的 Java 环境。macOS 下，服务未运行时可以前台启动：

```sh
JAVA_HOME=$(/usr/libexec/java_home -v 21) \
  data/runtime/neo4j-community-5.26.33/bin/neo4j console
```

Neo4j 使用数据库账号认证，连接凭据保存在 `.env`。namespace 用于数据分区，**不是授权边界**；持有数据库凭据的客户端具有该账号授予的权限。当前服务适用于本地或受控环境。

宿主模型负责理解经历和形成认识。定期自主总结、长期压缩与遗忘、自动实体消歧尚未实现，大规模性能尚未得到验证。CLI 帮助、部分诊断信息和详细测试指南目前仍使用中文。

## 开发

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

## 许可证

[Apache License 2.0](LICENSE)。
