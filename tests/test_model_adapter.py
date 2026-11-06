import json

import httpx
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI

from daily_meal_agent.main import create_app
from daily_meal_agent.service import ChatService
from tests.conftest import payload


def test_real_langchain_responses_adapter_with_mock_provider():
    """真实 LangChain/OpenAI SDK 序列化；HTTP 服务用模拟响应替代，无模型费用。"""
    model_requests = []

    def provider(request):
        body = json.loads(request.content)
        model_requests.append(body)
        assert request.url.path == "/v1/responses"
        assert "parallel_tool_calls" not in body
        assert "tools" not in body
        assert "tool_choice" not in body
        assert [item["role"] for item in body["input"]] == ["system", "user"]
        output = [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "你好，我是你的饮食助手。",
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

    with httpx.Client(transport=httpx.MockTransport(provider)) as model_http:
        model = ChatOpenAI(
            model="test-model",
            api_key="test-key",
            base_url="http://model/v1",
            use_responses_api=True,
            http_client=model_http,
            max_retries=0,
        )
        service = ChatService(model, "Asia/Shanghai")
        with TestClient(create_app(service)) as app:
            response = app.post("/api/v1/chat", json=payload())
        assert response.status_code == 200, response.text
        assert response.json() == {
            "reply": "你好，我是你的饮食助手。",
            "tool_results": [],
        }
        assert len(model_requests) == 1
