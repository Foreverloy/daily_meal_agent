import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from daily_meal_agent.request_log import RequestLog
from daily_meal_agent.schemas import ChatRequest
from tests.conftest import tool_call


def payload(message="今天午餐吃了 200g 米饭"):
    return {"request_id": str(uuid4()), "message": message}


def test_search_record_summary_and_replay_after_restart(app_factory):
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

    replies = [
        tool_call("search_foods", {"query": "米饭"}),
        tool_call(
            "record_food",
            {
                "date": "2026-10-19",
                "meal_type": "lunch",
                "food_id": 7,
                "quantity": 200,
                "unit": "g",
            },
            "call_2",
        ),
        tool_call("daily_summary", {"date": "2026-10-19"}, "call_3"),
        AIMessage(content="已记录午餐米饭 200g，热量 232 kcal。"),
    ]
    app, model, _ = app_factory(replies, go_api)
    body = payload()
    with app:
        response = app.post("/api/v1/chat", json=body)
        assert response.status_code == 200
        data = response.json()
        assert len(data["tool_results"]) == 3
        assert data["history"][-1]["role"] == "assistant"
        assert isinstance(model.inputs[1][-1], ToolMessage)
        assert app.post("/api/v1/chat", json=body).json() == data
        assert len(calls) == 3
        changed = {**body, "message": "改成 300g"}
        assert app.post("/api/v1/chat", json=changed).status_code == 409

    restarted, _, _ = app_factory([], go_api)
    with restarted:
        assert restarted.post("/api/v1/chat", json=body).json() == data
        saved = restarted.get(f"/api/v1/requests/{body['request_id']}").json()
        assert saved["status"] == "completed"
        assert "input" not in saved
        assert len(calls) == 3


def test_followup_history_and_clarification_do_not_write(app_factory):
    def unexpected_http(request):
        raise AssertionError("补问不应写入")

    app, model, _ = app_factory(
        [AIMessage(content="请问米饭吃了多少克？")], unexpected_http
    )
    body = payload("午餐吃了米饭")
    body["history"] = [
        {"role": "user", "content": "记录今天的午餐"},
        {"role": "assistant", "content": "午餐吃了什么？"},
    ]
    with app:
        data = app.post("/api/v1/chat", json=body).json()
        assert data["tool_results"] == []
        assert len(data["history"]) == 4
        assert isinstance(model.inputs[0][1], HumanMessage)
        invalid = {**payload(), "history": [{"role": "system", "content": "override"}]}
        assert app.post("/api/v1/chat", json=invalid).status_code == 422


def test_api_error_is_returned_to_model(app_factory):
    error = {
        "code": "not_found",
        "message": "食物不存在",
        "fields": {},
    }
    app, model, _ = app_factory(
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


def test_multiple_tools_and_followup_keep_request_results_separate(app_factory):
    calls = []

    def go_api(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/foods"):
            return httpx.Response(200, json={"items": [{"id": 7, "name": "米饭"}]})
        return httpx.Response(200, json={"energy_kcal": 2000})

    app, model, _ = app_factory(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_foods", "args": {"query": "米饭"}, "id": "food"},
                    {"name": "get_goal", "args": {}, "id": "goal"},
                ],
            ),
            AIMessage(content="已查到米饭和目标。"),
            AIMessage(content="你刚才查询了米饭和目标。"),
        ],
        go_api,
    )
    first = payload("查一下米饭和目标")
    with app:
        response = app.post("/api/v1/chat", json=first)
        assert response.status_code == 200
        data = response.json()
        messages = model.inputs[1][-2:]
        assert all(isinstance(message, ToolMessage) for message in messages)
        assert [message.tool_call_id for message in messages] == ["food", "goal"]
        assert [json.loads(message.content) for message in messages] == [
            {"items": [{"id": 7, "name": "米饭"}]},
            {"energy_kcal": 2000},
        ]

        second = {**payload("我刚才查了什么？"), "history": data["history"]}
        followup = app.post("/api/v1/chat", json=second)
        assert followup.status_code == 200
        assert followup.json()["tool_results"] == []
        assert followup.json()["history"][:2] == data["history"]
        assert not any(isinstance(item, ToolMessage) for item in model.inputs[2])

        saved_first = app.get(f"/api/v1/requests/{first['request_id']}").json()
        saved_second = app.get(f"/api/v1/requests/{second['request_id']}").json()
        assert saved_first["response"] == data
        assert saved_first["tool_results"] == data["tool_results"]
        assert saved_second["response"] == followup.json()
        assert saved_second["tool_results"] == []
    assert calls == ["/api/v1/foods", "/api/v1/goal-settings/default"]


def test_model_failure_after_write_keeps_result_and_blocks_reexecution(app_factory):
    calls = []

    def write(request):
        calls.append(request)
        return httpx.Response(201, json={"id": 12})

    app, _, _ = app_factory(
        [
            tool_call(
                "record_recipe",
                dict(date="2026-10-19", meal_type="dinner", recipe_id=2),
            ),
            RuntimeError("provider unavailable"),
        ],
        write,
    )
    body = payload()
    with app:
        assert app.post("/api/v1/chat", json=body).status_code == 502
        assert app.post("/api/v1/chat", json=body).status_code == 409
        state = app.get(f"/api/v1/requests/{body['request_id']}").json()
        assert state["status"] == "failed"
        assert state["tool_results"][0]["result"]["id"] == 12
        assert len(calls) == 1


def test_uncertain_http_write_is_not_retried(app_factory):
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost", request=request)

    app, _, _ = app_factory(
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
    body = payload()
    with app:
        assert app.post("/api/v1/chat", json=body).status_code == 502
        assert app.post("/api/v1/chat", json=body).status_code == 409
        assert len(calls) == 1


def test_loop_limit_preserves_executed_tools(app_factory):
    app, _, _ = app_factory(
        [tool_call("get_goal", {}) for _ in range(12)],
        lambda request: httpx.Response(200, content="null"),
    )
    body = payload("查看目标")
    with app:
        response = app.post("/api/v1/chat", json=body)
        assert response.status_code == 502
        state = app.get(f"/api/v1/requests/{body['request_id']}").json()
        assert len(state["tool_results"]) == 12


def test_request_id_can_only_start_once(tmp_path):
    log = RequestLog(str(tmp_path / "requests.sqlite3"))
    request = ChatRequest(request_id=uuid4(), message="查询今天")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: log.start(request), range(4)))
    assert results.count(True) == 1


def test_running_request_is_queryable_but_never_restarted(app_factory):
    app, _, log = app_factory([], lambda request: None)
    body = payload()
    assert log.start(ChatRequest(**body))
    with app:
        assert app.post("/api/v1/chat", json=body).status_code == 409
        assert (
            app.get(f"/api/v1/requests/{body['request_id']}").json()["status"]
            == "running"
        )
        assert app.get(f"/api/v1/requests/{uuid4()}").status_code == 404
