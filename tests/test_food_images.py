import json

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from daily_meal_agent.main import create_app
from tests.conftest import ScriptedModel, payload

IMAGE = "data:image/png;base64,dGVzdA=="
READING = {
    "name": "纯牛奶",
    "basis_quantity": 250,
    "basis_unit": "ml",
    "nutrients": [
        {"key": "energy", "value": 627.6, "unit": "kJ"},
        {"key": "protein", "value": 8, "unit": "g"},
        {"key": "fat", "value": 0, "unit": "g"},
    ],
}


def test_image_uses_real_langchain_adapter_and_returns_raw_readings(monkeypatch):
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert body["messages"][1]["content"][1]["image_url"]["url"] == IMAGE
        assert "test-key" not in json.dumps(body)
        assert body["response_format"] == {"type": "json_object"}
        assert "tools" not in body
        return httpx.Response(
            200,
            json={
                "id": "recognition-1",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(READING),
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(provider)) as model_http:
        model = ChatOpenAI(
            model="test-model",
            api_key="test-key",
            base_url="http://model/v1",
            use_responses_api=False,
            http_client=model_http,
            max_retries=0,
        )
        monkeypatch.setattr("daily_meal_agent.main.create_model", lambda *args: model)
        with TestClient(create_app()) as app:
            response = app.post(
                "/api/v1/food-recognitions",
                json={
                    "image_data_url": IMAGE,
                    "model_settings": payload()["model_settings"],
                },
            )
    assert response.status_code == 200, response.text
    assert response.json() == READING
    assert len(requests) == 1


def test_image_failure_does_not_log_image_or_credentials(monkeypatch, caplog):
    def fail(*args):
        raise RuntimeError(f"provider test-key {IMAGE}")

    monkeypatch.setattr("daily_meal_agent.main.create_model", fail)
    with TestClient(create_app()) as app:
        response = app.post(
            "/api/v1/food-recognitions",
            json={
                "image_data_url": IMAGE,
                "model_settings": payload()["model_settings"],
            },
        )
    assert response.status_code == 502
    assert "test-key" not in response.text + caplog.text
    assert IMAGE not in response.text + caplog.text


@pytest.mark.parametrize(
    "content", ["", '{"name":"milk"', json.dumps(READING) + "</think>"]
)
def test_image_rejects_incomplete_or_extra_output(monkeypatch, content):
    class ImageModel(ScriptedModel):
        def bind(self, **kwargs):
            return self

    model = ImageModel([AIMessage(content=content)])
    monkeypatch.setattr("daily_meal_agent.main.create_model", lambda *args: model)
    with TestClient(create_app()) as app:
        response = app.post(
            "/api/v1/food-recognitions",
            json={
                "image_data_url": IMAGE,
                "model_settings": payload()["model_settings"],
            },
        )
    assert response.status_code == 502
