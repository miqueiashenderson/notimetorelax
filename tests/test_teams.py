import json
import pytest

from database import (
    add_member, remove_member, create_team, get_teams, get_team,
    update_team, delete_team, add_team_member, get_team_members,
    get_workspace_team_members, remove_team_member, set_team_leader,
    is_team_leader, get_leading_team_ids, get_schedule_entries,
    get_schedule_entry, add_schedule_entry, update_schedule_entry,
    delete_schedule_entry, delete_workspace, get_workspace,
    get_or_create_user, Team, TeamMember, ScheduleEntry, Member, Workspace,
)


def _member(ws_id, name, busy=None, user_id=None):
    m, erro = add_member(ws_id, name, "CC", busy or [], user_id=user_id)
    assert erro is None
    return m


class TestTeams:
    def test_create_team(self, session, workspace):
        t = create_team(workspace.id, "Fuselagem")
        assert t.id is not None
        assert t.name == "Fuselagem"
        assert t.color
        assert [x.name for x in get_teams(workspace.id)] == ["Fuselagem"]

    def test_create_team_sanitizes_name(self, session, workspace):
        t = create_team(workspace.id, "<b>Asas</b>")
        assert "<" not in t.name and ">" not in t.name

    def test_create_team_auto_color_distinct(self, session, workspace):
        t1 = create_team(workspace.id, "A")
        t2 = create_team(workspace.id, "B")
        assert t1.color != t2.color

    def test_update_team(self, session, workspace):
        t = create_team(workspace.id, "Antiga")
        t2 = update_team(t.id, workspace.id, name="Nova", color="#abcdef")
        assert t2.name == "Nova"
        assert t2.color == "#abcdef"

    def test_update_team_outro_workspace(self, session, workspace):
        outro = Workspace(slug="outro", name="Outro")
        session.add(outro)
        session.commit()
        t = create_team(workspace.id, "A")
        assert update_team(t.id, outro.id, name="X") is None

    def test_delete_team(self, session, workspace):
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Alice")
        add_team_member(t.id, m.id)
        add_schedule_entry(workspace.id, t.id, 0, 0)
        assert delete_team(t.id, workspace.id) is True
        assert get_team(t.id) is None
        assert get_team_members(t.id) == []
        assert get_schedule_entries(workspace.id) == []

    def test_delete_team_outro_workspace(self, session, workspace):
        outro = Workspace(slug="outro", name="Outro")
        session.add(outro)
        session.commit()
        t = create_team(workspace.id, "A")
        assert delete_team(t.id, outro.id) is False


class TestTeamMembers:
    def test_add_member(self, session, workspace):
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Alice")
        add_team_member(t.id, m.id)
        rows = get_team_members(t.id)
        assert len(rows) == 1
        assert rows[0].member_id == m.id
        assert rows[0].is_leader is False

    def test_add_member_idempotent(self, session, workspace):
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Alice")
        add_team_member(t.id, m.id)
        add_team_member(t.id, m.id)
        assert len(get_team_members(t.id)) == 1

    def test_add_member_promote_leader(self, session, workspace):
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Alice")
        add_team_member(t.id, m.id)
        add_team_member(t.id, m.id, is_leader=True)
        assert get_team_members(t.id)[0].is_leader is True

    def test_remove_member(self, session, workspace):
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Alice")
        add_team_member(t.id, m.id)
        assert remove_team_member(t.id, m.id) is True
        assert get_team_members(t.id) == []
        assert remove_team_member(t.id, m.id) is False

    def test_set_team_leader(self, session, workspace):
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Alice")
        add_team_member(t.id, m.id)
        assert set_team_leader(t.id, m.id, True).is_leader is True
        assert set_team_leader(t.id, m.id, False).is_leader is False
        assert set_team_leader(t.id, 9999, True) is None

    def test_get_workspace_team_members(self, session, workspace):
        t1 = create_team(workspace.id, "A")
        t2 = create_team(workspace.id, "B")
        m = _member(workspace.id, "Alice")
        add_team_member(t1.id, m.id)
        add_team_member(t2.id, m.id)
        assert len(get_workspace_team_members(workspace.id)) == 2

    def test_is_team_leader(self, session, workspace):
        user = get_or_create_user("sub-lia", "lia@x.com", "Lia")
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Lia", user_id=user.id)
        add_team_member(t.id, m.id, is_leader=True)
        assert is_team_leader(t.id, user.id, workspace.id) is True
        assert is_team_leader(t.id, 9999, workspace.id) is False

    def test_is_team_leader_false_for_non_leader(self, session, workspace):
        user = get_or_create_user("sub-lia", "lia@x.com", "Lia")
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Lia", user_id=user.id)
        add_team_member(t.id, m.id, is_leader=False)
        assert is_team_leader(t.id, user.id, workspace.id) is False

    def test_get_leading_team_ids(self, session, workspace):
        user = get_or_create_user("sub-lia", "lia@x.com", "Lia")
        t1 = create_team(workspace.id, "A")
        t2 = create_team(workspace.id, "B")
        m = _member(workspace.id, "Lia", user_id=user.id)
        add_team_member(t1.id, m.id, is_leader=True)
        add_team_member(t2.id, m.id, is_leader=False)
        assert get_leading_team_ids(workspace.id, user.id) == {t1.id}

    def test_remove_member_clears_team_membership(self, session, workspace):
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Alice")
        add_team_member(t.id, m.id)
        remove_member(m.id, workspace.id)
        assert get_team_members(t.id) == []


class TestSchedule:
    def test_add_and_get(self, session, workspace):
        t = create_team(workspace.id, "A")
        e = add_schedule_entry(workspace.id, t.id, 2, 5, task="Montar cauda", status="em_andamento")
        assert e.id is not None
        assert get_schedule_entry(e.id).task == "Montar cauda"
        assert len(get_schedule_entries(workspace.id)) == 1

    def test_task_sanitized(self, session, workspace):
        t = create_team(workspace.id, "A")
        e = add_schedule_entry(workspace.id, t.id, 0, 0, task="<script>alert(1)</script>ok")
        assert "<" not in e.task and ">" not in e.task

    def test_update_entry(self, session, workspace):
        t = create_team(workspace.id, "A")
        e = add_schedule_entry(workspace.id, t.id, 0, 0, task="x", status="planejado")
        e2 = update_schedule_entry(e.id, workspace.id, task="y", status="concluido", day=3, slot=4)
        assert e2.task == "y"
        assert e2.status == "concluido"
        assert (e2.day, e2.slot) == (3, 4)

    def test_update_nonexistent(self, session, workspace):
        assert update_schedule_entry(9999, workspace.id, task="x") is None

    def test_delete_entry(self, session, workspace):
        t = create_team(workspace.id, "A")
        e = add_schedule_entry(workspace.id, t.id, 0, 0)
        assert delete_schedule_entry(e.id, workspace.id) is True
        assert get_schedule_entry(e.id) is None
        assert delete_schedule_entry(e.id, workspace.id) is False


class TestWorkspaceCleanup:
    def test_delete_workspace_removes_teams_and_entries(self, session, workspace):
        t = create_team(workspace.id, "A")
        m = _member(workspace.id, "Alice")
        add_team_member(t.id, m.id)
        add_schedule_entry(workspace.id, t.id, 0, 0)
        assert delete_workspace(workspace.slug) is True
        assert get_workspace(workspace.slug) is None
        assert get_teams(workspace.id) == []
        assert get_schedule_entries(workspace.id) == []
