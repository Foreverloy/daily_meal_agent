from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage

from daily_meal_agent.prompt import SYSTEM_PROMPT
from daily_meal_agent.schemas import ChatRequest, ChatResponse


class AgentError(Exception):
    pass


class ChatService:
    def __init__(self, model, timezone: str):
        self.model = model
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
        answer = self.model.invoke(messages)
        reply = answer.text
        if not reply.strip():
            raise AgentError("模型没有返回文字回复，本轮已停止。")
        # 保留 Go 响应字段；当前只做文字对话，没有工具结果。
        return ChatResponse(reply=reply, tool_results=[])
