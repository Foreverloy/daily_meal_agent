import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from daily_meal_agent.client import MealClient
from daily_meal_agent.main import create_app
from daily_meal_agent.request_log import RequestLog
from daily_meal_agent.service import ChatService


class ScriptedModel:
    """按脚本返回工具调用，测试真实编排逻辑，不调用付费模型。"""

    def __init__(self, replies):
        self.replies = iter(replies)
        self.inputs = []

    def bind_tools(self, tools, **kwargs):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.inputs.append(list(messages))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def tool_call(name, arguments, call_id="call_1"):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": arguments, "id": call_id}],
    )


@pytest.fixture
def app_factory(tmp_path):
    opened = []

    def build(replies, handler):
        model = ScriptedModel(replies)
        http = httpx.Client(
            base_url="http://meal-api", transport=httpx.MockTransport(handler)
        )
        opened.append(http)
        log = RequestLog(str(tmp_path / "requests.sqlite3"))
        service = ChatService(model, MealClient(http), log, "Asia/Shanghai")
        return TestClient(create_app(service)), model, log

    yield build
    for http in opened:
        http.close()
