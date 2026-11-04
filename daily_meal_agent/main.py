import logging
from contextlib import asynccontextmanager
from uuid import UUID

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langchain_openai import ChatOpenAI

from daily_meal_agent.client import MealClient
from daily_meal_agent.config import Settings
from daily_meal_agent.request_log import RequestLog
from daily_meal_agent.schemas import ChatRequest, ChatResponse
from daily_meal_agent.service import AgentError, ChatService

logger = logging.getLogger(__name__)
MODEL_TIMEOUT_SECONDS = 60
API_TIMEOUT_SECONDS = 15


def create_app(service: ChatService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is not None:
            app.state.service = service
            yield
            return

        settings = Settings()
        model = ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            use_responses_api=settings.openai_use_responses_api,
            timeout=MODEL_TIMEOUT_SECONDS,
            max_retries=0,
        )
        with httpx.Client(
            base_url=settings.meal_api_url, timeout=API_TIMEOUT_SECONDS
        ) as http:
            app.state.service = ChatService(
                model,
                MealClient(http),
                RequestLog(settings.request_db),
                settings.business_timezone,
            )
            yield

    app = FastAPI(title="Daily Meal Agent", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/requests/{request_id}")
    def request_status(request_id: UUID):
        saved = app.state.service.log.get(str(request_id))
        if saved is None:
            return JSONResponse(status_code=404, content={"error": "请求不存在"})
        saved.pop("input")
        return saved

    @app.post("/api/v1/chat", response_model=ChatResponse)
    def chat(request: ChatRequest):
        service = app.state.service
        request_id = str(request.request_id)
        if not service.log.start(request):
            saved = service.log.get(request_id)
            if saved["input"] != request.model_dump(mode="json"):
                message = "同一 request_id 不能用于不同消息或历史。"
            elif saved["status"] == "completed":
                return saved["response"]
            else:
                message = "该请求已开始执行，请查询请求状态和实际业务数据，不要换 ID 盲目重试。"
            return JSONResponse(status_code=409, content={"error": message})

        try:
            response = service.chat(request)
            service.log.finish(request_id, response)
            return response
        except Exception as error:
            # 统一在 HTTP 边界记录失败。调用中断时可能已有部分写入，不能重跑整轮。
            logger.exception("Agent request failed: %s", request_id)
            if isinstance(error, AgentError):
                message = str(error)
            else:
                message = "模型或业务接口调用失败。可能已有部分操作成功，请查询请求状态及实际记录。"
            detail = {"request_id": request_id, "message": message}
            service.log.fail(request_id, detail)
            return JSONResponse(status_code=502, content={"error": detail})

    return app


app = create_app()
