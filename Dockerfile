FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY daily_meal_agent ./daily_meal_agent
EXPOSE 8000
CMD [".venv/bin/uvicorn", "daily_meal_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
