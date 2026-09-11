from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import Settings
from app.db import get_engine

router = APIRouter()


@router.get("/health/live")
def live():
    return {"status": "alive"}


@router.get("/health/ready")
def ready():
    settings = Settings()
    checks = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT id FROM users LIMIT 1"))
        checks["database"] = "ready"
    except Exception:
        checks["database"] = "unavailable_or_migration_required"
    try:
        import redis
        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
        checks["queue"] = "ready"
    except Exception:
        checks["queue"] = "unavailable"
    try:
        from app.rag.store import ManualStore
        ManualStore().collection.count()
        checks["chroma"] = "ready"
    except Exception:
        checks["chroma"] = "unavailable"
    if not (Path(settings.model_path) / "modules.json").is_file():
        checks["model"] = "run_prepare_model"
    else:
        try:
            from app.rag.store import ManualStore
            ManualStore._create_embedding_model()
            checks["model"] = "ready"
        except Exception:
            checks["model"] = "load_failed"
    checks["auth"] = "ready" if len(settings.jwt_secret) >= 32 else "configure_jwt_secret"
    checks["deepseek"] = "ready" if settings.deepseek_api_key else "configure_api_key"
    ok = all(value == "ready" for value in checks.values())
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks},
                        status_code=200 if ok else 503)
