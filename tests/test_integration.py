"""使用真实 Go API 和独立 PostgreSQL schema；模型仍用固定脚本替代。"""

import os
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from daily_meal_agent.client import MealAPIError, MealClient
from daily_meal_agent.main import create_app
from daily_meal_agent.request_log import RequestLog
from daily_meal_agent.service import ChatService
from daily_meal_agent.tools import build_tools
from tests.conftest import ScriptedModel, tool_call


@pytest.fixture(scope="module")
def go_api(tmp_path_factory):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("需要 TEST_DATABASE_URL，且数据库名称必须以 _test 结尾")
    url = urlsplit(dsn)
    if url.scheme not in ("postgres", "postgresql") or not url.path.endswith("_test"):
        pytest.fail("集成测试只能连接名称以 _test 结尾的 PostgreSQL 数据库")
    backend = (
        Path(
            os.getenv(
                "DAILY_MEAL_BACKEND_DIR",
                str(Path(__file__).resolve().parents[2] / "daily_meal"),
            )
        )
        / "backend"
    )
    work = tmp_path_factory.mktemp("go-api")
    binary = work / "api"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/api"], cwd=backend, check=True
    )

    schema = "agent_test_" + uuid4().hex
    subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-c", f"CREATE SCHEMA {schema}"],
        check=True,
    )
    process = None
    try:
        query = dict(parse_qsl(url.query))
        query["search_path"] = schema
        isolated_dsn = urlunsplit(url._replace(query=urlencode(query)))
        env = {
            **os.environ,
            "DATABASE_URL": isolated_dsn,
            "BUSINESS_TIMEZONE": "Asia/Shanghai",
        }
        subprocess.run(
            ["go", "run", "./cmd/migrate", "up"], cwd=backend, env=env, check=True
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env["HTTP_ADDR"] = f"127.0.0.1:{port}"
        with (work / "api.log").open("w") as output:
            process = subprocess.Popen(
                [str(binary)], env=env, stdout=output, stderr=output
            )
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as http:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((work / "api.log").read_text())
                try:
                    if http.get("/healthz").status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("测试 Go API 未就绪")
            yield MealClient(http)
    finally:
        if process:
            process.terminate()
            process.wait(timeout=10)
        subprocess.run(
            [
                "psql",
                dsn,
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                f"DROP SCHEMA {schema} CASCADE",
            ],
            check=True,
        )


def test_tools_against_go_contract(go_api):
    tools = {t.name: t for t in build_tools(go_api)}

    def invoke(name, **args):
        return tools[name].invoke(args)

    assert invoke("get_goal") is None
    rice = invoke(
        "create_food",
        food={
            "name": "集成测试米饭",
            "basis_unit": "g",
            "nutrition_per_100": {"energy_kcal": 116, "protein_g": 2.6},
        },
    )
    food_id = rice["id"]
    assert invoke("get_food", food_id=food_id)["nutrition_per_100"]["sodium_mg"] is None
    invoke("update_food", food_id=food_id, changes={"favorite": True})
    assert invoke("search_foods", query="集成测试", favorite=True)["total"] == 1
    goal = dict(
        energy_kcal=2000, protein_percent=30, carbohydrate_percent=45, fat_percent=25
    )
    assert invoke("set_goal", goal=goal)["targets"]["protein_g"] == 150
    invoke("set_goal", date="2026-10-19", goal={**goal, "energy_kcal": 1800})
    assert invoke("get_goal", date="2026-10-19")["source"] == "override"
    invoke("reset_daily_goal", date="2026-10-19")
    assert invoke("get_goal", date="2026-10-19")["source"] != "override"

    recipe = invoke(
        "create_recipe",
        recipe={
            "name": "测试饭",
            "ingredients": [{"food_id": food_id, "quantity": 0.2, "unit": "kg"}],
        },
    )
    recipe_id = recipe["id"]
    assert invoke("search_recipes", query="测试饭")["total"] == 1
    assert (
        invoke("get_recipe", recipe_id=recipe_id)["ingredients"][0]["quantity"] == 200
    )
    entry_args = dict(
        date="2026-10-19", meal_type="lunch", food_id=food_id, quantity=0.2, unit="kg"
    )
    entry = invoke("record_food", **entry_args)
    # 当前后端每次 POST 都新建记录；Agent 的请求重放由下一个测试验证。
    duplicate = invoke("record_food", **entry_args)
    assert duplicate["id"] != entry["id"]
    entries = invoke("list_entries", date="2026-10-19", meal_type="lunch")["items"]
    assert {item["id"] for item in entries} == {entry["id"], duplicate["id"]}
    invoke("delete_entry", entry_id=duplicate["id"])
    with pytest.raises(MealAPIError) as error:
        invoke("get_food", food_id=food_id + 1000000)
    assert error.value.status_code == 404

    recipe_entry = invoke(
        "record_recipe", date="2026-10-19", meal_type="dinner", recipe_id=recipe_id
    )
    invoke(
        "replace_recipe",
        recipe_id=recipe_id,
        recipe={
            "name": "改过的测试饭",
            "ingredients": [{"food_id": food_id, "quantity": 300, "unit": "g"}],
        },
    )
    saved = invoke("list_entries", date="2026-10-19", meal_type="dinner")["items"][0]
    assert saved["id"] == recipe_entry["id"]
    assert saved["snapshot"]["name"] == "测试饭"
    assert saved["snapshot"]["ingredients"][0]["quantity"] == 200
    invoke("update_entry", entry_id=entry["id"], changes={"quantity": 100})
    summary = invoke("daily_summary", date="2026-10-19")
    assert summary["nutrition_totals"]["energy_kcal"]["known_total"] == 348
    assert summary["nutrition_totals"]["sodium_mg"] == {
        "known_total": None,
        "complete": False,
    }
    invoke("delete_entry", entry_id=entry["id"])
    assert len(invoke("list_entries", date="2026-10-19")["items"]) == 1


def test_chat_with_real_go_and_persisted_replay(go_api, tmp_path):
    food = go_api.request(
        "POST",
        "foods",
        body={
            "name": "对话测试牛奶",
            "basis_unit": "ml",
            "nutrition_per_100": {"energy_kcal": 60},
        },
    )
    model = ScriptedModel(
        [
            tool_call("search_foods", {"query": "对话测试牛奶"}),
            tool_call(
                "record_food",
                dict(
                    date="2026-10-20",
                    meal_type="breakfast",
                    food_id=food["id"],
                    quantity=250,
                    unit="ml",
                ),
                "call_2",
            ),
            AIMessage(content="已记录早餐牛奶 250ml。"),
        ]
    )
    service = ChatService(
        model, go_api, RequestLog(str(tmp_path / "requests.sqlite3")), "Asia/Shanghai"
    )
    body = {
        "request_id": str(uuid4()),
        "message": "10 月 20 日早餐喝了 250ml 对话测试牛奶",
    }
    with TestClient(create_app(service)) as app:
        response = app.post("/api/v1/chat", json=body)
        assert response.status_code == 200, response.text
        assert app.post("/api/v1/chat", json=body).json() == response.json()
    entries = go_api.request("GET", "meal-entries", params={"date": "2026-10-20"})
    assert len(entries["items"]) == 1
    assert entries["items"][0]["nutrition_totals"]["energy_kcal"]["known_total"] == 150
