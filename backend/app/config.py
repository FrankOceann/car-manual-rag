from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    chroma_path: str = "data/chroma"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
