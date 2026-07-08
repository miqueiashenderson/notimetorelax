import os, sys, json

os.environ["DATABASE_URL"] = "sqlite:////tmp/test_horariolivre.db"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base, Workspace, Member, CORES

_test_engine = create_engine(
    "sqlite:////tmp/test_horariolivre.db",
    connect_args={"check_same_thread": False},
)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(_test_engine)
    Base.metadata.create_all(_test_engine)
    yield
    Base.metadata.drop_all(_test_engine)


@pytest.fixture
def engine():
    return _test_engine


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def app():
    from main import app
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def workspace(session):
    ws = Workspace(slug="cc", name="Ciência da Computação")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture
def workspace_with_password(session):
    from database import hash_password
    ws = Workspace(
        slug="secret-lab",
        name="Laboratório Secreto",
        password_hash=hash_password("minha-senha"),
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture
def members(workspace, session):
    data = [
        (workspace.id, "Alice", "CC", [[0, 1], [2, 3]]),
        (workspace.id, "Bob", "EC", [[1, 2]]),
    ]
    objs = []
    for wid, name, course, busy in data:
        m = Member(
            workspace_id=wid, name=name, course=course,
            schedule=json.dumps(busy),
            color=CORES[len(objs) % len(CORES)],
        )
        session.add(m)
        objs.append(m)
    session.commit()
    for m in objs:
        session.refresh(m)
    return objs
