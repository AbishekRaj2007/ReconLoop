from fastapi import FastAPI

app = FastAPI(title="ReconLoop")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
