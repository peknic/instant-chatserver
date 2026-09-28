import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from . import models
from .database import Base, engine
from .routers import auth, chats, users
from .ws import router as ws_router

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _as_int_list(value) -> list[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, int)]


def _migrate_chat_adminmembers() -> None:
    inspector = inspect(engine)
    if "chats" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("chats")}
    if "adminmembers" not in columns:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE chats ADD COLUMN adminmembers JSON NOT NULL DEFAULT '[]'")
            )

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, members, adminmembers FROM chats")
        ).mappings().all()
        for row in rows:
            if _as_int_list(row["adminmembers"]):
                continue
            members = _as_int_list(row["members"])
            admins = [members[0]] if members else []
            conn.execute(
                text("UPDATE chats SET adminmembers = :admins WHERE id = :id"),
                {"admins": json.dumps(admins), "id": row["id"]},
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _migrate_chat_adminmembers()
    yield


app = FastAPI(title="Chat Server", version="0.3.0", lifespan=lifespan)


@app.middleware("http")
async def disable_asset_caching(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/":
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.include_router(users.router)
app.include_router(auth.router)
app.include_router(chats.router)
app.include_router(ws_router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/info")
def info():
    return {"service": "chatser", "status": "ok"}


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "healthy", "database": "connected"}


@app.get("/tables")
def tables():
    return {"tables": inspect(engine).get_table_names()}
