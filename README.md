# Daily Meal Agent

Daily Meal 的独立 Python 对话服务，使用 FastAPI 和 LangChain。Go 后端接收网页消息，读取模型配置并调用 Agent；Agent 调用一次模型，返回文字回复，由 Go 保存本轮记录。

当前学习阶段只进行文字对话，每条消息独立执行，不加载历史，也不调用工具。聊天不能查询或修改实际饮食、目标、食物与菜品数据。下一条消息不会自动接上上一条，需要补充信息时应完整重述需求。截图、流式响应和会话管理尚未接入。

共同业务规则仍读取 [总产品文档](../daily_meal/docs/product_v1.md)，本文件只说明当前实现，不改变产品文档审查状态。

## 本机启动

仓库相邻放置：

```text
Developer/
  daily_meal/          Go API 与模型配置
  daily_meal_agent/    Python 执行服务
  daily_meal_frontend/ Vue 网页
```

1. 在 Go 仓库直接编辑已有的 `backend/config/agent.yaml`，填写 `default` 下的模型名和 API Key。本机 `agent_url` 为 `http://127.0.0.1:8000`。兼容服务只支持 Chat Completions 时，将 `use_responses_api` 设为 `false`。
2. 按 Go README 设置 `DATABASE_URL`，执行 `make migrate`，然后 `make run`。
3. 在本仓库执行：

```sh
cp .env.example .env
uv sync --frozen
make run
```

Agent 使用 `.env` 中的 `BUSINESS_TIMEZONE`，默认 `Asia/Shanghai`；现有 `MEAL_API_URL` 配置在文字对话流程中不使用。模型名称、Key、模型服务地址均由 Go 随请求传入，不从 Agent 环境读取。两边业务时区应一致。

4. 在前端仓库执行 `npm ci`、`npm run dev`，打开网页的「饮食助手」。也可以直接请求 Go：

```sh
curl http://127.0.0.1:8080/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"你好，请介绍一下自己"}'
```

Go 的成功响应包括 `reply`、`tool_results` 和单轮记录信息；当前 `tool_results` 固定为空列表。前端只发送 `message`，不接触模型凭据。

## Docker Compose

本仓库 Compose 引用相邻 Go 仓库。先准备 Go 的 YAML 和 Agent 的 `.env`，把 YAML 中的 `agent_url` 设为 `http://agent:8000`，确认模型地址可从容器访问，然后执行：

```sh
docker compose -p daily_meal up --build -d
```

Go 将本地 YAML 只读挂载到 `/config/agent.yaml`；当前 Agent 只访问模型服务，不请求 Go 业务接口。没有新的 Agent 数据卷。旧版 SQLite 文件或数据卷不会自动删除或导入，新请求统一保存到 Go 数据库。

Compose 项目名影响业务数据库卷，沿用既有项目名；不要用 `down -v` 清理日常环境。前端仍从自己的仓库启动。

## 内部接口与执行过程

```text
网页 → Go /api/v1/chat
       读取 YAML default，创建 running 记录
       → Agent /api/v1/chat
           main.py     从本次 model_settings 创建模型客户端
           service.py  系统提示词 + 当前消息 → 一次模型调用 → 文字回复
       ← reply / tool_results=[]
       更新 completed 或 failed 记录
     ← 返回网页
```

内部 Agent 请求格式（仅用于说明，不填真实凭据到文档）：

```json
{
  "message": "你好，请介绍一下自己",
  "model_settings": {
    "api_key": "replace-with-your-api-key",
    "base_url": "https://api.openai.com/v1",
    "model": "replace-with-your-model-id",
    "use_responses_api": true
  }
}
```

Agent 返回 `reply` 和空的 `tool_results`，保留 Go 使用的响应结构。模型客户端每轮创建，凭据不放入提示词、日志或聊天记录。`GET /healthz` 只表明进程已初始化，不验证模型或 Go 可用性。

Agent 不接收 `request_id` 或历史上下文，不提供请求状态查询和 SQLite 重放处理。Go 的记录只保存，不读取为历史或重复请求结果；重复发送会重新调用模型。模型调用失败或没有返回文字时，Agent 返回 `502`，错误中的 `tool_results` 也为空列表，由 Go 保存失败记录。

`tools.py` 和 `client.py` 保留现有工具定义与业务 HTTP 封装，供后续学习接入；当前聊天入口不加载或调用它们。提示词说明暂不能查询或保存数据，真实模型是否遵循仍需单独验证。

## 验证

```sh
make check
make test
```

离线测试使用脚本模型与模拟 HTTP，覆盖单次文字回复、不执行工具、请求间隔离、按次模型配置、空回复和模型错误、凭据不回显，以及真实 LangChain SDK 的模拟 Responses API 请求不携带工具定义。独立工具的参数与 HTTP 测试继续保留。

要单独验证保留的工具 → Go → PostgreSQL，以及文字对话不会新增饮食记录，设置名称以 `_test` 结尾的独立数据库，再执行：

```sh
TEST_DATABASE_URL='postgres://USER:PASSWORD@localhost:5433/dailymeal_agent_test?sslmode=disable' make test-integration
```

集成测试使用随机 schema，结束后清理；不使用个人饮食数据。没有 `TEST_DATABASE_URL` 时，这部分会跳过。集成测试仍使用脚本模型，不代表真实模型验收。更多接口与测试边界见 [后端接入说明](docs/backend_integration.md)。
