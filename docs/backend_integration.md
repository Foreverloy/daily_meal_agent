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

- Agent 当前只组织系统提示词和本条用户消息，调用一次模型并返回文字回复；工具调用暂未接入聊天流程。
- Go 后端负责单位换算、营养计算、历史快照和最终业务写入，使用 PostgreSQL 保存业务数据。
- 当前聊天流程不调用 Go 业务 API，也不直接访问业务数据库。Go 保存模型配置及单轮请求记录；Agent 接收本次配置和消息，返回文字回复与空的 `tool_results`。
- 网页的「饮食助手」调用 Go 聊天入口；前端不传模型凭据，不加载历史上下文。

## 产品与接口依据

- [总产品文档](../../daily_meal/docs/product_v1.md)：共同的产品定位、业务规则和验收标准，只引用这一份，不复制到本仓库另立基准。
- [后端产品文档](../../daily_meal/docs/backend_product_v1.md)：Go API 的交付范围与接口验收。
- [OpenAPI](../../daily_meal/backend/openapi/openapi.json)：工具调用使用的路径、参数、响应和错误契约。
- [后端项目说明](../../daily_meal/README.md)：后端运行、迁移和开发命令。

修改工具参数、接口路径或响应处理时，先核对 OpenAPI 与实际接口行为。发现接口或实现与已确认产品需求冲突时，说明差异；对产品文档的修改仍遵守本仓库 AGENTS.md 中的审查要求。

当前 OpenAPI 标记为 2.2.0，路由仍为 `/api/v1`。后端已移除严格业务参数校验和 `Idempotency-Key` 创建去重，假定调用方提交符合约定的输入；仍返回解析、类型、资源不存在和服务错误。截图接口另用 `422` 表示识别信息不足或营养基准不匹配。产品文档中的部分校验、原子保存和重试去重验收要求尚未满足，不能将当前实现视为这些要求已经通过验收。

## 调用与运行

```text
网页 → Go /api/v1/chat → Agent main.py → service.py → 模型
                     ← reply / tool_results=[]
       Go 保存单轮记录 → 返回网页
```

`schemas.py` 描述 Agent 输入、响应和保留的工具参数。`tools.py`、`client.py` 及其独立测试保留，当前聊天服务不加载或执行这些工具，因此不能查询营养、记录饮食或修改目标。

现有 `MEAL_API_URL` 配置保留，但当前聊天流程不使用，也不创建业务 HTTP 客户端。Agent 与后端的 `BUSINESS_TIMEZONE` 应一致，默认 `Asia/Shanghai`。

当前 `compose.yaml` 通过 `include` 引用 `../daily_meal/compose.yaml`，这一套 Compose 启动方式需要相邻后端仓库。Go 仍负责聊天入口和记录保存，Agent 只访问模型服务。具体启动步骤见 [Agent README](../README.md)。

Agent 的 `/healthz` 只表示自身完成初始化，不探测模型或 Go API 的可用性。

## 集成测试

截图链路为：网页食物表单 → Go `POST /api/v1/foods/from-image` 或 `PATCH /api/v1/foods/{id}/from-image` → Python `POST /api/v1/food-recognitions` → 视觉模型提取原始数据 → Go 换算 → 食物 Service / Store 保存 → 返回食物详情。

Python 请求包含 `image_data_url` 和 `model_settings`，返回 `name`、`basis_quantity`、`basis_unit` 和 `nutrients` 数组（每项为 `key/value/unit`）。模型需同时支持视觉输入和 JSON 输出。Go 完成每 100g/ml、kJ/kcal 和营养素 g/mg/ug 换算；更新只提交已识别营养，保留缺项。原图和识别请求不持久化，Python 不直接访问业务数据库。普通聊天保持现有文字行为。

离线测试使用脚本模型与模拟 HTTP，可独立运行。真实集成测试单独验证保留的工具 → Go API → PostgreSQL，并检查文字对话不会新增饮食记录，前提为：

- 安装 Go 和 `psql`，并具备后端源码；默认使用相邻 `../daily_meal`，也可通过 `DAILY_MEAL_BACKEND_DIR` 指定后端仓库根目录。
- 使用 `TEST_DATABASE_URL` 指定名称以 `_test` 结尾的独立数据库，不能使用个人饮食数据库。
- 每次测试建立独立的随机 schema，在其中迁移、写入、验证，并在结束后清理该 schema。

满足条件后，从 Agent 仓库根目录运行 `make test-integration`。`make test` 也会收集集成测试，但未设置 `TEST_DATABASE_URL` 时会跳过；交付时须说明这一点。

集成测试中的聊天仍使用脚本模型，不代表真实模型理解或前端交互已通过验收，也不表示工具已接回聊天流程。Go 侧另有聊天入口测试，验证默认配置传递、独立执行与数据库记录。

## 请求失败与记录

模型调用失败或未返回文字时，Agent 返回 `502` 和空的工具结果，Go 保存失败记录。聊天流程没有业务工具写入，也不再包含部分工具结果或工具回滚处理。

新请求记录由 Go 保存，暂不读取历史、不重放结果。Agent 不使用 SQLite、request_id 或状态查询接口；旧 SQLite 文件不自动迁移或删除。每条消息独立执行，重复发送会重新调用模型并创建聊天记录，前端发送期间禁用按钮；服务不自动重试。

默认模型配置固定读取 Go 的 `backend/config/agent.yaml` 中 `default` 项。模型凭据由 Go 传给 Agent，只用于创建模型客户端，不进入单轮数据库记录或前端响应。
