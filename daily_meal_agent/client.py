import httpx


class MealAPIError(Exception):
    def __init__(self, status_code: int, detail: dict):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail["message"])


class MealClient:
    """发送 HTTP 请求，并将 API 错误交给调用方。"""

    def __init__(self, http: httpx.Client):
        self.http = http

    def request(self, method: str, path: str, *, params=None, body=None):
        response = self.http.request(
            method, f"/api/v1/{path}", params=params, json=body
        )
        if response.is_error:
            # 4xx 的错误信息交回模型；5xx 抛到 HTTP 入口，停止本轮操作。
            if response.status_code < 500:
                raise MealAPIError(response.status_code, response.json()["error"])
            response.raise_for_status()
        if response.status_code == 204:
            return {"deleted": True}
        return response.json()
