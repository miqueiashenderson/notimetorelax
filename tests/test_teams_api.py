import pytest

from database import get_or_create_user, add_member, get_teams, get_schedule_entries


def _login(client, sub, email, name=""):
    import main as mm
    user = get_or_create_user(sub, email, name)
    client.cookies.set("user_session", mm._make_user_session_token(user.id))
    return user


def _member(ws_id, name, busy=None, user_id=None):
    m, erro = add_member(ws_id, name, "CC", busy or [], user_id=user_id)
    assert erro is None
    return m


class TestEscalaPage:
    def test_page_renders(self, client, workspace, members):
        resp = client.get(f"/workspace/{workspace.slug}/escala")
        assert resp.status_code == 200
        assert "Escala de trabalho" in resp.text
        assert "Equipes" in resp.text

    def test_page_nonexistent(self, client):
        resp = client.get("/workspace/nope/escala")
        assert resp.status_code == 200
        assert "não encontrado" in resp.text.lower()

    def test_page_redirects_with_password(self, client, workspace_with_password):
        resp = client.get(f"/workspace/{workspace_with_password.slug}/escala", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_dashboard_links_to_escala(self, client, workspace, members):
        resp = client.get(f"/workspace/{workspace.slug}")
        assert f"/workspace/{workspace.slug}/escala" in resp.text


class TestTeamsAPI:
    def test_create_team_as_owner(self, client, workspace, session):
        user = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = user.id
        session.commit()
        resp = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "Fuselagem"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Fuselagem"
        assert data["members"] == []
        assert len(get_teams(workspace.id)) == 1

    def test_create_team_requires_admin(self, client, workspace, session):
        _login(client, "sub-bob", "bob@x.com", "Bob")
        resp = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "X"})
        assert resp.status_code == 403
        assert "administradores" in resp.json()["erro"]

    def test_create_team_unclaimed(self, client, workspace):
        _login(client, "sub-ana", "ana@x.com", "Ana")
        resp = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "X"})
        assert resp.status_code == 403

    def test_create_team_blank_name(self, client, workspace, session):
        user = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = user.id
        session.commit()
        resp = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "  "})
        assert resp.status_code == 400

    def test_get_teams(self, client, workspace, session):
        user = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = user.id
        session.commit()
        client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "A"})
        resp = client.get(f"/api/workspace/{workspace.slug}/teams")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_teams_requires_auth(self, client, workspace_with_password):
        resp = client.get(f"/api/workspace/{workspace_with_password.slug}/teams")
        assert resp.status_code == 403

    def test_delete_team(self, client, workspace, session):
        user = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = user.id
        session.commit()
        tid = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "A"}).json()["id"]
        resp = client.delete(f"/api/workspace/{workspace.slug}/teams/{tid}")
        assert resp.status_code == 200
        assert get_teams(workspace.id) == []


class TestWorkspaceAdmins:
    """Co-donos: podem tudo que o criador pode, exceto apagar o workspace e
    administrar a lista de co-donos; só o criador nomeia/remove co-donos."""

    def _setup(self, client, workspace, session):
        owner = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = owner.id
        session.commit()
        # integrante COM conta (pode virar co-dono)
        cap_user = get_or_create_user("sub-lia", "lia@x.com", "Lia")
        cap = _member(workspace.id, "Lia", user_id=cap_user.id)
        # integrante SEM conta
        sem_conta = _member(workspace.id, "Carla")
        return owner, cap, cap_user, sem_conta

    def test_get_admins_lists_owner(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        resp = client.get(f"/api/workspace/{workspace.slug}/admins")
        assert resp.status_code == 200
        data = resp.json()
        assert data["owner_id"] == owner.id
        assert data["owner"]["name"] == "Ana"
        assert data["admins"] == []

    def test_owner_promotes_member_with_account(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/admins", json={"member_id": cap.id}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert [a["user_id"] for a in data["admins"]] == [cap_user.id]
        assert data["admins"][0]["name"] == "Lia"

    def test_promote_member_without_account_rejected(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/admins", json={"member_id": sem_conta.id}
        )
        assert resp.status_code == 400
        assert "conta" in resp.json()["erro"]

    def test_promote_owner_rejected(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        owner_m = _member(workspace.id, "Ana", user_id=owner.id)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/admins", json={"member_id": owner_m.id}
        )
        assert resp.status_code == 400
        assert "criador" in resp.json()["erro"]

    def test_only_owner_can_promote(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        # promove a cap
        client.post(f"/api/workspace/{workspace.slug}/admins", json={"member_id": cap.id})
        # entra como a co-dona e tenta promover outra pessoa
        import main as mm
        client.cookies.set("user_session", mm._make_user_session_token(cap_user.id))
        resp = client.post(
            f"/api/workspace/{workspace.slug}/admins", json={"member_id": sem_conta.id}
        )
        assert resp.status_code == 403
        assert "criador" in resp.json()["erro"]

    def test_codono_can_create_and_delete_teams(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        client.post(f"/api/workspace/{workspace.slug}/admins", json={"member_id": cap.id})
        # entra como co-dono
        import main as mm
        client.cookies.set("user_session", mm._make_user_session_token(cap_user.id))
        r = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "Co"})
        assert r.status_code == 200
        tid = r.json()["id"]
        r2 = client.delete(f"/api/workspace/{workspace.slug}/teams/{tid}")
        assert r2.status_code == 200

    def test_codono_can_set_leader(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        import main as mm
        client.cookies.set("user_session", mm._make_user_session_token(owner.id))
        tid = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "A"}).json()["id"]
        client.post(f"/api/workspace/{workspace.slug}/teams/{tid}/members", json={"member_id": cap.id})
        client.post(f"/api/workspace/{workspace.slug}/admins", json={"member_id": cap.id})
        # entra como co-dono e promove cap a líder da equipe
        client.cookies.set("user_session", mm._make_user_session_token(cap_user.id))
        resp = client.patch(
            f"/api/workspace/{workspace.slug}/teams/{tid}/leaders/{cap.id}",
            json={"is_leader": True},
        )
        assert resp.status_code == 200

    def test_codono_cannot_delete_workspace(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        import main as mm
        client.cookies.set("user_session", mm._make_user_session_token(owner.id))
        client.post(f"/api/workspace/{workspace.slug}/admins", json={"member_id": cap.id})
        client.cookies.set("user_session", mm._make_user_session_token(cap_user.id))
        resp = client.delete(f"/api/workspace/{workspace.slug}")
        assert resp.status_code == 403

    def test_demote_removes_powers(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        import main as mm
        client.cookies.set("user_session", mm._make_user_session_token(owner.id))
        client.post(f"/api/workspace/{workspace.slug}/admins", json={"member_id": cap.id})
        resp = client.delete(f"/api/workspace/{workspace.slug}/admins/{cap.id}")
        assert resp.status_code == 200
        assert resp.json()["admins"] == []
        # agora, como ex-co-dono, não pode mais criar equipes
        client.cookies.set("user_session", mm._make_user_session_token(cap_user.id))
        r = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "X"})
        assert r.status_code == 403

    def test_escala_page_exposes_is_admin(self, client, workspace, session):
        owner, cap, cap_user, sem_conta = self._setup(client, workspace, session)
        client.post(f"/api/workspace/{workspace.slug}/admins", json={"member_id": cap.id})
        import main as mm
        client.cookies.set("user_session", mm._make_user_session_token(cap_user.id))
        resp = client.get(f"/workspace/{workspace.slug}/escala")
        assert resp.status_code == 200
        assert "const IS_ADMIN = true;" in resp.text
        assert "const IS_OWNER = false;" in resp.text
        assert '"badge"' not in resp.text  # sanity: payload é JSON puro


class TestTeamMembersAPI:
    def _setup(self, client, workspace, session):
        owner = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = owner.id
        session.commit()
        tid = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "A"}).json()["id"]
        leader_user = get_or_create_user("sub-lia", "lia@x.com", "Lia")
        leader_m = _member(workspace.id, "Lia", user_id=leader_user.id)
        other_m = _member(workspace.id, "Carla")
        return owner, tid, leader_user, leader_m, other_m

    def test_add_member_as_owner(self, client, workspace, session):
        owner, tid, leader_user, leader_m, other_m = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": other_m.id},
        )
        assert resp.status_code == 200
        assert [m["member_id"] for m in resp.json()["members"]] == [other_m.id]

    def test_add_member_not_in_workspace(self, client, workspace, session):
        from database import Workspace
        owner, tid, leader_user, leader_m, other_m = self._setup(client, workspace, session)
        outro = Workspace(slug="outro", name="Outro")
        session.add(outro)
        session.commit()
        estranho = _member(outro.id, "Intruso")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": estranho.id},
        )
        assert resp.status_code == 404

    def test_leader_can_add_member_to_own_team(self, client, workspace, session):
        owner, tid, leader_user, leader_m, other_m = self._setup(client, workspace, session)
        client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": leader_m.id, "is_leader": True},
        )
        _login(client, "sub-lia", "lia@x.com", "Lia")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": other_m.id},
        )
        assert resp.status_code == 200
        assert len(resp.json()["members"]) == 2

    def test_leader_cannot_manage_other_team(self, client, workspace, session):
        owner, tid, leader_user, leader_m, other_m = self._setup(client, workspace, session)
        client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": leader_m.id, "is_leader": True},
        )
        other_tid = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "B"}).json()["id"]
        _login(client, "sub-lia", "lia@x.com", "Lia")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/teams/{other_tid}/members",
            json={"member_id": other_m.id},
        )
        assert resp.status_code == 403

    def test_plain_member_cannot_manage(self, client, workspace, session):
        owner, tid, leader_user, leader_m, other_m = self._setup(client, workspace, session)
        _login(client, "sub-x", "x@x.com", "X")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": other_m.id},
        )
        assert resp.status_code == 403

    def test_leader_flag_ignored_for_non_owner(self, client, workspace, session):
        owner, tid, leader_user, leader_m, other_m = self._setup(client, workspace, session)
        client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": leader_m.id, "is_leader": True},
        )
        _login(client, "sub-lia", "lia@x.com", "Lia")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": other_m.id, "is_leader": True},
        )
        assert resp.status_code == 200
        assert all(not m["is_leader"] for m in resp.json()["members"] if m["member_id"] == other_m.id)

    def test_remove_member(self, client, workspace, session):
        owner, tid, leader_user, leader_m, other_m = self._setup(client, workspace, session)
        client.post(f"/api/workspace/{workspace.slug}/teams/{tid}/members", json={"member_id": other_m.id})
        resp = client.delete(f"/api/workspace/{workspace.slug}/teams/{tid}/members/{other_m.id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_set_leader_owner_only(self, client, workspace, session):
        owner, tid, leader_user, leader_m, other_m = self._setup(client, workspace, session)
        client.post(f"/api/workspace/{workspace.slug}/teams/{tid}/members", json={"member_id": other_m.id})
        resp = client.patch(
            f"/api/workspace/{workspace.slug}/teams/{tid}/leaders/{other_m.id}",
            json={"is_leader": True},
        )
        assert resp.status_code == 200
        assert any(m["is_leader"] for m in resp.json()["members"] if m["member_id"] == other_m.id)


class TestScheduleAPI:
    def _setup(self, client, workspace, session, busy=None):
        owner = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = owner.id
        session.commit()
        tid = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "A"}).json()["id"]
        leader_user = get_or_create_user("sub-lia", "lia@x.com", "Lia")
        busy_m = _member(workspace.id, "Lia", busy=busy or [], user_id=leader_user.id)
        client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": busy_m.id, "is_leader": True},
        )
        return owner, tid, leader_user, busy_m

    def test_create_entry_as_owner(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 1, "slot": 2, "task": "Colar", "status": "planejado"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task"] == "Colar"
        assert data["conflicts"] == []
        assert len(get_schedule_entries(workspace.id)) == 1

    def test_create_entry_conflict_requires_force(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session, busy=[[0, 0]])
        payload = {"team_id": tid, "day": 0, "slot": 0, "status": "planejado"}
        resp = client.post(f"/api/workspace/{workspace.slug}/schedule", json=payload)
        assert resp.status_code == 409
        assert resp.json()["needs_confirm"] is True
        assert resp.json()["conflicts"][0]["name"].lower().startswith("lia")
        assert len(get_schedule_entries(workspace.id)) == 0
        resp2 = client.post(f"/api/workspace/{workspace.slug}/schedule", json={**payload, "force": True})
        assert resp2.status_code == 200
        assert resp2.json()["conflicts"][0]["id"] == busy_m.id

    def test_create_entry_invalid_day(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 9, "slot": 0},
        )
        assert resp.status_code == 400

    def test_create_entry_invalid_status(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 0, "slot": 0, "status": "x"},
        )
        assert resp.status_code == 400

    def test_create_entry_team_from_other_workspace(self, client, workspace, session):
        from database import Workspace, create_team
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        outro = Workspace(slug="outro", name="Outro")
        session.add(outro)
        session.commit()
        t_outro = create_team(outro.id, "Intrusa")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": t_outro.id, "day": 0, "slot": 0},
        )
        assert resp.status_code == 404

    def test_leader_can_edit_own_team_schedule(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        _login(client, "sub-lia", "lia@x.com", "Lia")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 2, "slot": 3},
        )
        assert resp.status_code == 200

    def test_leader_cannot_edit_other_team_schedule(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        other_tid = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "B"}).json()["id"]
        _login(client, "sub-lia", "lia@x.com", "Lia")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": other_tid, "day": 2, "slot": 3},
        )
        assert resp.status_code == 403

    def test_plain_member_cannot_create(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        _login(client, "sub-x", "x@x.com", "X")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 0, "slot": 0},
        )
        assert resp.status_code == 403

    def test_get_schedule_includes_conflicts(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session, busy=[[4, 4]])
        client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 4, "slot": 4, "force": True},
        )
        resp = client.get(f"/api/workspace/{workspace.slug}/schedule")
        assert resp.status_code == 200
        assert resp.json()[0]["conflicts"]

    def test_update_entry(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        eid = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 0, "slot": 0},
        ).json()["id"]
        resp = client.patch(
            f"/api/workspace/{workspace.slug}/schedule/{eid}",
            json={"task": "Nova tarefa", "status": "concluido", "day": 3, "slot": 6},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["task"] == "Nova tarefa"
        assert data["status"] == "concluido"
        assert (data["day"], data["slot"]) == (3, 6)

    def test_delete_entry(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        eid = client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 0, "slot": 0},
        ).json()["id"]
        resp = client.delete(f"/api/workspace/{workspace.slug}/schedule/{eid}")
        assert resp.status_code == 200
        assert get_schedule_entries(workspace.id) == []

    def test_export_csv(self, client, workspace, session):
        owner, tid, leader_user, busy_m = self._setup(client, workspace, session)
        client.post(
            f"/api/workspace/{workspace.slug}/schedule",
            json={"team_id": tid, "day": 0, "slot": 0, "task": "Colar", "status": "concluido"},
        )
        resp = client.get(f"/api/workspace/{workspace.slug}/schedule.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "Horário" in resp.text
        assert "Colar" in resp.text
        assert "Concluído" in resp.text

    def test_export_csv_requires_auth(self, client, workspace_with_password):
        resp = client.get(f"/api/workspace/{workspace_with_password.slug}/schedule.csv")
        assert resp.status_code == 403


class TestScheduleBulkAPI:
    def _setup(self, client, workspace, session, busy=None):
        owner = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = owner.id
        session.commit()
        tid = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "A"}).json()["id"]
        leader_user = get_or_create_user("sub-lia", "lia@x.com", "Lia")
        m = _member(workspace.id, "Lia", busy=busy or [], user_id=leader_user.id)
        client.post(
            f"/api/workspace/{workspace.slug}/teams/{tid}/members",
            json={"member_id": m.id, "is_leader": True},
        )
        return owner, tid, leader_user, m

    def test_bulk_creates_multiple(self, client, workspace, session):
        owner, tid, leader_user, m = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk",
            json={"team_id": tid, "task": "Lote", "status": "planejado",
                  "cells": [[0, 0], [0, 1], [0, 2]]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 3
        assert len(get_schedule_entries(workspace.id)) == 3

    def test_bulk_skips_duplicates(self, client, workspace, session):
        owner, tid, leader_user, m = self._setup(client, workspace, session)
        payload = {"team_id": tid, "cells": [[0, 0], [0, 1]]}
        client.post(f"/api/workspace/{workspace.slug}/schedule/bulk", json=payload)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk",
            json={"team_id": tid, "cells": [[0, 1], [0, 2]]},
        )
        assert resp.status_code == 200
        assert resp.json()["created"] == 1
        assert resp.json()["skipped"] == 1
        assert len(get_schedule_entries(workspace.id)) == 3

    def test_bulk_conflict_requires_force(self, client, workspace, session):
        owner, tid, leader_user, m = self._setup(client, workspace, session, busy=[[1, 1]])
        payload = {"team_id": tid, "cells": [[1, 1], [1, 2]]}
        resp = client.post(f"/api/workspace/{workspace.slug}/schedule/bulk", json=payload)
        assert resp.status_code == 409
        assert resp.json()["needs_confirm"] is True
        assert len(get_schedule_entries(workspace.id)) == 0
        resp2 = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk", json={**payload, "force": True}
        )
        assert resp2.status_code == 200
        assert resp2.json()["created"] == 2

    def test_bulk_entries_carry_conflicts(self, client, workspace, session):
        """`entries` devolve os conflitos por célula (contrato p/ atualizar local)."""
        owner, tid, leader_user, m = self._setup(client, workspace, session, busy=[[1, 1]])
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk",
            json={"team_id": tid, "cells": [[1, 1], [1, 2]], "force": True},
        )
        assert resp.status_code == 200
        entries = {(e["day"], e["slot"]): e for e in resp.json()["entries"]}
        assert len(entries) == 2
        assert entries[(1, 1)]["conflicts"][0]["id"] == m.id
        assert entries[(1, 2)]["conflicts"] == []
        # a lista agregada também reflete o conflito
        assert resp.json()["conflicts"][0]["id"] == m.id

    def test_bulk_invalid_cells_filtered(self, client, workspace, session):
        owner, tid, leader_user, m = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk",
            json={"team_id": tid, "cells": [[0, 0], [9, 9], "x", [0, 0]]},
        )
        assert resp.status_code == 200
        assert resp.json()["created"] == 1

    def test_bulk_no_cells(self, client, workspace, session):
        owner, tid, leader_user, m = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk",
            json={"team_id": tid, "cells": []},
        )
        assert resp.status_code == 400

    def test_bulk_invalid_status(self, client, workspace, session):
        owner, tid, leader_user, m = self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk",
            json={"team_id": tid, "status": "x", "cells": [[0, 0]]},
        )
        assert resp.status_code == 400

    def test_bulk_plain_member_forbidden(self, client, workspace, session):
        owner, tid, leader_user, m = self._setup(client, workspace, session)
        _login(client, "sub-x", "x@x.com", "X")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk",
            json={"team_id": tid, "cells": [[0, 0]]},
        )
        assert resp.status_code == 403

    def test_bulk_team_other_workspace(self, client, workspace, session):
        from database import Workspace, create_team
        owner, tid, leader_user, m = self._setup(client, workspace, session)
        outro = Workspace(slug="outro", name="Outro")
        session.add(outro)
        session.commit()
        t_outro = create_team(outro.id, "Intrusa")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk",
            json={"team_id": t_outro.id, "cells": [[0, 0]]},
        )
        assert resp.status_code == 404


class TestScheduleBulkDeleteAPI:
    def _setup(self, client, workspace, session):
        owner = _login(client, "sub-ana", "ana@x.com", "Ana")
        workspace.owner_id = owner.id
        session.commit()
        t1 = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "A"}).json()["id"]
        t2 = client.post(f"/api/workspace/{workspace.slug}/teams", json={"name": "B"}).json()["id"]
        # owner cria entradas via bulk
        for tid in (t1, t2):
            client.post(
                f"/api/workspace/{workspace.slug}/schedule/bulk",
                json={"team_id": tid, "cells": [[0, 0], [0, 1]]},
            )
        return owner, t1, t2

    def _ids(self, workspace):
        return [e.id for e in get_schedule_entries(workspace.id)]

    def test_owner_bulk_delete(self, client, workspace, session):
        self._setup(client, workspace, session)
        ids = self._ids(workspace)
        assert len(ids) == 4
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk-delete",
            json={"ids": ids[:3]},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 3
        assert len(get_schedule_entries(workspace.id)) == 1

    def test_bulk_delete_empty(self, client, workspace, session):
        self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk-delete", json={"ids": []}
        )
        assert resp.status_code == 400
        assert len(get_schedule_entries(workspace.id)) == 4

    def test_bulk_delete_unknown_ids_skipped(self, client, workspace, session):
        self._setup(client, workspace, session)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk-delete",
            json={"ids": [9999, "x"]},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0
        assert resp.json()["skipped"] == 2

    def test_bulk_delete_other_workspace_skipped(self, client, workspace, session):
        from database import Workspace, create_team, add_schedule_entry
        self._setup(client, workspace, session)
        outro = Workspace(slug="outro", name="Outro")
        session.add(outro)
        session.commit()
        t_outro = create_team(outro.id, "X")
        e_outro = add_schedule_entry(outro.id, t_outro.id, 0, 0)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk-delete",
            json={"ids": [e_outro.id]},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0
        assert len(get_schedule_entries(outro.id)) == 1

    def test_leader_deletes_only_own_team(self, client, workspace, session):
        from database import add_team_member
        owner, t1, t2 = self._setup(client, workspace, session)
        leader_user = get_or_create_user("sub-lia", "lia@x.com", "Lia")
        m = _member(workspace.id, "Lia", user_id=leader_user.id)
        add_team_member(t1, m.id, is_leader=True)
        _login(client, "sub-lia", "lia@x.com", "Lia")
        ids = self._ids(workspace)
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk-delete", json={"ids": ids}
        )
        assert resp.status_code == 200
        # apenas as 2 entradas da própria equipe foram apagadas
        assert resp.json()["deleted"] == 2
        restantes = get_schedule_entries(workspace.id)
        assert len(restantes) == 2
        assert all(e.team_id == t2 for e in restantes)

    def test_bulk_delete_non_member_deletes_nothing(self, client, workspace, session):
        # Workspaces sem senha são acessíveis, mas remover exige ser criador/líder.
        self._setup(client, workspace, session)
        ids = self._ids(workspace)
        _login(client, "sub-x", "x@x.com", "X")
        resp = client.post(
            f"/api/workspace/{workspace.slug}/schedule/bulk-delete", json={"ids": ids}
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0
        assert resp.json()["skipped"] == 4
        assert len(get_schedule_entries(workspace.id)) == 4

    def test_bulk_delete_requires_login(self, client, workspace_with_password):
        resp = client.post(
            f"/api/workspace/{workspace_with_password.slug}/schedule/bulk-delete",
            json={"ids": [1]},
        )
        assert resp.status_code == 403
