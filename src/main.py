from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.config import get_settings
from src.db.session import check_database, dispose_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_chunk_settings()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    yield
    await dispose_database()
    print("Shutting down...")


app = FastAPI(
    title="MediPay Agent API",
    description=(
        "Backend API for BHYT questions, hospital fee analysis, and payment guidance. "
        "Built with FastAPI and LangGraph."
    ),
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "Agent",
            "description": "Chat, analysis, and status endpoints for MediPay Agent.",
        },
        {
            "name": "System",
            "description": "Application health and readiness endpoints.",
        },
    ],
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get(
    "/health",
    tags=["System"],
    summary="Check API liveness",
    description="Return immediately when the API process is running.",
)
async def health():
    return {"status": "ok", "env": settings.app_env}


@app.get(
    "/ready",
    tags=["System"],
    summary="Check API readiness",
    description="Check whether the API can reach its configured database.",
)
async def readiness():
    database_ready = await check_database()
    return {"status": "ready" if database_ready else "degraded", "database": database_ready}
