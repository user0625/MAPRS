from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from backend.api.routes.analysis import router as analysis_router
from backend.api.routes.health import router as health_router
from backend.api.routes.tasks import router as tasks_router
from backend.api import task_store as task_store_module
from backend.api.task_store import DatabaseTaskStore
from backend.core.config import get_settings


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
    store.recover_interrupted_tasks()
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

  app.include_router(health_router)
  app.include_router(analysis_router)
  app.include_router(tasks_router)

  return app

app = create_app()
