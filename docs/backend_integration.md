# Daily Meal Agent 与后端接入说明

本文说明当前 Agent 与 Daily Meal Go 后端的协作方式、接口依据和联调条件。它记录现有实现关系，不新增产品规则，也不改变产品文档的审查状态。

## 仓库与职责

Daily Meal Agent、Go 后端和网页分别维护在独立仓库中。本机默认目录为：

```text
Developer/
  daily_meal_agent/    Python Agent
  daily_meal/          Go 业务后端
  daily_meal_frontend/ 网页
```

- Agent 负责理解用户消息、组织对话上下文、选择并执行工具，以及将工具结果交给模型生成回复。
- Go 后端负责单位换算、营养计算、历史快照和最终业务写入，使用 PostgreSQL 保存业务数据。
- Agent 只通过 HTTP/JSON 调用 Go API，不直接访问业务数据库。Go 保存模型配置及单轮请求记录；Agent 接收本次配置和消息，只返回执行结果。
- 网页的「饮食助手」调用 Go 聊天入口；前端不传模型凭据，不加载历史上下文。

## 产品与接口依据

- [总产品文档](../../daily_meal/docs/product_v1.md)：共同的产品定位、业务规则和验收标准，只引用这一份，不复制到本仓库另立基准。
- [后端产品文档](../../daily_meal/docs/backend_product_v1.md)：Go API 的交付范围与接口验收。
- [OpenAPI](../../daily_meal/backend/openapi/openapi.json)：工具调用使用的路径、参数、响应和错误契约。
- [后端项目说明](../../daily_meal/README.md)：后端运行、迁移和开发命令。

修改工具参数、接口路径或响应处理时，先核对 OpenAPI 与实际接口行为。发现接口或实现与已确认产品需求冲突时，说明差异；对产品文档的修改仍遵守本仓库 AGENTS.md 中的审查要求。

当前 OpenAPI 标记为 2.1.0，路由仍为 `/api/v1`。后端已移除严格业务参数校验和 `Idempotency-Key` 创建去重，假定调用方提交符合约定的输入；仍返回解析、类型、资源不存在和服务错误。产品文档中的部分校验、原子保存和重试去重验收要求尚未满足，不能将当前实现视为这些要求已经通过验收。

## 调用与运行

```text
网页 → Go /api/v1/chat → Agent main.py → service.py → tools.py → client.py
  → Go HTTP API → Handler → Service → Store → PostgreSQL
```

`schemas.py` 描述 Agent 输入和工具参数的形状，不另建业务参数校验，也不重复实现营养计算。模型通过工具取得业务数据，写入结果以 Go API 的实际返回为依据。

本机运行时，`MEAL_API_URL` 默认指向 `http://127.0.0.1:8080`，可配置为实际 API 地址；连接已运行的 API 不要求后端源码位于本机。Agent 与后端的 `BUSINESS_TIMEZONE` 应一致，默认 `Asia/Shanghai`。

当前 `compose.yaml` 通过 `include` 引用 `../daily_meal/compose.yaml`，容器内通过 `http://api:8080` 访问后端，因此这一套 Compose 启动方式需要相邻后端仓库。具体启动步骤见 [Agent README](../README.md)。

Agent 的 `/healthz` 只表示自身完成初始化，不探测模型或 Go API 的可用性。

## 集成测试

离线测试使用脚本模型与模拟 HTTP，可独立运行。真实集成测试的调用范围是 Python Agent → Go API → PostgreSQL，前提为：

- 安装 Go 和 `psql`，并具备后端源码；默认使用相邻 `../daily_meal`，也可通过 `DAILY_MEAL_BACKEND_DIR` 指定后端仓库根目录。
- 使用 `TEST_DATABASE_URL` 指定名称以 `_test` 结尾的独立数据库，不能使用个人饮食数据库。
- 每次测试建立独立的随机 schema，在其中迁移、写入、验证，并在结束后清理该 schema。

满足条件后，从 Agent 仓库根目录运行 `make test-integration`。`make test` 也会收集集成测试，但未设置 `TEST_DATABASE_URL` 时会跳过；交付时须说明这一点。

集成测试仍使用脚本模型，只验证接口、编排和数据链路，不代表真实模型理解、截图识别或前端交互已通过验收。Go 侧另有聊天入口测试，验证默认配置传递、独立执行与数据库记录。

## 请求失败与写入结果

一次对话可能调用多个工具，这些调用不共享一个业务事务。Agent 在失败响应中返回已经收到的工具结果，Go 保存到 `chat_requests` 并返回错误。连接或进程中断可能使记录不完整，先核对实际业务数据。

本次按用户确认的范围，将新请求记录交给 Go 保存，但暂不读取历史、不重放结果。Agent 不再使用 SQLite、request_id 或状态查询接口；旧 SQLite 文件不自动迁移或删除。每条消息独立执行，重复发送可能重复写入，前端发送期间禁用按钮；服务不自动重试。

默认模型配置固定读取 Go 的 `backend/config/agent.yaml` 中 `default` 项。模型凭据由 Go 传给 Agent，只用于创建模型客户端，不进入单轮数据库记录或前端响应。
