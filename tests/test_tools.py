import json

import httpx
import pytest
from pydantic import ValidationError

from daily_meal_agent.client import MealClient
from daily_meal_agent.tools import build_tools


def test_patch_preserves_omission_unknown_and_false():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"id": 1})

    with httpx.Client(
        base_url="http://go", transport=httpx.MockTransport(handle)
    ) as http:
        tools = {t.name: t for t in build_tools(MealClient(http))}
        tools["update_food"].invoke(
            {
                "food_id": 1,
                "changes": {
                    "nutrition_per_100": {"sodium_mg": None},
                    "favorite": False,
                },
            }
        )
        assert json.loads(requests[0].content) == {
            "nutrition_per_100": {"sodium_mg": None},
            "favorite": False,
        }
        tools["update_entry"].invoke({"entry_id": 3, "changes": {"quantity": 80}})
        assert json.loads(requests[1].content) == {"quantity": 80}


def test_record_tools_send_explicit_payloads_without_idempotency_headers():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(201, json={"id": 1})

    with httpx.Client(
        base_url="http://go", transport=httpx.MockTransport(handle)
    ) as http:
        tools = {t.name: t for t in build_tools(MealClient(http))}
        tools["record_food"].invoke(
            dict(
                date="2026-10-19", meal_type="lunch", food_id=1, quantity=200, unit="g"
            )
        )
        tools["record_recipe"].invoke(
            dict(date="2026-10-19", meal_type="dinner", recipe_id=2)
        )
    assert [json.loads(request.content) for request in requests] == [
        {
            "date": "2026-10-19",
            "meal_type": "lunch",
            "item_type": "food",
            "food_id": 1,
            "quantity": 200,
            "unit": "g",
        },
        {
            "date": "2026-10-19",
            "meal_type": "dinner",
            "item_type": "recipe",
            "recipe_id": 2,
        },
    ]
    for request in requests:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/meal-entries"
        assert "Idempotency-Key" not in request.headers


def test_goal_dates_are_dates_not_arbitrary_paths():
    tools = {t.name: t for t in build_tools(None)}
    with pytest.raises(ValidationError):
        tools["reset_daily_goal"].invoke({"date": "../meal-entries/1"})
