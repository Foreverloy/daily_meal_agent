import json
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from daily_meal_agent.client import MealAPIError, MealClient
from daily_meal_agent.prompt import SYSTEM_PROMPT
from daily_meal_agent.request_log import RequestLog
from daily_meal_agent.schemas import ChatRequest, ChatResponse, Message, ToolResult
from daily_meal_agent.tools import build_tools

MAX_MODEL_TURNS = 12


class AgentError(Exception):
    pass


class ChatService:
    def __init__(self, model, client: MealClient, log: RequestLog, timezone: str):
        tools = build_tools(client)
        self.model = model.bind_tools(tools, parallel_tool_calls=False)
        self.tools = {tool.name: tool for tool in tools}
        self.log = log
        self.timezone = ZoneInfo(timezone)

    def chat(self, request: ChatRequest) -> ChatResponse:
        # 工具在初始化时绑定；消息和执行结果只属于当前这次请求。
        messages = [
            SystemMessage(
                SYSTEM_PROMPT.format(
                    timezone=self.timezone,
                    today=datetime.now(self.timezone).date().isoformat(),
                )
            )
        ]
        for item in request.history:
            message_type = HumanMessage if item.role == "user" else AIMessage
            messages.append(message_type(item.content))
        messages.append(HumanMessage(request.message))
        results: list[ToolResult] = []

        # 一次循环：模型决定下一步 → 执行工具 → 把结果交回模型。
        for _ in range(MAX_MODEL_TURNS):
            answer = self.model.invoke(messages)
            messages.append(answer)
            if answer.invalid_tool_calls:
                raise AgentError("模型返回了无法解析的工具参数，本轮已停止。")
            if not answer.tool_calls:
                reply = answer.text
                if not reply.strip():
                    raise AgentError("模型没有返回文字回复，本轮已停止。")
                return ChatResponse(
                    request_id=request.request_id,
                    reply=reply,
                    history=[
                        *request.history,
                        Message(role="user", content=request.message),
                        Message(role="assistant", content=reply),
                    ],
                    tool_results=results,
                )

            for call in answer.tool_calls:
                if call["name"] not in self.tools:
                    raise AgentError(f"模型调用了不存在的工具：{call['name']}")
                try:
                    value = self.tools[call["name"]].invoke(call["args"])
                except MealAPIError as error:
                    value = {"error": error.detail}
                except ValidationError as error:
                    # 这里只处理工具参数形状；不另写一套业务校验。
                    value = {"error": str(error)}
                results.append(
                    ToolResult(name=call["name"], arguments=call["args"], result=value)
                )
                # TODO：多次写入不共享事务，后续失败不会回滚已成功的工具操作。
                self.log.record_tools(str(request.request_id), results)
                messages.append(
                    ToolMessage(
                        content=json.dumps(value, ensure_ascii=False),
                        tool_call_id=call["id"],
                    )
                )

        raise AgentError("本轮模型调用次数达到上限，请先查看已执行结果，再继续对话。")
