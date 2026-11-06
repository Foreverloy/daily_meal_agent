import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from daily_meal_agent.main import create_app
from tests.conftest import ScriptedModel, payload


def test_chat_returns_text_after_one_model_call(app_factory):
    app, model = app_factory([AIMessage(content="你好，我是你的饮食助手。")])
    with app:
        response = app.post("/api/v1/chat", json=payload())
    assert response.status_code == 200
    assert response.json() == {"reply": "你好，我是你的饮食助手。", "tool_results": []}
    assert len(model.inputs) == 1
    system, user = model.inputs[0]
    assert isinstance(system, SystemMessage)
    assert isinstance(user, HumanMessage)
    assert user.content == "你好"
    assert "test-key" not in str(model.inputs)
    assert "test-key" not in response.text


def test_each_message_is_independent_and_not_replayed(app_factory):
    app, model = app_factory(
        [
            AIMessage(content="请完整说明午餐食物和用量。"),
            AIMessage(content="请说明 200g 是什么食物。"),
        ]
    )
    with app:
        first = app.post("/api/v1/chat", json=payload("午餐吃了米饭"))
        second = app.post(
            "/api/v1/chat",
            json={
                **payload("200g"),
                "history": [{"role": "user", "content": "午餐吃了米饭"}],
            },
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["tool_results"] == second.json()["tool_results"] == []
        assert len(model.inputs[1]) == 2
        assert isinstance(model.inputs[1][1], HumanMessage)
        assert model.inputs[1][1].content == "200g"
        assert app.get("/api/v1/requests/old-request").status_code == 404

    repeated, model = app_factory(
        [AIMessage(content="第一次"), AIMessage(content="第二次")]
    )
    with repeated:
        assert repeated.post("/api/v1/chat", json=payload()).json()["reply"] == "第一次"
        assert repeated.post("/api/v1/chat", json=payload()).json()["reply"] == "第二次"
    assert len(model.inputs) == 2


def test_model_tool_call_is_not_executed(app_factory, monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("文字对话不应调用 Go 业务 API")

    monkeypatch.setattr(
        "daily_meal_agent.client.MealClient.request", unexpected_request
    )
    app, model = app_factory(
        [
            AIMessage(
                content="当前只能进行文字对话。",
                tool_calls=[
                    {
                        "name": "record_recipe",
                        "args": {
                            "date": "2026-10-19",
                            "meal_type": "dinner",
                            "recipe_id": 2,
                        },
                        "id": "call_1",
                    }
                ],
            )
        ]
    )
    with app:
        response = app.post("/api/v1/chat", json=payload("帮我记录晚餐"))
    assert response.status_code == 200
    assert response.json() == {"reply": "当前只能进行文字对话。", "tool_results": []}
    assert len(model.inputs) == 1


def test_model_failure_returns_error_without_provider_secret(app_factory, caplog):
    app, model = app_factory([RuntimeError("provider exposed test-key")])
    with app:
        response = app.post("/api/v1/chat", json=payload())
    assert response.status_code == 502
    assert response.json() == {
        "error": {"message": "模型调用失败，请稍后再试。", "tool_results": []}
    }
    assert "test-key" not in response.text + caplog.text
    assert len(model.inputs) == 1


@pytest.mark.parametrize("content", ["", " \n "])
def test_empty_model_reply_returns_error(app_factory, content):
    app, model = app_factory([AIMessage(content=content)])
    with app:
        response = app.post("/api/v1/chat", json=payload())
    assert response.status_code == 502
    assert response.json() == {
        "error": {"message": "模型没有返回文字回复，本轮已停止。", "tool_results": []}
    }
    assert len(model.inputs) == 1


def test_request_model_configuration_is_isolated(monkeypatch):
    configurations, models = [], []

    def build_model(**kwargs):
        configurations.append(kwargs)
        model = ScriptedModel([AIMessage(content="收到")])
        models.append(model)
        return model

    monkeypatch.setattr("daily_meal_agent.main.ChatOpenAI", build_model)
    with TestClient(create_app()) as app:
        for key, name in [("key-a", "model-a"), ("key-b", "model-b")]:
            body = payload()
            body["model_settings"].update(api_key=key, model=name)
            response = app.post("/api/v1/chat", json=body)
            assert response.status_code == 200
            assert key not in response.text
    assert [item["api_key"] for item in configurations] == ["key-a", "key-b"]
    assert [item["model"] for item in configurations] == ["model-a", "model-b"]
    assert all(len(model.inputs) == 1 for model in models)
    assert all(len(model.inputs[0]) == 2 for model in models)


def test_validation_error_does_not_echo_credentials():
    with TestClient(create_app()) as app:
        body = payload()
        del body["model_settings"]["model"]
        response = app.post("/api/v1/chat", json=body)
    assert response.status_code == 422
    assert "test-key" not in response.text
