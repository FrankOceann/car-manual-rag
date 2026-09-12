from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"
    chroma_path: str = "data/chroma"
    chroma_host: str | None = None
    chroma_port: int = 8000
    database_url: str = "sqlite:///data/app.db"
    redis_url: str = "redis://localhost:6379/0"
    storage_path: str = "data/manuals"
    model_path: str = "data/models/embedding"
    jwt_secret: str = ""
    jwt_ttl_minutes: int = 60
    upload_max_bytes: int = 100 * 1024 * 1024
    deepseek_timeout: float = 60
    ocr_enabled: bool = True
    ocr_language: str = "ch"
    ocr_render_dpi: int = 200
    vision_enabled: bool = False
    vision_base_url: str | None = None
    vision_api_key: str | None = None
    vision_model: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
