from fastapi import FastAPI

from app.api.routes.exceptions import router as exceptions_router
from app.api.routes.matches import router as matches_router
from app.api.routes.reconcile import router as reconcile_router
from app.db.session import Base, engine

app = FastAPI(title="ReconLoop")
app.include_router(reconcile_router)
app.include_router(matches_router)
app.include_router(exceptions_router)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(engine)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
