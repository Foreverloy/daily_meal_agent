from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    meal_api_url: str = "http://127.0.0.1:8080"
    business_timezone: str = "Asia/Shanghai"
    openai_api_key: str
    openai_model: str
    openai_base_url: str = "https://api.openai.com/v1"
    openai_use_responses_api: bool = True
    request_db: str = "data/requests.sqlite3"
