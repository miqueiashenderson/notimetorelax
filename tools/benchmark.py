import os, sys, json, time, statistics

os.environ["DATABASE_URL"] = "sqlite:////tmp/benchmark_horariolivre.db"
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["GOOGLE_REDIRECT_URI"] = ""
os.environ["SESSION_SECRET"] = "benchmark-secret"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import event
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

import database
from database import Base, Workspace, Member, Team, TeamMember, ScheduleEntry, CORES

DB_PATH = "/tmp/benchmark_horariolivre.db"

# Use a global list since TestClient runs in a different thread
_query_log = []

def _log_query(conn, cursor, statement, params, context, executemany):
    _query_log.append(statement)

def _reset_engine():
    database._db_ready = False
    database.engine = None
    engine = database.get_engine()
    event.listen(engine, "before_cursor_execute", _log_query)
    return engine

def setup():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    engine = _reset_engine()
    Base.metadata.create_all(engine)
    database._db_ready = True

    with Session(engine) as sess:
        ws = Workspace(slug="bench-ws", name="Benchmark Workspace")
        sess.add(ws)
        sess.commit()
        sess.refresh(ws)

        members = []
        for i in range(30):
            m = Member(
                workspace_id=ws.id,
                name=f"Membro {i:02d}",
                course="CC",
                schedule=json.dumps([[i % 7, i % 14]]),
                color=CORES[i % len(CORES)],
            )
            sess.add(m)
            members.append(m)
        sess.commit()

        teams = []
        for i in range(3):
            t = Team(workspace_id=ws.id, name=f"Equipe {i+1}", color=CORES[i % len(CORES)])
            sess.add(t)
            teams.append(t)
        sess.commit()

        for i, m in enumerate(members):
            tm = TeamMember(
                team_id=teams[i % 3].id,
                member_id=m.id,
                is_leader=(i % 10 == 0),
            )
            sess.add(tm)
        sess.commit()

        for i in range(50):
            e = ScheduleEntry(
                workspace_id=ws.id,
                team_id=teams[i % 3].id,
                day=i % 7,
                slot=i % 14,
                task=f"Tarefa {i}",
                status="planejado",
            )
            sess.add(e)
        sess.commit()
        return ws.slug

def medir(client, method, path, body=None, n=20):
    tempos = []
    queries_counts = []
    for _ in range(n):
        _query_log.clear()
        t0 = time.perf_counter()
        if method == "GET":
            r = client.get(path)
        elif method == "POST":
            r = client.post(path, json=body)
        elif method == "DELETE":
            r = client.delete(path, json=body)
        tempos.append((time.perf_counter() - t0) * 1000)
        queries_counts.append(len(_query_log))
    return {
        "min": round(min(tempos), 2),
        "media": round(statistics.mean(tempos), 2),
        "max": round(max(tempos), 2),
        "queries": round(statistics.mean(queries_counts), 1),
    }

def main():
    slug = setup()
    from main import app
    client = TestClient(app, raise_server_exceptions=False)

    # Also listen on the engine the app will actually use
    app_engine = database.get_engine()
    event.listen(app_engine, "before_cursor_execute", _log_query)

    # Warm up: first request triggers init_db via middleware
    client.get(f"/workspace/{slug}/escala")

    rotas = [
        ("GET", f"/workspace/{slug}/escala", None),
        ("GET", f"/api/workspace/{slug}/teams", None),
        ("GET", f"/api/workspace/{slug}/schedule", None),
        ("POST", f"/api/workspace/{slug}/schedule", {"team_id": 1, "day": 0, "slot": 0}),
        ("POST", f"/api/workspace/{slug}/schedule/bulk", {
            "team_id": 1,
            "cells": [[d, s] for d in range(6) for s in range(5)],
        }),
    ]

    print("=" * 80)
    print("BENCHMARK — NoTimeToRelax (antes)")
    print("=" * 80)
    print(f"{'Rota':<50} {'Média (ms)':>10} {'Queries':>8}")
    print("-" * 80)

    for method, path, body in rotas:
        r = medir(client, method, path, body)
        print(f"{method} {path:<45} {r['media']:>10.2f} {r['queries']:>8.1f}")

    with Session(database.get_engine()) as sess:
        ids = [e.id for e in sess.query(ScheduleEntry).filter_by(team_id=1).all()[:30]]
    r = medir(client, "POST", f"/api/workspace/{slug}/schedule/bulk-delete", {"ids": ids})
    print(f"{'POST bulk-delete':<50} {r['media']:>10.2f} {r['queries']:>8.1f}")

    os.makedirs("tests/fixtures", exist_ok=True)
    with open("tests/fixtures/teams_snapshot.json", "w") as f:
        json.dump(client.get(f"/api/workspace/{slug}/teams").json(), f, indent=2, ensure_ascii=False)
    with open("tests/fixtures/schedule_snapshot.json", "w") as f:
        json.dump(client.get(f"/api/workspace/{slug}/schedule").json(), f, indent=2, ensure_ascii=False)
    print("\nSnapshots salvos em tests/fixtures/")

if __name__ == "__main__":
    main()
