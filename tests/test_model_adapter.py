import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI

from daily_meal_agent.client import MealClient
from daily_meal_agent.main import create_app
from daily_meal_agent.request_log import RequestLog
from daily_meal_agent.service import ChatService


def test_real_langchain_responses_adapter_with_mock_provider(tmp_path):
    """真实 LangChain/OpenAI SDK 序列化；HTTP 服务用模拟响应替代，无模型费用。"""
    model_requests = []

    def provider(request):
        body = json.loads(request.content)
        model_requests.append(body)
        assert request.url.path == "/v1/responses"
        assert body["parallel_tool_calls"] is False
        assert any(t["name"] == "get_goal" for t in body["tools"])
        if len(model_requests) == 1:
            output = [
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "get_goal",
                    "arguments": "{}",
                    "status": "completed",
                }
            ]
        else:
            assert any(item["type"] == "function_call_output" for item in body["input"])
            output = [
                {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "尚未设置默认目标。",
                            "annotations": [],
                        }
                    ],
                }
            ]
        return httpx.Response(
            200,
            json={
                "id": "resp_" + str(len(model_requests)),
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "test-model",
                "output": output,
                "parallel_tool_calls": False,
                "error": None,
                "incomplete_details": None,
            },
        )

    with (
        httpx.Client(transport=httpx.MockTransport(provider)) as model_http,
        httpx.Client(
            base_url="http://go",
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content="null")
            ),
        ) as go_http,
    ):
        model = ChatOpenAI(
            model="test-model",
            api_key="test-key",
            base_url="http://model/v1",
            use_responses_api=True,
            http_client=model_http,
            max_retries=0,
        )
        service = ChatService(
            model,
            MealClient(go_http),
            RequestLog(str(tmp_path / "requests.sqlite3")),
            "Asia/Shanghai",
        )
        with TestClient(create_app(service)) as app:
            response = app.post(
                "/api/v1/chat", json={"request_id": str(uuid4()), "message": "查看目标"}
            )
        assert response.status_code == 200, response.text
        assert response.json()["reply"] == "尚未设置默认目标。"
        assert len(model_requests) == 2
