import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from langchain_openai import ChatOpenAI

from daily_meal_agent.config import Settings
from daily_meal_agent.food_images import (
    FoodImageRequest,
    FoodRecognition,
    recognize_food,
)
from daily_meal_agent.schemas import ChatRequest, ChatResponse, ModelSettings
from daily_meal_agent.service import AgentError, ChatService

logger = logging.getLogger(__name__)
MODEL_TIMEOUT_SECONDS = 60


def create_model(settings: ModelSettings, model_http: httpx.Client):
    # 聊天和截图识别共用本次请求的配置，凭据只用于模型客户端。
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key.get_secret_value(),
        base_url=settings.base_url,
        use_responses_api=settings.use_responses_api,
        timeout=MODEL_TIMEOUT_SECONDS,
        max_retries=0,
        http_client=model_http,
    )


def create_app(service: ChatService | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = Settings()
        yield

    app = FastAPI(title="Daily Meal Agent", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # 默认校验错误会回显输入片段；请求含凭据，不能直接返回这些片段。
        return JSONResponse(status_code=422, content={"error": "请求格式不正确"})

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/api/v1/chat", response_model=ChatResponse)
    def chat(request: ChatRequest):
        try:
            if service is not None:
                return service.chat(request)
            settings = request.model_settings
            # 每轮使用 Go 传来的配置，不修改共享模型，也不把凭据放入 messages。
            with httpx.Client() as model_http:
                model = create_model(settings, model_http)
                chat_service = ChatService(model, app.state.settings.business_timezone)
                return chat_service.chat(request)
        except Exception as error:
            # 上游异常可能带凭据或原始请求，只记录异常类型。
            cause = error.__cause__ or error
            logger.error(
                "Request: %s\nAgent request failed: %s (status=%s)",
                request,
                type(cause).__name__,
                getattr(cause, "status_code", None),
            )
            message = "模型调用失败，请稍后再试。"
            if isinstance(error, AgentError):
                message = str(error)
            return JSONResponse(
                status_code=502,
                content={"error": {"message": message, "tool_results": []}},
            )

    @app.post("/api/v1/food-recognitions", response_model=FoodRecognition)
    def food_recognition(request: FoodImageRequest):
        try:
            with httpx.Client() as model_http:
                model = create_model(request.model_settings, model_http)
                return recognize_food(model, request)
        except Exception as error:
            # 不记录截图、请求参数或模型原始异常，避免把图片和凭据写入日志。
            cause = error.__cause__ or error
            logger.error("Food recognition failed: %s", type(cause).__name__)
            return JSONResponse(
                status_code=502,
                content={"error": {"message": "截图识别失败，请重试或手动填写。"}},
            )

    return app


app = create_app()
