import pytest
from fastapi.testclient import TestClient


class TestLanding:
    def test_landing_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_landing_contains_expected_text(self, client):
        resp = client.get("/")
        assert "NoTimeToRelax" in resp.text


class TestCreateWorkspaceAPI:
    def test_create_without_password(self, client):
        resp = client.post("/api/workspace", data={"name": "Meu Workspace"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Meu Workspace"
        assert data["slug"] == "meu-workspace"
        assert data["has_password"] is False

    def test_create_with_password(self, client):
        resp = client.post("/api/workspace", data={"name": "Secreto", "password": "123"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_password"] is True
        assert f"ws_{data['slug']}" in resp.cookies

    def test_create_with_empty_name(self, client):
        resp = client.post("/api/workspace", data={"name": ""})
        assert resp.status_code == 400
        assert "obrigatório" in resp.json()["erro"]

    def test_create_with_blank_name(self, client):
        resp = client.post("/api/workspace", data={"name": "   "})
        assert resp.status_code == 400

    def test_create_duplicate_slug(self, client):
        client.post("/api/workspace", data={"name": "CC"})
        resp = client.post("/api/workspace", data={"name": "CC"})
        assert resp.status_code == 200
        assert resp.json()["slug"] == "cc-1"


class TestDashboardPage:
    def test_dashboard_exists(self, client, workspace):
        resp = client.get(f"/workspace/{workspace.slug}")
        assert resp.status_code == 200
        assert workspace.name in resp.text

    def test_dashboard_nonexistent(self, client):
        resp = client.get("/workspace/nao-existe")
        assert resp.status_code == 200
        assert "não encontrado" in resp.text.lower()

    def test_dashboard_with_password_redirects(self, client, workspace_with_password):
        resp = client.get(f"/workspace/{workspace_with_password.slug}", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]


class TestLoginPage:
    def test_login_page_renders(self, client, workspace_with_password):
        resp = client.get(f"/workspace/{workspace_with_password.slug}/login")
        assert resp.status_code == 200
        assert "Senha" in resp.text

    def test_login_redirects_if_no_password(self, client, workspace):
        resp = client.get(f"/workspace/{workspace.slug}/login", follow_redirects=False)
        assert resp.status_code == 302

    def test_login_nonexistent_workspace(self, client):
        resp = client.get("/workspace/nope/login")
        assert resp.status_code == 200
        assert "não encontrado" in resp.text.lower()


class TestAuthAPI:
    def test_auth_with_correct_password(self, client, workspace_with_password):
        resp = client.post(
            f"/api/workspace/{workspace_with_password.slug}/auth",
            json={"password": "minha-senha"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert f"ws_{workspace_with_password.slug}" in resp.cookies

    def test_auth_with_wrong_password(self, client, workspace_with_password):
        resp = client.post(
            f"/api/workspace/{workspace_with_password.slug}/auth",
            json={"password": "errada"},
        )
        assert resp.status_code == 401
        assert "incorreta" in resp.json()["erro"]

    def test_auth_nonexistent_workspace(self, client):
        resp = client.post(
            "/api/workspace/nope/auth",
            json={"password": "x"},
        )
        assert resp.status_code == 404


class TestUploadPage:
    def test_upload_page_accessible(self, client, workspace):
        resp = client.get(f"/workspace/{workspace.slug}/upload")
        assert resp.status_code == 200
        assert "form" in resp.text.lower()

    def test_upload_page_redirects_if_not_authed(self, client, workspace_with_password):
        resp = client.get(
            f"/workspace/{workspace_with_password.slug}/upload",
            follow_redirects=False,
        )
        assert resp.status_code == 302


class TestMembersAPI:
    def test_get_members(self, client, workspace, members):
        resp = client.get(f"/api/workspace/{workspace.slug}/members")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_get_members_requires_auth(self, client, workspace_with_password):
        resp = client.get(f"/api/workspace/{workspace_with_password.slug}/members")
        assert resp.status_code == 403

    def test_delete_member(self, client, workspace, members):
        resp = client.delete(f"/api/workspace/{workspace.slug}/members/{members[0].id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_nonexistent_member(self, client, workspace):
        resp = client.delete(f"/api/workspace/{workspace.slug}/members/9999")
        assert resp.status_code == 404

    def test_delete_requires_auth(self, client, workspace_with_password, members):
        resp = client.delete(
            f"/api/workspace/{workspace_with_password.slug}/members/{members[0].id}"
        )
        assert resp.status_code == 403

    def test_add_member_manual(self, client, workspace):
        resp = client.post(
            f"/api/workspace/{workspace.slug}/members",
            json={"nome": "Carlos", "curso": "Física", "busy": [[0, 0]]},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Carlos"

    def test_add_member_duplicate(self, client, workspace, members):
        resp = client.post(
            f"/api/workspace/{workspace.slug}/members",
            json={"nome": "Alice", "busy": []},
        )
        assert resp.status_code == 409
        assert resp.json()["nome_existente"] is True


class TestUploadAPI:
    def test_upload_non_pdf(self, client, workspace):
        resp = client.post(
            f"/api/workspace/{workspace.slug}/upload",
            files={"file": ("test.txt", b"not a pdf", "text/plain")},
        )
        assert resp.status_code == 400
        assert "PDF" in resp.json()["erro"]

    def test_upload_requires_auth(self, client, workspace_with_password):
        resp = client.post(
            f"/api/workspace/{workspace_with_password.slug}/upload",
            files={"file": ("test.pdf", b"%PDF-", "application/pdf")},
        )
        assert resp.status_code == 403

    def test_upload_nonexistent_workspace(self, client):
        resp = client.post(
            "/api/workspace/nope/upload",
            files={"file": ("test.pdf", b"%PDF-", "application/pdf")},
        )
        assert resp.status_code == 404


class TestErrorHandling:
    def test_middleware_catches_db_errors(self, client):
        pass
