import pytest
from sqlalchemy import text
from database import (
    init_db, create_workspace, get_workspace, get_members, get_member,
    add_member, remove_member, hash_password, check_password,
    slugify, get_or_create_user, get_user, update_extra_busy, Workspace, Member,
)


def test_init_db(engine):
    init_db()
    with engine.connect() as conn:
        tables = [row[0] for row in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )]
    assert "workspaces" in tables
    assert "members" in tables
    assert "users" in tables


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
    ok = remove_member(members[0].id, members[0].workspace_id)
    assert ok is True
    remaining = get_members(members[0].workspace_id)
    assert len(remaining) == 1
    assert remaining[0].id == members[1].id


def test_remove_nonexistent_member(session, workspace):
    assert remove_member(9999, workspace.id) is False


def test_remove_member_nao_remove_de_outro_workspace(session, workspace):
    outro = Workspace(slug="outro", name="Outro")
    session.add(outro)
    session.commit()
    session.refresh(outro)
    _, erro = add_member(outro.id, "Roubo", "CC", [])
    assert erro is None
    outros = get_members(outro.id)
    assert len(outros) == 1
    ok = remove_member(outros[0].id, workspace.id)
    assert ok is False
    assert get_member(outros[0].id) is not None


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


class TestUser:
    def test_get_or_create_user(self, session):
        u = get_or_create_user("sub1", "ana@x.com", "Ana", "")
        assert u.id is not None
        assert u.google_sub == "sub1"
        assert u.google_email == "ana@x.com"

    def test_get_or_create_user_updates_info(self, session):
        u = get_or_create_user("sub1", "ana@x.com", "Ana", "")
        u2 = get_or_create_user("sub1", "ana@x.com", "Ana Atualizada", "pic-url")
        assert u2.id == u.id
        assert u2.google_name == "Ana Atualizada"
        assert u2.google_picture == "pic-url"

    def test_get_or_create_user_two_users(self, session):
        u1 = get_or_create_user("sub1", "a@x.com")
        u2 = get_or_create_user("sub2", "b@x.com")
        assert u1.id != u2.id

    def test_get_user(self, session):
        u = get_or_create_user("sub1", "a@x.com")
        assert get_user(u.id).id == u.id
        assert get_user(9999) is None


class TestExtraBusy:
    def test_add_member_with_user_id(self, session, workspace):
        u = get_or_create_user("sub1", "ana@x.com", "Ana")
        m, erro = add_member(workspace.id, "Ana", "CC", [], user_id=u.id)
        assert erro is None
        assert m.user_id == u.id

    def test_update_extra_busy(self, session, workspace):
        m, _ = add_member(workspace.id, "Ana", "CC", [[0, 0]])
        assert m.get_extra_busy() == []
        updated = update_extra_busy(m.id, [[2, 3], [4, 5]])
        assert updated.id == m.id
        assert updated.get_extra_busy() == [[2, 3], [4, 5]]

    def test_update_extra_busy_nonexistent(self, session):
        assert update_extra_busy(9999, []) is None

    def test_get_member(self, session, members):
        assert get_member(members[0].id).id == members[0].id
        assert get_member(9999) is None

    def test_extra_busy_separado_do_schedule(self, session, workspace):
        m, _ = add_member(workspace.id, "Ana", "CC", [[0, 0]])
        update_extra_busy(m.id, [[5, 5]])
        m2, erro = add_member(workspace.id, "Ana", "CC", [[0, 0], [1, 1]], force=True)
        assert erro is None
        assert m2.get_busy() == [[0, 0], [1, 1]]
        assert m2.get_extra_busy() == [[5, 5]]

    def test_reupload_sobrescreve_por_user_id(self, session, workspace):
        u = get_or_create_user("sub1", "ana@x.com", "Ana")
        m, _ = add_member(workspace.id, "Ana", "CC", [[0, 0], [1, 1]], user_id=u.id)
        m2, erro = add_member(workspace.id, "Ana Souza", "CC", [[2, 2]], force=True, user_id=u.id)
        assert erro is None
        assert m2.id == m.id
        assert m2.get_busy() == [[2, 2]]
        assert len(get_members(workspace.id)) == 1

    def test_reupload_nao_rouba_ownership(self, session, workspace):
        u1 = get_or_create_user("sub1", "a@x.com")
        u2 = get_or_create_user("sub2", "b@x.com")
        m, _ = add_member(workspace.id, "Ana", "CC", [[0, 0]], user_id=u1.id)
        m2, erro = add_member(workspace.id, "Ana", "CC", [[1, 1]], force=True, user_id=u2.id)
        assert erro is None
        assert m2.id == m.id
        assert m2.user_id == u1.id
        assert m2.get_busy() == [[1, 1]]
