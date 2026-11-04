import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from daily_meal_agent.schemas import ChatRequest, ChatResponse, ToolResult


class RequestLog:
    """保存每条消息的执行状态，防止 HTTP 重试再次执行已完成的写操作。"""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS chat_requests (
                    id TEXT PRIMARY KEY,
                    input TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tool_results TEXT NOT NULL DEFAULT '[]',
                    response TEXT,
                    error TEXT
                )
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            # sqlite3 的 with 负责提交/回滚；连接仍需显式关闭。
            with db:
                yield db
        finally:
            db.close()

    def start(self, request: ChatRequest) -> bool:
        """返回 True 表示本次可以执行；重复 ID 交给调用方查询已有结果。"""
        # TODO：进程中断后 running 状态不会自动恢复，需先核对已执行结果。
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO chat_requests (id, input, status) VALUES (?, ?, ?)",
                (str(request.request_id), request.model_dump_json(), "running"),
            )
            return cursor.rowcount == 1

    def get(self, request_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM chat_requests WHERE id = ?", (request_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("input", "tool_results", "response", "error"):
            if result[key] is not None:
                result[key] = json.loads(result[key])
        return result

    def record_tools(self, request_id: str, results: list[ToolResult]) -> None:
        saved_results = [item.model_dump(mode="json") for item in results]
        with self.connect() as db:
            db.execute(
                "UPDATE chat_requests SET tool_results = ? WHERE id = ?",
                (json.dumps(saved_results, ensure_ascii=False), request_id),
            )

    def finish(self, request_id: str, response: ChatResponse) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE chat_requests SET status = 'completed', response = ? WHERE id = ?",
                (response.model_dump_json(), request_id),
            )

    def fail(self, request_id: str, error: dict) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE chat_requests SET status = 'failed', error = ? WHERE id = ?",
                (json.dumps(error, ensure_ascii=False), request_id),
            )
