# Chronicle Memory

面向 Agent 的通用记忆服务。它用 Neo4j 将经历、人物、主题和逐渐形成的认识组织成记忆图，通过 CLI 和 MCP 为 Agent 提供存储、关联回忆和历史查询。

## 核心能力

- **记录经历**：保存完整叙述、背景、原始来源，以及明确或模糊的发生时间。
- **关联回忆**：沿共同人物、主题、显式关联和证据关系找到相关记忆，返回关联理由。
- **形成认识**：将用户明确表达与系统归纳分开，认识必须引用支撑它的经历。
- **保留变化**：补充和纠错形成新版本，支持查询现在的认识与过去已知的信息。
- **重新审视**：支撑经历被更正后，相关认识会标记为 `needs_review`。

## 数据结构

| 对象 | 内容 | 主要关系 |
| --- | --- | --- |
| `EvidenceSource` | 原始消息、工具结果、来源角色和时间 | `EVIDENCED_BY` |
| `Experience` | 经历正文、背景、人物与主题、时间精度 | `INVOLVES`、`REVISES`、`CORRECTS` |
| `Entity` | 人物、事物、地点或主题的稳定 ID、名称和别名 | 连接涉及同一实体的经历 |
| `Understanding` | 认识内容、表达依据、适用时间 | `SUPPORTED_BY` 指向具体经历版本 |
| 显式关联 | 关联解释、依据、有效时间 | `RELATED_TO`、`CONTINUES`、`CONTRADICTS`、`CAUSED_BY`、`REVISES` |

`Memory` 表示稳定的逻辑身份，通过 `HAS_REVISION` 连接经历或认识的版本。正文保存在节点文字属性中，时间采用 Neo4j 原生 DateTime 属性，完整结构同时保存为 JSON。

共同人物是一条回忆线索；因果关系则需要单独的依据。认识引用具体经历版本，便于追溯和重新审视。

## 安装与启动

需要 Python 3.11+、uv 和 Neo4j。项目提供 Neo4j Community 5.26.33 的本地安装脚本，使用 Java 21。

```sh
uv sync --locked
uv run python scripts/setup_neo4j.py
uv run chronicle init
uv run chronicle check
uv run chronicle demo
```

安装脚本下载官方发行包、核对 SHA-256，在 `data/runtime/` 安装并启动服务。下载文件使用系统临时目录，完成或失败后自动清理。生成的数据库凭据保存于权限为 600 的 `.env`。

连接已有 Neo4j 时，参照 `.env.example` 配置连接地址、账号、密码和数据库名。

默认本地地址：

- Neo4j Browser：`http://127.0.0.1:7474`
- Bolt：`bolt://127.0.0.1:7687`

macOS 下可在终端前台运行并查看日志：

```sh
JAVA_HOME=$(/usr/libexec/java_home -v 21) \
  data/runtime/neo4j-community-5.26.33/bin/neo4j console
```

同一目录的 `bin/neo4j status` 和 `bin/neo4j stop` 用于查看状态和停止服务，运行时需使用兼容的 Java 环境。

`chronicle demo` 使用真实 Neo4j 和模拟日期演示经历、关联、认识及历史更正。演示数据位于独立临时分区，运行结束后自动清理。

## CLI

记忆命令使用 JSON 标准输入/文件输入，输出 JSON。CLI 与 MCP 使用相同的参数模型、业务操作和存储逻辑。

```sh
# 保存完整来源和经历；JSON 结构见 examples/experience.json
uv run chronicle remember --input examples/experience.json
cat examples/experience.json | uv run chronicle remember --input -

# 先获取相关背景，再按 ID 读取完整内容
uv run chronicle recall "和小林有关的经历"
uv run chronicle search "读书会"
uv run chronicle show <record-id>
uv run chronicle neighbors <record-id>
uv run chronicle history --entity-id friend-lin

# 限制事情时间和当时获知的信息
uv run chronicle recall "当时的想法" \
  --at 2026-11-01T00:00:00+08:00 --known-at 2026-11-01T00:00:00+08:00

# 精确状态、待确认内容和检查记录
uv run chronicle state "用户" "偏好"
uv run chronicle timeline "用户" "偏好"
uv run chronicle reviews
uv run chronicle resolve <review-id> --input resolution.json
uv run chronicle checks
```

`--env-file` 和 `--namespace` 可写在子命令前或后。同一用户或工作空间使用一致的 namespace。`resolve` 的输入包含补充来源和已明确的记忆；省略记忆内容表示放弃候选。

返回格式：成功为 `{"ok": true, "result": ...}`，失败为 `{"ok": false, "error": {"code": ..., "message": ...}}`。版本冲突还返回 `current_record_id`，调用方应读取新版本再决定如何修改。

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `2` | 输入、参数或命令无效 |
| `3` | 来源或版本冲突 |
| `4` | 存储、初始化或模型服务不可用 |
| `130` | 操作被中断 |

`config`、`tools`、`demo` 和 `--help` 分别输出配置、工具定义、演示结果和帮助文本。

## 接入 Codex 与 Claude

Agent 可以执行 CLI 命令，也可以通过 MCP 调用同一组操作。宿主负责理解聊天和提取记忆，Chronicle 负责保存、检索和时间判断。

生成配置片段：

```sh
uv run chronicle config codex --env-file /absolute/path/chronicle-memory/.env --namespace personal
uv run chronicle config claude --env-file /absolute/path/chronicle-memory/.env --namespace personal
uv run chronicle config claude-desktop --env-file /absolute/path/chronicle-memory/.env --namespace personal
```

将输出的 `chronicle` 条目合并到对应客户端配置。同一个用户或工作空间使用一致的 namespace，便于跨 Agent 共享记忆。配置包含启动命令和 `.env` 路径，凭据由服务进程读取。

在项目目录注册到 Codex 用户配置：

```sh
codex mcp add chronicle -- \
  "$(pwd)/.venv/bin/chronicle" mcp \
  --env-file "$(pwd)/.env" --namespace personal
```

Claude Code 使用项目 `.mcp.json`；通过 `claude mcp get chronicle` 或 `/mcp` 查看连接状态。修改工具结构或配置后，重新加载 MCP。

可以向宿主说明使用习惯：

> 使用 Chronicle 保留有背景的经历，区分我的原话和你的推测。聊到相关话题时先回忆，再按需查看来源；谨慎区分临时感受与长期特征。

MCP 初始化信息已包含对应指引。自动存取依赖宿主模型选择调用工具；需要每轮强制采集时，应由宿主生命周期接口触发。

## 工具

| 工具 | 用途 |
| --- | --- |
| `memory_write` | 原子保存来源、经历、认识及显式关联 |
| `memory_context` | 返回相关记忆短索引、关联理由和时间状态 |
| `memory_search` | 关键词、实体名称/别名及可选向量检索 |
| `memory_details` | 批量读取完整记忆与原始证据 |
| `memory_neighbors` | 沿关联、共同实体和证据关系寻找相关记忆 |
| `memory_history` | 按记忆或实体查看版本、时间范围及关联历史 |
| `memory_now` | 获取当前时间、时区和 namespace |
| `memory_reviews` / `memory_resolve_review` | 查看和处理待确认内容 |
| `memory_state` / `memory_timeline` | 精确属性、规则和计划的状态及历史查询 |
| `memory_tick` | 处理到期检查 |

配置独立提取模型后，还可使用 `memory_ingest`。

## 写入约定

完整示例见 [examples/experience.json](examples/experience.json)。

- 同一经历或认识使用稳定 `id`。保存后返回版本 `record_id`，更新时以 `replaces` 引用最新版本并提交完整内容。
- `revision_kind=update` 表示补充或变化，`correct` 表示更正此前错误。
- `when` 支持 `exact`、`day`、`range`、`approximate` 和 `unknown`；不确定的日期保留描述。
- `understandings.supports` 引用经历版本 ID，或本批次的经历 ID。`basis=explicit` 表示用户明确表达，`inferred` 表示系统归纳。
- 助手可以引用已有经历保存 inferred 认识。新经历需要用户消息或实际工具结果作为来源。
- 显式关联需要解释、依据和时间信息；认识的证据应追溯到经历。
- `source.id` 用于重试去重。修改已保存内容时使用新的来源 ID。
- 精确属性、规则和计划通过 `memory_write.memories` 保存。

来源、记忆版本、关系和检查项通过 Neo4j 事务提交。写入失败后，已接收的原始消息可用于重试。

## 时间查询

系统区分三类时间：

1. **发生时间**：经历的 `when.start/end`、精度和时间描述。
2. **获知时间**：服务生成的 `recorded_at`。
3. **适用时间**：认识或关联的 `valid_from/valid_to`。

`at` 指定查询的事情或适用时点；`known_at` 限制可以使用的信息获知截止时间。回看“当时知道什么”时，查询和详情读取应使用同一个 known_at。

`memory_history.undated` 单独返回缺少可排序日期的记忆，保留时间的不确定性。更正经历后，派生认识的依据变化会在查询中显式标记。

规则到期后不再作为当前有效规则。计划到期进入结果待确认状态，完成需要实际证据。适用性在查询时计算；到期检查记录由独立 worker 更新。

## 独立时间检查进程

```sh
uv run chronicle worker --namespace personal --interval 30
```

`worker` 持续运行，收到 SIGINT 或 SIGTERM 后完成已开始的数据库操作并退出。可由进程管理器或 shell 放到后台运行。它只在处理到期事项时输出 JSON 行，诊断信息写入标准错误。

每批最多处理 100 项检查，批次之间释放写锁；有积压时继续处理，空闲时按设定间隔等待。

```sh
# 一次性补处理，也可交给外部定时器调用
uv run chronicle tick --namespace personal
```

CLI 查询和 MCP 服务均不启动额外 worker。多个 worker 共用同一分区时，由数据库锁和已处理标记避免重复提交检查结果。

## 并发与检索

- 同一分区的写事务使用 Neo4j 锁串行化；不同分区独立处理。
- 来源 ID 和写入内容共同用于幂等检查。已成功的来源不会被迟到的失败处理改为失败。
- 经历或认识更新必须引用最新版本。冲突返回明确错误，不自动合并不同 Agent 的修改。
- 一次检索固定提交序号和时间视角，各读取步骤使用同一份已提交记忆版本。
- 数据库操作在线程中执行，异步服务可以继续处理其他请求；关闭时等待已开始的操作结束。

普通检索先通过全文索引、实体线索和可选向量索引获得候选，再读取这些候选的历史及证据。返回 `candidate_count` 和 `candidate_budget`，便于观察检索范围。单段记忆拥有大量版本或证据时，相关历史的读取仍可能增加。

向量使用 Neo4j 原生索引，按 namespace 和模型分别建立。近似向量召回有候选预算，时间筛选可能减少召回数量；需要完整的指定对象历史时使用 `history`。

中文关键词使用二字切分。短实体名称需要精确名称查询或显式 `--subject`，避免普通问句中的子串被误当成人名。未配置嵌入模型时，召回依赖关键词、实体和图关联。

`chronicle init` 显式创建约束与基础索引，普通 CLI/MCP 启动只校验初始化标记。配置嵌入模型后执行 `chronicle reindex` 建立或补齐对应向量索引。

## 可选模型与访问边界

独立提取和向量检索使用可配置的 Chat Completions / Embeddings 兼容服务，配置项见 `.env.example`。

```sh
uv run chronicle ingest --input source.json
uv run chronicle reindex
```

宿主自行提取时直接使用 `remember`。模型服务故障时保留已提交记忆，并返回检索降级说明。

Neo4j 使用数据库账号认证，凭据保存在 `.env`。namespace 是数据分区，不是授权边界；持有数据库凭据的客户端具有该账号授予的访问权限。当前适用于本地或受控环境。

## 开发与验证

| 文件 | 职责 |
| --- | --- |
| `store.py` | Neo4j 事务、记忆图、版本及证据 |
| `models.py` | 经历、认识、关联和时间的输入约束 |
| `service.py` | 检索融合与关联上下文 |
| `cli.py` / `mcp_server.py` / `tools.py` | CLI、MCP 与共享操作分发 |
| `worker.py` | 独立时间检查循环 |
| `state_projection.py` | 精确状态的时间投影 |

```sh
uv run pytest -q
```

测试需要可用的 Neo4j 服务，使用独立分区并清理自身数据。测试覆盖范围和宿主验收步骤见 [测试说明](docs/testing.md)。

GitHub Actions 使用独立 Neo4j 服务执行初始化、测试和构建。

当前版本由宿主模型整理经历和形成认识。定期自主总结、长期压缩与遗忘、自动实体消歧尚未实现；大规模数据量的性能仍需单独验证。

安装参考：[Neo4j 部署中心](https://neo4j.com/deployment-center/)、[Java 兼容要求](https://neo4j.com/docs/operations-manual/current/installation/requirements/)。
