import json
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from daily_meal_agent.client import MealAPIError, MealClient
from daily_meal_agent.prompt import SYSTEM_PROMPT
from daily_meal_agent.schemas import ChatRequest, ChatResponse, ToolResult
from daily_meal_agent.tools import build_tools

MAX_MODEL_TURNS = 12


class AgentError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.tool_results: list[ToolResult] = []


class ChatService:
    def __init__(self, model, client: MealClient, timezone: str):
        tools = build_tools(client)
        self.model = model.bind_tools(tools, parallel_tool_calls=False)
        self.tools = {tool.name: tool for tool in tools}
        self.timezone = ZoneInfo(timezone)

    def chat(self, request: ChatRequest) -> ChatResponse:
        # 当前只处理本条消息；记录由 Go 保存，暂不加载历史。
        messages = [
            SystemMessage(
                SYSTEM_PROMPT.format(
                    timezone=self.timezone,
                    today=datetime.now(self.timezone).date().isoformat(),
                )
            ),
            HumanMessage(request.message),
        ]
        results: list[ToolResult] = []
        try:
            for _ in range(MAX_MODEL_TURNS):
                answer = self.model.invoke(messages)
                messages.append(answer)
                if answer.invalid_tool_calls:
                    raise AgentError("模型返回了无法解析的工具参数，本轮已停止。")
                if not answer.tool_calls:
                    reply = answer.text
                    if not reply.strip():
                        raise AgentError("模型没有返回文字回复，本轮已停止。")
                    return ChatResponse(reply=reply, tool_results=results)

                for call in answer.tool_calls:
                    if call["name"] not in self.tools:
                        raise AgentError(f"模型调用了不存在的工具：{call['name']}")
                    try:
                        value = self.tools[call["name"]].invoke(call["args"])
                    except MealAPIError as error:
                        value = {"error": error.detail}
                    except ValidationError as error:
                        value = {"error": str(error)}
                    results.append(
                        ToolResult(
                            name=call["name"], arguments=call["args"], result=value
                        )
                    )
                    # TODO：工具写入不共享事务，后续失败不会回滚已成功的操作。
                    messages.append(
                        ToolMessage(
                            content=json.dumps(value, ensure_ascii=False),
                            tool_call_id=call["id"],
                        )
                    )
            raise AgentError("本轮模型调用次数达到上限，请先查看已执行结果。")
        except Exception as error:
            failure = (
                error
                if isinstance(error, AgentError)
                else AgentError(
                    "模型或业务接口调用失败，部分操作可能已经保存，请先查看相关饮食数据。"
                )
            )
            # 已收到的工具结果随失败响应交回 Go 保存，不在 Agent 落盘。
            failure.tool_results = results
            if failure is error:
                raise
            raise failure from error
