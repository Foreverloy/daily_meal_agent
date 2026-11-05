import json

import httpx
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daily_meal_agent.main import create_app
from tests.conftest import ScriptedModel, payload, tool_call


def test_search_record_summary_returns_all_tool_results(app_factory):
    calls = []

    def go_api(request):
        calls.append(request)
        if request.url.path.endswith("/foods"):
            return httpx.Response(200, json={"items": [{"id": 7, "name": "米饭"}]})
        if request.method == "POST":
            assert json.loads(request.content)["quantity"] == 200
            assert "Idempotency-Key" not in request.headers
            return httpx.Response(201, json={"id": 9, "nutrition_totals": {}})
        return httpx.Response(200, json={"total": {"energy_kcal": 232}})

    app, model = app_factory(
        [
            tool_call("search_foods", {"query": "米饭"}),
            tool_call(
                "record_food",
                dict(
                    date="2026-10-19",
                    meal_type="lunch",
                    food_id=7,
                    quantity=200,
                    unit="g",
                ),
                "call_2",
            ),
            tool_call("daily_summary", {"date": "2026-10-19"}, "call_3"),
            AIMessage(content="已记录午餐米饭 200g，热量 232 kcal。"),
        ],
        go_api,
    )
    with app:
        response = app.post("/api/v1/chat", json=payload())
    assert response.status_code == 200
    assert len(response.json()["tool_results"]) == 3
    assert "history" not in response.json()
    assert isinstance(model.inputs[1][-1], ToolMessage)
    assert len(calls) == 3
    assert "test-key" not in str(model.inputs)
    assert "test-key" not in response.text


def test_each_message_is_independent_and_not_replayed(app_factory):
    app, model = app_factory(
        [
            AIMessage(content="请完整说明午餐食物和用量。"),
            AIMessage(content="请说明 200g 是什么食物。"),
        ],
        lambda request: (_ for _ in ()).throw(AssertionError("补问不应写入")),
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
        [AIMessage(content="第一次"), AIMessage(content="第二次")], lambda request: None
    )
    with repeated:
        assert (
            repeated.post("/api/v1/chat", json=payload("你好")).json()["reply"]
            == "第一次"
        )
        assert (
            repeated.post("/api/v1/chat", json=payload("你好")).json()["reply"]
            == "第二次"
        )
    assert len(model.inputs) == 2


def test_api_error_is_returned_to_model(app_factory):
    error = {"code": "not_found", "message": "食物不存在", "fields": {}}
    app, model = app_factory(
        [
            tool_call("get_food", {"food_id": 7}),
            AIMessage(content="该食物已不存在，请重新查询。"),
        ],
        lambda request: httpx.Response(404, json={"error": error}),
    )
    with app:
        result = app.post("/api/v1/chat", json=payload())
    assert result.status_code == 200
    assert result.json()["tool_results"][0]["result"]["error"] == error
    assert json.loads(model.inputs[1][-1].content)["error"] == error


def test_multiple_tools_keep_call_ids_and_next_request_is_empty(app_factory):
    app, model = app_factory(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_foods", "args": {"query": "米饭"}, "id": "food"},
                    {"name": "get_goal", "args": {}, "id": "goal"},
                ],
            ),
            AIMessage(content="已查到米饭和目标。"),
            AIMessage(content="请完整说明本次需求。"),
        ],
        lambda request: httpx.Response(200, json={"items": []}),
    )
    with app:
        result = app.post("/api/v1/chat", json=payload("查一下米饭和目标"))
        assert result.status_code == 200
        messages = model.inputs[1][-2:]
        assert [message.tool_call_id for message in messages] == ["food", "goal"]
        assert (
            app.post("/api/v1/chat", json=payload("我刚才查了什么？")).json()[
                "tool_results"
            ]
            == []
        )
        assert not any(isinstance(item, ToolMessage) for item in model.inputs[2])


def test_failure_after_write_returns_partial_results_without_provider_secret(
    app_factory, caplog
):
    calls = []

    def write(request):
        calls.append(request)
        return httpx.Response(201, json={"id": 12})

    app, _ = app_factory(
        [
            tool_call(
                "record_recipe",
                dict(date="2026-10-19", meal_type="dinner", recipe_id=2),
            ),
            RuntimeError("provider exposed test-key"),
        ],
        write,
    )
    with app:
        response = app.post("/api/v1/chat", json=payload())
    assert response.status_code == 502
    assert response.json()["error"]["tool_results"][0]["result"]["id"] == 12
    assert "test-key" not in response.text + caplog.text
    assert len(calls) == 1


def test_uncertain_http_write_is_not_retried(app_factory):
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost", request=request)

    app, _ = app_factory(
        [
            tool_call(
                "create_food",
                {
                    "food": {
                        "name": "牛奶",
                        "basis_unit": "ml",
                        "nutrition_per_100": {"energy_kcal": 60},
                    }
                },
            )
        ],
        timeout,
    )
    with app:
        response = app.post("/api/v1/chat", json=payload())
    assert response.status_code == 502
    assert response.json()["error"]["tool_results"] == []
    assert len(calls) == 1


def test_loop_limit_returns_executed_tools(app_factory):
    app, _ = app_factory(
        [tool_call("get_goal", {}) for _ in range(12)],
        lambda request: httpx.Response(200, content="null"),
    )
    with app:
        response = app.post("/api/v1/chat", json=payload("查看目标"))
    assert response.status_code == 502
    assert len(response.json()["error"]["tool_results"]) == 12


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
            body = payload("你好")
            body["model_settings"].update(api_key=key, model=name)
            response = app.post("/api/v1/chat", json=body)
            assert response.status_code == 200
            assert key not in response.text
    assert [item["api_key"] for item in configurations] == ["key-a", "key-b"]
    assert [item["model"] for item in configurations] == ["model-a", "model-b"]
    assert all(len(model.inputs[0]) == 2 for model in models)


def test_validation_error_does_not_echo_credentials():
    with TestClient(create_app()) as app:
        body = payload()
        del body["model_settings"]["model"]
        response = app.post("/api/v1/chat", json=body)
    assert response.status_code == 422
    assert "test-key" not in response.text
