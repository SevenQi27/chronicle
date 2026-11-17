# Chronicle Memory

**面向 Agent 的时间记忆，保留经历、原始证据，以及认识随时间发生的变化。**

[English](README.md) · 简体中文

**发生过什么？当时我们知道什么？后来又更正了什么？** 一条经历或认识被补充、更正时，Chronicle 会保存新版本，并保留旧版本及其来源。Agent 可以回到过去的时点查证，也可以沿着一条认识找到支撑它的经历和原话。

## 同一件事，在不同时间知道的答案可以不同

假设 11 月 3 日入库的消息说「11 月 20 日的评审在三楼」。11 月 12 日收到更正，确认场地一直是五楼。两条消息说的是同一场评审。

| 想问的问题 | 查询时间 | 已知信息截止时间 | 查到的版本 |
| --- | --- | --- | --- |
| 11 月 8 日时，我们以为评审在哪？ | `at` 为 11 月 20 日 | `known_at` 为 11 月 8 日 | 三楼，保留当时的消息 |
| 按后来确认的信息，评审在哪？ | `at` 为 11 月 20 日 | `known_at` 为 11 月 12 日之后 | 五楼，附上更正的来源 |

两次查询的事情时间相同，变化的是当时已经知道的信息。旧版本仍可追溯，检索和详情可以使用同一个 `known_at`。

## 记忆里的三种时间

| 时间 | 保存方式 | 用途 |
| --- | --- | --- |
| **发生时间** | 经历的 `when.start`、`when.end`、精度和时间描述 | 记录经历发生在什么时候。只知道大概日期时，可以保留模糊时间或描述。 |
| **获知时间** | 每次写入由 Chronicle 记录 `recorded_at` | `known_at` 限定在指定时刻已经入库的信息，用于回看当时的版本。 |
| **适用时间** | 认识可填写 `valid_from` 和 `valid_to` | `at` 指定查询时点。检索按这个时点检查认识是否已生效、是否已经到期。 |

原始消息和工具结果也保留自己的时间 `source.occurred_at`。一条消息什么时候发出、它描述的事情什么时候发生、Chronicle 什么时候获知，可以是三个不同的时刻。

## 时间之外，记忆还保留什么

- **经历与来源**。经历保留叙述、人物或主题，以及用户原话或工具结果。认识通过 `supports` 引用同一分区中的具体经历版本，并用 `basis` 区分用户明确表达与模型归纳。
- **补充与更正**。沿用记忆的逻辑 ID，使用新来源 ID，并用 `replaces` 指向当前版本的 `record_id`。关键词、实体别名和显式提供的替代表达可以在指定时间视角下参与全文检索。
- **可见性**。撤回会隐藏一条记忆的所有版本，包括默认历史查询中的结果。原文和版本继续保存。恢复后可以重新检索。

## 快速开始

需要 Go 1.24 或更高版本。可执行文件内置 SQLite 和 FTS5 全文索引。在仓库目录运行安装命令。

```sh
go install ./cmd/chronicle
```

在 Agent 的 MCP 配置中，把 `chronicle` 可执行文件的绝对路径设为启动命令，参数填 `mcp`。首次启动会建库和建立索引，默认库为 `~/.local/share/chronicle/memory.sqlite3`。下面的参数可以指定库和分区。

```sh
chronicle mcp --sqlite /absolute/path/memory.sqlite3 --namespace personal
```

打开已有记忆库时，沿用原来的 `--sqlite`、`--namespace` 和已有的 `--capture-queue`。

### MCP 工具

| 工具 | 用途 |
| --- | --- |
| `memory_write` | 写入带来源的经历或认识，补充或更正一条记忆。 |
| `memory_search`、`memory_context` | 召回相关记忆。可指定 `at`、`known_at`，也可按来源平台、工作区、会话或证据筛选。 |
| `memory_details` | 按版本 ID 查看正文和来源。沿 `support_record_ids` 可回到认识引用的经历版本。使用 `source_mode=summary` 可略去来源正文。 |
| `memory_visibility` | 按记忆 ID 或版本 ID 执行撤回和恢复。通过 `memory_details.include_withdrawn=true` 可显式查看被撤回的版本。 |

## 在 3D 图谱里看时间和证据

```sh
chronicle ui
```

打开 [本地图谱](http://127.0.0.1:8765/)。图谱默认沿用本机可读取的 Chronicle MCP 配置，也可以显式传入 `--sqlite` 和 `--namespace`。选择「时间轨迹」，滑块按**获知时间**筛选已加载的记忆版本。点开「认识依据」和「来源证据」，可以沿关系看回原始对话。

## 可选的对话采集与自动提炼

宿主可以把对话消息提交到本地队列。MCP 连接时自动归档，断开期间排队的消息在重连后继续处理。可选的 Codex 适配器通过 `UserPromptSubmit` 和 `Stop` 采集提问和最终答复。运行下面的命令完成安装，再在 Codex 审核这两个 hook。

```sh
chronicle capture setup-codex
```

其他宿主可调用 `chronicle capture submit --input event.json`。事件包含 `origin`、`turn_id` 和 `source`，可选 `workspace` 和 `private`。可用 `chronicle capture status` 查看进度。需要在 MCP 离线时持续归档，可运行 `chronicle capture worker`，加 `--once` 只处理一批。自定义队列时，MCP 的 `--capture-queue` 和采集命令的 `--queue` 指向同一个文件。

保存来源、检索和采集无需配置模型。需要在完整对话归档后自动提炼认识时，为 `chronicle mcp` 设置 `--refine-base-url` 和 `--refine-model`。端点需要鉴权时，使用环境变量 `CHRONICLE_REFINE_API_KEY`。MCP 会把该轮原文和相关旧认识发给所配置的模型。提炼结果保留支撑经历，重跑沿用同一个归档版本的任务。

## 测试

运行 `go test ./...`。参见[测试说明](docs/testing.md)。
