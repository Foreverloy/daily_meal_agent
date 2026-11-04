.PHONY: run test test-integration check

run:
	uv run uvicorn daily_meal_agent.main:app --host 127.0.0.1 --port 8000 --reload

test:
	uv run pytest -q

test-integration:
	@test -n "$(TEST_DATABASE_URL)" || (echo "TEST_DATABASE_URL is required" && exit 1)
	uv run pytest -q tests/test_integration.py

check:
	uv run ruff check .
	uv run ruff format --check .
