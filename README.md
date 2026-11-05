# Daily Meal Agent

Daily Meal 的独立 Python 执行服务，使用 FastAPI 和 LangChain。Go 后端接收网页消息，读取模型配置并调用 Agent；Agent 通过工具访问 Go 业务 API，返回回复和工具结果，由 Go 保存本轮记录。

当前每条消息独立执行，不加载历史。支持现有的营养查询、饮食记录、目标设置、食物与菜品维护和菜单建议。信息不完整时，请用户完整重述需求；下一条消息不会自动接上上一条。截图、流式响应和会话管理尚未接入。

共同业务规则仍读取 [总产品文档](../daily_meal/docs/product_v1.md)，本文件只说明当前实现，不改变产品文档审查状态。

## 本机启动

仓库相邻放置：

```text
Developer/
  daily_meal/          Go API 与模型配置
  daily_meal_agent/    Python 执行服务
  daily_meal_frontend/ Vue 网页
```

1. 在 Go 仓库直接编辑已有的 `backend/config/agent.yaml`，填写 `default` 下的模型名和 API Key。本机 `agent_url` 为 `http://127.0.0.1:8000`。模型须支持工具调用；兼容服务只支持 Chat Completions 时，将 `use_responses_api` 设为 `false`。
2. 按 Go README 设置 `DATABASE_URL`，执行 `make migrate`，然后 `make run`。
3. 在本仓库执行：

```sh
cp .env.example .env
uv sync --frozen
make run
```

Agent 的 `.env` 只配置 `MEAL_API_URL` 和 `BUSINESS_TIMEZONE`，默认指向 `http://127.0.0.1:8080`、`Asia/Shanghai`。模型名称、Key、模型服务地址均由 Go 随请求传入，不从 Agent 环境读取。两边业务时区应一致。

4. 在前端仓库执行 `npm ci`、`npm run dev`，打开网页的「饮食助手」。也可以直接请求 Go：

```sh
curl http://127.0.0.1:8080/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"查一下今天吃了多少热量"}'
```

Go 的成功响应包括 `reply`、`tool_results` 和单轮记录信息；前端只发送 `message`，不接触模型凭据。

## Docker Compose

本仓库 Compose 引用相邻 Go 仓库。先准备 Go 的 YAML 和 Agent 的 `.env`，把 YAML 中的 `agent_url` 设为 `http://agent:8000`，确认模型地址可从容器访问，然后执行：

```sh
docker compose -p daily_meal up --build -d
```

Go 将本地 YAML 只读挂载到 `/config/agent.yaml`；Agent 容器通过 `http://api:8080` 访问业务接口。没有新的 Agent 数据卷。旧版 SQLite 文件或数据卷不会自动删除或导入，新请求统一保存到 Go 数据库。

Compose 项目名影响业务数据库卷，沿用既有项目名；不要用 `down -v` 清理日常环境。前端仍从自己的仓库启动。

## 内部接口与执行过程

```text
网页 → Go /api/v1/chat
       读取 YAML default，创建 running 记录
       → Agent /api/v1/chat
           main.py     从本次 model_settings 创建模型客户端
           service.py  当前消息 → 模型 → 工具 → 模型回复
           tools.py    表达饮食业务动作
           client.py   调用 Go HTTP API
       ← reply / tool_results
       更新 completed 或 failed 记录
     ← 返回网页
```

内部 Agent 请求格式（仅用于说明，不填真实凭据到文档）：

```json
{
  "message": "查看默认营养目标",
  "model_settings": {
    "api_key": "replace-with-your-api-key",
    "base_url": "https://api.openai.com/v1",
    "model": "replace-with-your-model-id",
    "use_responses_api": true
  }
}
```

Agent 返回 `reply` 和 `tool_results`。模型客户端每轮创建，凭据不放入提示词、工具参数、日志或聊天记录。`GET /healthz` 只表明进程已初始化，不验证模型或 Go 可用性。

本次接口变更移除了 Agent 的 `request_id`、`history`、请求状态查询和 SQLite 重放处理。Go 的记录只保存，不读取为历史或重复请求结果；重复发送会重新执行。模型调用失败时，Agent 返回 `502` 和已经收到的工具结果，由 Go 保存。工具写入不共享事务；连接或进程中断时可能缺少已执行结果，先核对实际业务数据，不自动重试。

## 验证

```sh
make check
make test
```

离线测试使用脚本模型与模拟 HTTP，覆盖单轮工具执行、请求间隔离、按次模型配置、错误与部分工具结果、凭据不回显，以及真实 LangChain SDK 的模拟 Responses API。历史和重放相关测试已改为明确验证当前的独立单轮行为。

要验证 Agent → Go → PostgreSQL，设置名称以 `_test` 结尾的独立数据库，再执行：

```sh
TEST_DATABASE_URL='postgres://USER:PASSWORD@localhost:5433/dailymeal_agent_test?sslmode=disable' make test-integration
```

集成测试使用随机 schema，结束后清理；不使用个人饮食数据。没有 `TEST_DATABASE_URL` 时，这部分会跳过。集成测试仍使用脚本模型，不代表真实模型验收。更多接口与测试边界见 [后端接入说明](docs/backend_integration.md)。
