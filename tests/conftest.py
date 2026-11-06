import pytest
from fastapi.testclient import TestClient

from daily_meal_agent.main import create_app
from daily_meal_agent.service import ChatService


class ScriptedModel:
    """按脚本返回模型回复，不调用付费模型。"""

    def __init__(self, replies):
        self.replies = iter(replies)
        self.inputs = []

    def invoke(self, messages):
        self.inputs.append(list(messages))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def app_factory():
    def build(replies):
        model = ScriptedModel(replies)
        service = ChatService(model, "Asia/Shanghai")
        return TestClient(create_app(service)), model

    return build


def payload(message="你好"):
    return {
        "message": message,
        "model_settings": {
            "api_key": "test-key",
            "base_url": "http://model/v1",
            "model": "test-model",
            "use_responses_api": True,
        },
    }
