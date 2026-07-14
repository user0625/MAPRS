from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from backend.api.routes.analysis import router as analysis_router
from backend.api.routes.health import router as health_router
from backend.api.routes.tasks import router as tasks_router
from backend.api.routes.conversations import router as conversations_router
from backend.api.routes.comparisons import router as comparisons_router
from backend.api import task_store as task_store_module
from backend.api.task_store import DatabaseTaskStore
from backend.core.config import get_settings
import re
import uuid
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


class RequestIDMiddleware:
  def __init__(self, app: ASGIApp) -> None:
    self.app = app

  async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] != "http":
      await self.app(scope, receive, send)
      return
    headers = dict(scope.get("headers", []))
    supplied = headers.get(b"x-request-id", b"").decode("ascii", "ignore")
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", supplied) else uuid.uuid4().hex
    scope.setdefault("state", {})["request_id"] = request_id
    async def send_with_id(message: Message) -> None:
      if message["type"] == "http.response.start":
        message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
      await send(message)
    await self.app(scope, receive, send_with_id)


def create_app() -> FastAPI:
  settings = get_settings()
  if task_store_module.task_store.database_url != settings.database_url:
    task_store_module.task_store = DatabaseTaskStore(settings.database_url)
    # routes imports the object name, so update that binding as well.
    from backend.api.routes import tasks as tasks_module
    tasks_module.task_store = task_store_module.task_store
  store = task_store_module.task_store

  @asynccontextmanager
  async def lifespan(_: FastAPI):
    store.create_tables()
    # Only stale heartbeats are considered lost; live Celery work survives API restarts.
    store.recover_interrupted_tasks(settings.task_stale_after_seconds)
    store.cleanup_expired_files(settings.file_retention_days)
    yield

  app = FastAPI(
    title="Multi-Agent Paper Reader API",
    description="Backend API for the Multi_Agent Paper Reader System.",
    version="0.1.0",
    lifespan=lifespan,
  )

  # Development CORS settings.
  # Tighten this before productino deployment.
  app.add_middleware(
    CORSMiddleware,
    allow_origins=[
      "http://localhost:3000",
      "http://localhost:5173",
      "http://127.0.0.1:3000",
      "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods = ["*"],
    allow_headers = ["*"],
  )

  app.add_middleware(RequestIDMiddleware)

  @app.exception_handler(StarletteHTTPException)
  async def http_error(request: Request, exc: StarletteHTTPException):
    codes = {400: "validation", 404: "not_found", 409: "conflict", 413: "validation", 422: "validation"}
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail),
      "code": codes.get(exc.status_code, "workflow"), "request_id": request.state.request_id})

  @app.exception_handler(RequestValidationError)
  async def validation_error(request: Request, _: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "Request validation failed.",
      "code": "validation", "request_id": request.state.request_id})

  app.include_router(health_router)
  app.include_router(analysis_router)
  app.include_router(tasks_router)
  app.include_router(conversations_router)
  app.include_router(comparisons_router)

  return app

app = create_app()
