from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.exceptions import router as exceptions_router
from app.api.routes.matches import router as matches_router
from app.api.routes.reconcile import router as reconcile_router
from app.api.routes.score import router as score_router
from app.db.session import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="ReconLoop", lifespan=lifespan)
app.include_router(reconcile_router)
app.include_router(matches_router)
app.include_router(exceptions_router)
app.include_router(score_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
