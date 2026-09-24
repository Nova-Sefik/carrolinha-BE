from fastapi import FastAPI

app = FastAPI(title="Carrolinha API")


@app.get("/")
def read_root():
    return {"message": "Hello from Carrolinha API"}


@app.get("/api/health")
def read_health():
    return {"status": "ok"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str | None = None):
    return {"item_id": item_id, "q": q}
