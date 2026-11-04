# Daily Meal Agent

Daily Meal 的独立 Python 对话服务，使用 FastAPI 和 LangChain。通过具名工具调用相邻 `daily_meal` 仓库的 Go API，业务计算与业务数据继续由 Go 管理。

这一版接通文字对话：查询营养与目标、记录/修改/删除饮食、设置目标、维护食物和菜品模板，以及根据现有数据给出菜单建议。食物匹配、用量或餐次不明确时，提示词要求模型补问。模型实际能否准确理解具体表达，需要用你配置的模型验收。

截图录入、SSE、前端聊天界面和临时菜品配方尚未接入。临时改配方不会自动修改模板，当前会说明限制。业务范围和共同约定仍读取 [总产品文档](../daily_meal/docs/product_v1.md)；本文件说明当前实现，不改变产品文档的审查状态。

## 启动

仓库相邻放置：

```text
Developer/
  daily_meal/          Go 业务 API
  daily_meal_agent/    当前 Python 服务
  daily_meal_frontend/ 网页
```

在当前目录复制 `.env.example` 为 `.env`，填写自己的 `OPENAI_API_KEY` 和 `OPENAI_MODEL`。模型须支持工具调用；默认使用 OpenAI Responses API。使用兼容服务时配置 `OPENAI_BASE_URL`，若该服务仅支持 Chat Completions，则将 `OPENAI_USE_RESPONSES_API` 设为 `false`。兼容服务支持程度取决于提供方。

已有 Go API 在 `http://127.0.0.1:8080` 运行时，安装 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/) 后执行：

```sh
uv sync --frozen
make run
```

访问 `http://127.0.0.1:8000/docs` 可在 FastAPI 文档页发送请求。Agent 会读取当前目录的 `.env`；Go 本机运行仍按它自己的 README 设置 `DATABASE_URL`。两边的 `BUSINESS_TIMEZONE` 应一致，默认 `Asia/Shanghai`。

也可以使用 Docker Compose 同时启动数据库、迁移、Go API 和 Agent：

```sh
docker compose -p daily_meal up --build -d
curl http://127.0.0.1:8000/healthz
```

Compose 通过 `include` 引用相邻 Go 仓库的配置，要求 Docker Compose 2.20+。`-p daily_meal` 与 Go 仓库默认项目名一致；已有使用其他项目名或端口的学习环境，请先停止冲突的服务或使用本机 Python 启动方式。数据库 volume 由 Compose 项目名决定，随意更换项目名会连接另一套数据。不要用 `down -v` 清理日常环境。

`/healthz` 只证明 Agent 进程完成初始化，不探测模型额度或 Go 可用性。默认仅监听本机，不包含登录或多用户功能。

## 发送消息与补问

```sh
curl http://127.0.0.1:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "28b4e2e7-165b-4270-9fb9-f7f19686ba7d",
    "message": "查一下今天吃了多少热量",
    "history": []
  }'
```

每条**新消息**使用新的 UUID；重复发送同一条消息必须复用原 `request_id` 和完整请求体。响应包含：

- `reply`：助手的文字回复。
- `history`：原历史加本轮用户消息和回复；下一条消息把它原样传回。
- `tool_results`：本轮工具名、参数和 Go 返回值，方便学习和排查。

例如用户先说“午餐吃了米饭”，助手补问用量。下一次请求用新 UUID，`message` 为“200g”，`history` 使用上次响应返回的数组。服务不维护隐藏的聊天 session，历史只允许 user/assistant 文字消息。

用法示例：

- “把默认目标设为 2000 大卡，蛋白质 30%、碳水 45%、脂肪 25%。”
- “查找食物库里的牛奶。”
- “今天早餐喝了 250ml 的某品牌牛奶。”
- “今天午餐的米饭改成 150g。”
- “今天还有多少热量和蛋白质额度？”
- “根据今天的记录，推荐晚餐。”（推荐不代表实际摄入。）

## 请求状态与重复发送

`data/requests.sqlite3` 只保存 Agent 请求日志，不保存独立的一套饮食业务表。一次请求从 `running` 变为 `completed` 或 `failed`。SQLite 的主键保证同一 UUID 只有一次执行机会。

- 已完成：原请求重发时直接返回保存的响应，模型和 Go 都不会再调用。
- 同 UUID、不同输入：返回 `409`。
- 执行中或已失败：返回 `409`，先查状态和 Go 的实际数据，不能换 UUID 盲目重跑。
- 模型/Go 调用中断：返回 `502`，之前可能已有部分写入成功；多次工具调用不共享一个事务。

```sh
curl http://127.0.0.1:8000/api/v1/requests/28b4e2e7-165b-4270-9fb9-f7f19686ba7d
```

状态接口返回 `status`、已经收到结果的 `tool_results`、最终 `response` 和 `error`。进程中断可能留下 `running`；网络中断也可能让一次写入没有出现在结果列表里，因此日志没有结果不等于 Go 没有写入。该版本不自动恢复中断的请求。先通过 Go 接口核对实际数据，再发新的查询或修正消息。

请求重放由 Agent 的 SQLite 日志处理，请求日志要保留；Docker 中使用 `agent_requests` volume。不要为重试清空日志。当前 Go API 每次成功的记餐 POST 都会新建记录，不再处理 `Idempotency-Key`，Agent 也不再发送该请求头。同一请求 ID 的整轮重放会被拦住，但模型在一轮内重复调用记餐工具，或用新请求 ID 重复记餐，仍可能产生多条记录。

## 按这条路径读代码

```text
POST /api/v1/chat
  main.py          解析请求、检查 request_id、保存最终结果
    service.py     用户消息 → 模型 → 工具 → 模型回复的循环
      tools.py     search_foods / record_food 等业务动作
        client.py  HTTP 请求和 Go 错误处理
          Go API   Handler → Service → Store → PostgreSQL
```

建议先看 `tools.py` 的 `record_food`，对照 Go 的 `POST /api/v1/meal-entries`；再看 `service.py` 怎样把工具返回值放进 `ToolMessage`，最后读 `main.py` 和 `request_log.py`。

`schemas.py` 描述输入形状，`prompt.py` 描述模型使用工具的方式。服务初始化时创建工具并通过 `bind_tools` 绑定到模型；每次请求独立组织消息和执行结果，通过 `invoke` 循环调用。`request_log.py` 负责将请求、工具结果和最终响应序列化后存入 SQLite。

单位换算和营养计算由业务 API 完成。当前后端按合法输入约定处理请求，已移除严格业务参数校验；Agent 也不另建一套业务校验逻辑。接口依据和联调条件见 [后端接入说明](docs/backend_integration.md)。

接入依据：[LangChain ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai) 和 [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)。

## 验证

```sh
make check
make test
```

离线测试使用固定模型回复和模拟 HTTP，覆盖调用编排、补问上下文、API 错误、PATCH、省略与 null、重复请求、并发请求、模型失败和请求状态；另有真实 LangChain/OpenAI SDK 的模拟 Responses API 测试。无 `TEST_DATABASE_URL` 时，真实 Go 集成测试明确跳过。CI 执行这些离线检查，不需要 API 密钥。

要验证 Python → Go → PostgreSQL，安装 Go 和 `psql`，准备独立测试库：

```sh
# 用户名、端口按本机 PostgreSQL 替换；不能使用个人饮食库。
createdb -h 127.0.0.1 -U postgres daily_meal_agent_test
export TEST_DATABASE_URL='postgres://postgres@127.0.0.1:5432/daily_meal_agent_test?sslmode=disable'
make test-integration
```

默认使用相邻 `../daily_meal`；可用 `DAILY_MEAL_BACKEND_DIR` 指定 Go 仓库根目录。测试编译并临时启动 Go API，在随机 schema 内迁移、写入、验证后清理，不启动日常 Compose 数据库。

集成测试遍历所有业务工具，验证真实营养汇总、同类单位换算、历史菜品快照、目标覆盖、资源不存在的错误、后端重复创建行为及 Agent 聊天重放。这仍不证明真实模型会正确理解用户意图；配置自己的模型后，应通过 `/docs` 检查明确记餐、歧义补问、推荐不记餐和临时配方限制。
