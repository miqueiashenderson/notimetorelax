"""Regressão: _ensure_db() deve criar tabelas novas em bancos já existentes.

O D5 fazia _ensure_db() pular init_db() sempre que a tabela "workspaces" já
existisse. Isso impedia a criação de tabelas introduzidas depois (ex.:
workspace_admins, do recurso de co-donos), quebrando a rota da escala em
produção (Supabase) com "Erro interno". Os testes não pegavam porque o
conftest cria todas as tabelas antes de rodar.
"""
from sqlalchemy import create_engine, inspect

import database
import main as mm
from database import Base


def test_ensure_db_cria_tabela_ausente(tmp_path, monkeypatch):
    db_path = tmp_path / "legado.db"
    eng = create_engine(f"sqlite:///{db_path}")

    # Simula um banco antigo: todas as tabelas, EXCETO workspace_admins.
    legado = [t for t in Base.metadata.sorted_tables if t.name != "workspace_admins"]
    Base.metadata.create_all(eng, tables=legado)

    assert "workspaces" in inspect(eng).get_table_names()
    assert "workspace_admins" not in inspect(eng).get_table_names()

    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(mm, "_db_ready", False)

    mm._ensure_db()

    tabelas = set(inspect(eng).get_table_names())
    assert "workspace_admins" in tabelas
    assert set(Base.metadata.tables).issubset(tabelas)


def test_ensure_db_roda_apenas_uma_vez(monkeypatch):
    chamadas = []

    def _fake_init_db():
        chamadas.append(1)

    monkeypatch.setattr(mm, "init_db", _fake_init_db)
    monkeypatch.setattr(mm, "_db_ready", False)

    mm._ensure_db()
    mm._ensure_db()

    assert len(chamadas) == 1
