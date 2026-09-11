from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextvars import ContextVar
from time import perf_counter
from uuid import uuid4
from contextlib import asynccontextmanager
import json
import logging

from app.api.routes import router
from app.auth import router as auth_router
from app.management import router as management_router
from app.health import router as health_router

request_id_context = ContextVar("request_id", default=None)


class JsonLogFormatter(logging.Formatter):
    def format(self, record):
        payload = {"event": record.getMessage(), "level": record.levelname,
                   "request_id": request_id_context.get()}
        for field in ("job_id", "duration_ms", "error_type", "status"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JsonLogFormatter())
logging.getLogger("rag").handlers = [handler]
logging.getLogger("rag").setLevel(logging.INFO)
logging.getLogger("rag").propagate = False


@asynccontextmanager
async def lifespan(app):
    yield
    from app.rag.store import close_shared_clients
    close_shared_clients()


app = FastAPI(title="Car Manual RAG API", lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    token = request_id_context.set(request_id)
    started = perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            from openai import APITimeoutError, APIConnectionError, APIStatusError
            status = 504 if isinstance(exc, APITimeoutError) else 503
            logging.getLogger("rag").error("request_failed", extra={"error_type": type(exc).__name__})
            response = JSONResponse({"detail": "服务暂时不可用，请稍后重试。",
                                     "request_id": request_id}, status_code=status)
        response.headers["X-Request-ID"] = request_id
        logging.getLogger("rag").info("request_completed", extra={
            "duration_ms": round((perf_counter()-started)*1000), "status": response.status_code})
        return response
    finally:
        request_id_context.reset(token)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc):
    return JSONResponse({"detail": exc.detail, "request_id": request.state.request_id},
                        status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc):
    return JSONResponse({"detail": "请求参数无效，请检查输入。",
                         "request_id": request.state.request_id}, status_code=422)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.include_router(router)
app.include_router(auth_router)
app.include_router(management_router)
app.include_router(health_router)
