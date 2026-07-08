import pytest
from sqlalchemy import text
from database import (
    init_db, create_workspace, get_workspace, get_members,
    add_member, remove_member, hash_password, check_password,
    slugify, Workspace, Member,
)


def test_init_db(engine):
    init_db()
    with engine.connect() as conn:
        tables = [row[0] for row in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )]
    assert "workspaces" in tables
    assert "members" in tables


def test_create_workspace(session):
    ws = create_workspace("Ciência da Computação")
    assert ws.slug == "ciencia-da-computacao"
    assert ws.name == "Ciência da Computação"
    assert ws.password_hash is None
    assert ws.id is not None


def test_create_workspace_duplicate_slug(session):
    create_workspace("CC")
    ws2 = create_workspace("CC")
    assert ws2.slug == "cc-1"


def test_create_workspace_with_password(session):
    ws = create_workspace("Lab", "minha-senha")
    assert ws.password_hash is not None
    assert ":" in ws.password_hash


def test_get_workspace(session, workspace):
    ws = get_workspace("cc")
    assert ws is not None
    assert ws.id == workspace.id
    assert ws.name == "Ciência da Computação"


def test_get_nonexistent_workspace(session):
    assert get_workspace("nope") is None


def test_add_member(session, workspace):
    busy = [[0, 1], [2, 3]]
    member, erro = add_member(workspace.id, "Alice", "CC", busy)
    assert erro is None
    assert member.name == "Alice"
    assert member.course == "CC"
    assert member.get_busy() == busy
    assert member.workspace_id == workspace.id


def test_add_duplicate_member_fails(session, workspace):
    add_member(workspace.id, "Alice", "CC", [])
    member, erro = add_member(workspace.id, "Alice", "EC", [])
    assert member is None
    assert "Já existe" in erro


def test_add_duplicate_member_force(session, workspace):
    add_member(workspace.id, "Alice", "CC", [[0, 0]])
    member, erro = add_member(workspace.id, "Alice", "EC", [[1, 1]], force=True)
    assert erro is None
    assert member.name == "Alice"
    assert member.course == "EC"
    assert member.get_busy() == [[1, 1]]


def test_get_members(session, workspace, members):
    result = get_members(workspace.id)
    assert len(result) == 2
    names = {m.name for m in result}
    assert names == {"Alice", "Bob"}


def test_get_members_empty_workspace(session):
    ws = Workspace(slug="vazio", name="Vazio")
    session.add(ws)
    session.commit()
    assert get_members(ws.id) == []


def test_remove_member(session, members):
    ok = remove_member(members[0].id)
    assert ok is True
    remaining = get_members(members[0].workspace_id)
    assert len(remaining) == 1
    assert remaining[0].id == members[1].id


def test_remove_nonexistent_member(session):
    assert remove_member(9999) is False


def test_hash_password():
    h = hash_password("senha123")
    assert ":" in h
    assert check_password("senha123", h) is True


def test_check_password_wrong():
    h = hash_password("senha123")
    assert check_password("outra", h) is False


def test_check_password_invalid_hash():
    assert check_password("x", "invalido") is False


def test_slugify():
    assert slugify("Ciência da Computação") == "ciencia-da-computacao"
    assert slugify("  Hello   World  ") == "hello-world"
    assert slugify("A-B-C") == "a-b-c"
    assert slugify("special!!!chars???") == "specialchars"
