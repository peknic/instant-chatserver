from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Chat, Message, User


def test_app_starts_and_health():
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["database"] == "connected"


def test_tables_created():
    with TestClient(app):
        names = set(inspect(engine).get_table_names())
    assert {"users", "chats", "messages"}.issubset(names)
    assert "chat_members" not in names


def test_sqlite_connection():
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1


def test_models_crud():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = User(username="alice", email="alice@gmail.com")
        db.add(user)
        db.commit()
        db.refresh(user)

        chat = Chat(name="test", members=[user.id, 999])
        db.add(chat)
        db.commit()
        db.refresh(chat)

        message = Message(chat_id=chat.id, sender_id=user.id, content="hi")
        db.add(message)
        db.commit()
        db.refresh(message)

        assert chat.members == [user.id, 999]
        assert message.message_type == "text"
        assert user.created_at is not None
    finally:
        db.close()
