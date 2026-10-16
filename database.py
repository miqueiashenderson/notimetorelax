import json, os, re, hashlib, hmac, secrets, unicodedata, socket
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
from sqlalchemy import create_engine, ForeignKey, UniqueConstraint, Index, and_, insert, or_, text
from sqlalchemy.pool import NullPool, QueuePool
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from datetime import datetime, timezone

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_SQLITE_PATH = os.path.join(DATA_DIR, "horariolivre.db")


def get_engine():
    global engine
    if engine is not None:
        return engine

    url = os.environ.get("DATABASE_URL")
    if url and "[YOUR-PASSWORD]" not in url and "[SUA-SENHA]" not in url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        try:
            test_engine = create_engine(
                url,
                pool_pre_ping=True,
                poolclass=QueuePool,
                pool_size=5,
                max_overflow=5,
                pool_recycle=1800,
            )
            with test_engine.connect():
                pass
            engine = test_engine
            return engine
        except Exception as e:
            print(f"[AVISO] Falha ao conectar ao banco remoto: {e}. Tentando SQLite local.")

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        engine = create_engine(f"sqlite:///{DEFAULT_SQLITE_PATH}")
        return engine
    except Exception as e:
        raise RuntimeError(
            "Banco de dados indisponível: configure DATABASE_URL "
            "(Supabase) nas variáveis de ambiente do servidor."
        ) from e


engine = None


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "has_password": bool(self.password_hash),
            "owner_id": self.owner_id,
            "created_at": self.created_at,
        }


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(unique=True, nullable=False)
    google_email: Mapped[str] = mapped_column(nullable=False)
    google_name: Mapped[str] = mapped_column(default="")
    google_picture: Mapped[str] = mapped_column(default="")
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self):
        return {
            "id": self.id,
            "google_sub": self.google_sub,
            "google_email": self.google_email,
            "google_name": self.google_name,
            "google_picture": self.google_picture,
            "created_at": self.created_at,
        }


class WorkspaceAdmin(Base):
    """Co-dono (administrador) de um workspace.

    O criador (`Workspace.owner_id`) é administrador implícito; esta tabela
    guarda os demais administradores. Co-donos podem tudo que o criador pode,
    exceto apagar o workspace e mexer na lista de co-donos.
    """

    __tablename__ = "workspace_admins"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_admin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self):
        return {
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
        }


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    course: Mapped[str] = mapped_column(default="")
    schedule: Mapped[str] = mapped_column(default="[]")
    extra_busy: Mapped[str] = mapped_column(default="[]")
    # Blocos do schedule (PDF/SIGAA) que o próprio aluno apagou da grade.
    removed_busy: Mapped[str] = mapped_column(default="[]")
    color: Mapped[str] = mapped_column(default="#3ddbd9")
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )

    def get_busy(self):
        """Aulas do PDF/SIGAA, já descontando as que o aluno apagou."""
        schedule = json.loads(self.schedule)
        removidos = {tuple(b) for b in json.loads(self.removed_busy)}
        return [b for b in schedule if tuple(b) not in removidos]

    def get_removed_busy(self):
        return json.loads(self.removed_busy)

    def get_extra_busy(self):
        return json.loads(self.extra_busy)

    def get_all_busy(self):
        return self.get_busy() + self.get_extra_busy()

    def to_dict(self):
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "course": self.course,
            "color": self.color,
            "busy": self.get_busy(),
            "extra_busy": self.get_extra_busy(),
            "removed_busy": self.get_removed_busy(),
            "total": len(self.get_all_busy()),
            "user_id": self.user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Team(Base):
    """Equipe/grupo de trabalho dentro de um workspace.

    O criador do workspace administra as equipes; um membro marcado como
    líder pode gerenciar a própria equipe e a escala dela.
    """

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    color: Mapped[str] = mapped_column(default="#3ddbd9")
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self):
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "color": self.color,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TeamMember(Base):
    """Associação N:N entre equipes e membros do workspace."""

    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("team_id", "member_id", name="uq_team_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), index=True, nullable=False
    )
    is_leader: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self):
        return {
            "team_id": self.team_id,
            "member_id": self.member_id,
            "is_leader": bool(self.is_leader),
        }


SCHEDULE_STATUSES = ("planejado", "em_andamento", "concluido")


class ScheduleEntry(Base):
    """Uma atribuição de trabalho na escala semanal: equipe x (dia, slot)."""

    __tablename__ = "schedule_entries"
    # Índice composto: acelera a checagem de duplicados por (workspace, equipe)
    # e a listagem da escala por workspace.
    __table_args__ = (
        Index("ix_schedule_entries_ws_team_day_slot", "workspace_id", "team_id", "day", "slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day: Mapped[int] = mapped_column(nullable=False)
    slot: Mapped[int] = mapped_column(nullable=False)
    task: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(default="planejado")
    created_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self):
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "team_id": self.team_id,
            "day": self.day,
            "slot": self.slot,
            "task": self.task,
            "status": self.status,
            "created_by_user_id": self.created_by_user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


CORES = [
    "#3ddbd9", "#ee5396", "#42be65", "#d2a106", "#be95ff",
    "#4589ff", "#ff7eb6", "#08bdba", "#ff8389", "#a56eff",
    "#6fdc8c", "#ff9f43", "#7a4bff", "#ff6b6b", "#4dd0e1",
    "#ffb74d", "#81c784", "#f06292", "#64b5f6", "#ba68c8",
]


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{h.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":", 1)
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
        return hmac.compare_digest(h, calc)
    except Exception:
        return False


def _sanitize_texto(valor: str, max_len: int = 120) -> str:
    """Remove tags/ângulos e caracteres de controle antes de gravar no banco."""
    if not valor:
        return ""
    t = valor.strip().replace("<", "").replace(">", "")
    t = re.sub(r"[\x00-\x1f\x7f]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t[:max_len]


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII')
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s)
    return s.strip("-")


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate(engine)


def _migrate(engine):
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tables = set(insp.get_table_names())

    if "members" in tables:
        member_cols = {c["name"] for c in insp.get_columns("members")}
        with engine.begin() as conn:
            if "extra_busy" not in member_cols:
                conn.execute(text("ALTER TABLE members ADD COLUMN extra_busy TEXT DEFAULT '[]'"))
            if "removed_busy" not in member_cols:
                conn.execute(text("ALTER TABLE members ADD COLUMN removed_busy TEXT DEFAULT '[]'"))
            if "user_id" not in member_cols:
                conn.execute(text("ALTER TABLE members ADD COLUMN user_id INTEGER"))

    if "workspaces" in tables:
        ws_cols = {c["name"] for c in insp.get_columns("workspaces")}
        with engine.begin() as conn:
            if "owner_id" not in ws_cols:
                conn.execute(text("ALTER TABLE workspaces ADD COLUMN owner_id INTEGER"))

    if "schedule_entries" in tables:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_schedule_entries_ws_team_day_slot "
                "ON schedule_entries (workspace_id, team_id, day, slot)"
            ))


def create_workspace(name: str, password: str | None = None, owner_id: int | None = None):
    base_slug = slugify(name)
    slug = base_slug
    with Session(get_engine()) as sess:
        counter = 1
        while sess.query(Workspace).filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1
        pw_hash = hash_password(password) if password else None
        ws = Workspace(slug=slug, name=name, password_hash=pw_hash, owner_id=owner_id)
        sess.add(ws)
        sess.commit()
        sess.refresh(ws)
        return ws


def get_workspace(slug: str):
    with Session(get_engine()) as sess:
        return sess.query(Workspace).filter_by(slug=slug).first()


def delete_workspace(slug: str):
    with Session(get_engine()) as sess:
        ws = sess.query(Workspace).filter_by(slug=slug).first()
        if not ws:
            return False
        team_ids = [t.id for t in sess.query(Team).filter_by(workspace_id=ws.id).all()]
        sess.query(ScheduleEntry).filter_by(workspace_id=ws.id).delete()
        if team_ids:
            sess.query(TeamMember).filter(TeamMember.team_id.in_(team_ids)).delete(
                synchronize_session=False
            )
        sess.query(Team).filter_by(workspace_id=ws.id).delete()
        sess.query(Member).filter_by(workspace_id=ws.id).delete()
        sess.query(WorkspaceAdmin).filter_by(workspace_id=ws.id).delete()
        sess.delete(ws)
        sess.commit()
        return True


def claim_workspace(slug: str, owner_id: int):
    """Atribui o usuário como criador do workspace se ainda não houver dono.
    Retorna o workspace atualizado, None se não existir, ou False se já tiver dono."""
    with Session(get_engine()) as sess:
        ws = sess.query(Workspace).filter_by(slug=slug).first()
        if not ws:
            return None
        if ws.owner_id is not None:
            return False
        ws.owner_id = owner_id
        sess.commit()
        sess.refresh(ws)
        return ws


def get_members(workspace_id: int):
    with Session(get_engine()) as sess:
        return list(
            sess.query(Member).filter_by(workspace_id=workspace_id).all()
        )


def get_workspace_admin_user_ids(workspace_id: int) -> list[int]:
    """IDs de usuário dos co-donos (não inclui o criador)."""
    with Session(get_engine()) as sess:
        rows = (
            sess.query(WorkspaceAdmin.user_id)
            .filter_by(workspace_id=workspace_id)
            .order_by(WorkspaceAdmin.id)
            .all()
        )
        return [r[0] for r in rows]


def is_workspace_admin(workspace_id: int, user_id: int) -> bool:
    """True se o usuário é co-dono (não considera o criador)."""
    if not user_id:
        return False
    with Session(get_engine()) as sess:
        return (
            sess.query(WorkspaceAdmin.id)
            .filter_by(workspace_id=workspace_id, user_id=user_id)
            .first()
            is not None
        )


def add_workspace_admin(workspace_id: int, user_id: int) -> bool:
    """Promove o usuário a co-dono. True se adicionou, False se já era co-dono."""
    if not user_id:
        return False
    with Session(get_engine()) as sess:
        existe = (
            sess.query(WorkspaceAdmin.id)
            .filter_by(workspace_id=workspace_id, user_id=user_id)
            .first()
        )
        if existe:
            return False
        sess.add(WorkspaceAdmin(workspace_id=workspace_id, user_id=user_id))
        sess.commit()
        return True


def remove_workspace_admin(workspace_id: int, user_id: int) -> bool:
    """Remove o cargo de co-dono. True se removeu algo."""
    with Session(get_engine()) as sess:
        n = (
            sess.query(WorkspaceAdmin)
            .filter_by(workspace_id=workspace_id, user_id=user_id)
            .delete()
        )
        sess.commit()
        return bool(n)


def get_member(member_id: int):
    with Session(get_engine()) as sess:
        return sess.query(Member).filter_by(id=member_id).first()


def get_or_create_user(google_sub: str, google_email: str, google_name: str = "", google_picture: str = ""):
    with Session(get_engine()) as sess:
        u = sess.query(User).filter_by(google_sub=google_sub).first()
        if not u:
            u = User(
                google_sub=google_sub,
                google_email=google_email,
                google_name=google_name,
                google_picture=google_picture,
            )
            sess.add(u)
            sess.commit()
            sess.refresh(u)
            return u
        changed = False
        if u.google_email != google_email:
            u.google_email = google_email
            changed = True
        if google_name and u.google_name != google_name:
            u.google_name = google_name
            changed = True
        if google_picture and u.google_picture != google_picture:
            u.google_picture = google_picture
            changed = True
        if changed:
            sess.commit()
            sess.refresh(u)
        return u


def get_user(user_id: int):
    with Session(get_engine()) as sess:
        return sess.query(User).filter_by(id=user_id).first()


def update_extra_busy(member_id: int, extra_busy: list):
    with Session(get_engine()) as sess:
        m = sess.query(Member).filter_by(id=member_id).first()
        if not m:
            return None
        m.extra_busy = json.dumps(extra_busy)
        m.updated_at = datetime.now(timezone.utc).isoformat()
        sess.commit()
        sess.refresh(m)
        return m


def _busy_dict_from_row(row) -> dict:
    """Monta o payload do membro a partir de uma linha RETURNING, aplicando
    o mesmo filtro de removed_busy do Member.get_busy()."""
    removidos = {tuple(b) for b in json.loads(row.removed_busy)}
    busy = [b for b in json.loads(row.schedule) if tuple(b) not in removidos]
    extra = json.loads(row.extra_busy)
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "name": row.name,
        "course": row.course,
        "color": row.color,
        "busy": busy,
        "extra_busy": extra,
        "removed_busy": json.loads(row.removed_busy),
        "total": len(busy) + len(extra),
        "user_id": row.user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


_MEMBER_RETURNING = (
    "RETURNING id, workspace_id, name, course, color, schedule, "
    "extra_busy, removed_busy, user_id, created_at, updated_at"
)


def _update_own_busy_fields(
    member_id: int,
    workspace_id: int,
    owner_user_id: int,
    extra_busy: list | None = None,
    removed_busy: list | None = None,
):
    """Atualiza os campos de horário de um membro do próprio usuário em UMA
    ida ao banco. None/False seguem a semântica de update_own_extra_busy."""
    sets = ["updated_at = :now"]
    params: dict = {"now": datetime.now(timezone.utc).isoformat()}
    if extra_busy is not None:
        sets.append("extra_busy = :eb")
        params["eb"] = json.dumps(extra_busy)
    if removed_busy is not None:
        sets.append("removed_busy = :rb")
        params["rb"] = json.dumps(removed_busy)
    params.update({"mid": member_id, "wid": workspace_id, "uid": owner_user_id})
    stmt = text(
        f"UPDATE members SET {', '.join(sets)} "
        "WHERE id = :mid AND workspace_id = :wid AND user_id = :uid "
        + _MEMBER_RETURNING
    )
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        row = conn.execute(stmt, params).first()
    if row is None:
        m = get_member(member_id)
        if m is None or m.workspace_id != workspace_id:
            return None
        return False
    return _busy_dict_from_row(row)


def update_own_extra_busy(member_id: int, extra_busy: list, workspace_id: int, owner_user_id: int):
    """Atualiza o extra_busy de um membro do próprio usuário em UMA ida ao banco.
    Retorna dict do membro atualizado, None se não existir neste workspace,
    ou False se o membro não pertence ao usuário."""
    return _update_own_busy_fields(
        member_id, workspace_id, owner_user_id, extra_busy=extra_busy
    )


def update_own_busy(
    member_id: int,
    removed_busy: list | None,
    extra_busy: list | None,
    workspace_id: int,
    owner_user_id: int,
):
    """Atualiza removed_busy (aulas apagadas da grade) e/ou extra_busy do
    próprio usuário em UMA ida ao banco. Passar None mantém o valor atual.
    Mesma semântica de retorno de update_own_extra_busy."""
    return _update_own_busy_fields(
        member_id,
        workspace_id,
        owner_user_id,
        extra_busy=extra_busy,
        removed_busy=removed_busy,
    )


def add_member(
    workspace_id: int, name: str, course: str, busy: list, force: bool = False, user_id: int | None = None
):
    name = _sanitize_texto(name)
    course = _sanitize_texto(course, max_len=60)
    with Session(get_engine()) as sess:
        existing = None
        if user_id is not None and force:
            existing = (
                sess.query(Member)
                .filter_by(workspace_id=workspace_id, user_id=user_id)
                .first()
            )
            if existing:
                existing.name = name
                existing.course = course
                existing.schedule = json.dumps(busy)
                existing.removed_busy = "[]"
                existing.updated_at = datetime.now(timezone.utc).isoformat()
                sess.commit()
                sess.refresh(existing)
                return existing, None
        if existing is None:
            existing = (
                sess.query(Member)
                .filter_by(workspace_id=workspace_id, name=name)
                .first()
            )
        if existing and not force:
            return None, "Já existe um membro com este nome."
        if existing:
            existing.schedule = json.dumps(busy)
            existing.removed_busy = "[]"
            existing.course = course
            if user_id is not None and existing.user_id is None:
                existing.user_id = user_id
            existing.updated_at = datetime.now(timezone.utc).isoformat()
            sess.commit()
            sess.refresh(existing)
            return existing, None
        count = (
            sess.query(Member).filter_by(workspace_id=workspace_id).count()
        )
        color = CORES[count % len(CORES)]
        member = Member(
            workspace_id=workspace_id,
            name=name,
            course=course,
            schedule=json.dumps(busy),
            color=color,
            user_id=user_id,
        )
        sess.add(member)
        sess.commit()
        sess.refresh(member)
        return member, None


def remove_member(member_id: int, workspace_id: int):
    with Session(get_engine()) as sess:
        m = sess.query(Member).filter_by(id=member_id, workspace_id=workspace_id).first()
        if m:
            sess.query(TeamMember).filter_by(member_id=member_id).delete()
            sess.delete(m)
            sess.commit()
            return True
        return False


# ─── Equipes (times/grupos) ─────────────────────────────────────────────────


def get_teams(workspace_id: int):
    with Session(get_engine()) as sess:
        return list(
            sess.query(Team).filter_by(workspace_id=workspace_id).order_by(Team.id).all()
        )


def get_team(team_id: int):
    with Session(get_engine()) as sess:
        return sess.query(Team).filter_by(id=team_id).first()


def create_team(workspace_id: int, name: str, color: str | None = None):
    name = _sanitize_texto(name, max_len=60)
    with Session(get_engine()) as sess:
        if not color:
            count = sess.query(Team).filter_by(workspace_id=workspace_id).count()
            color = CORES[count % len(CORES)]
        team = Team(workspace_id=workspace_id, name=name, color=color)
        sess.add(team)
        sess.commit()
        sess.refresh(team)
        return team


def update_team(team_id: int, workspace_id: int, name: str | None = None, color: str | None = None):
    with Session(get_engine()) as sess:
        t = sess.query(Team).filter_by(id=team_id, workspace_id=workspace_id).first()
        if not t:
            return None
        if name is not None:
            nome = _sanitize_texto(name, max_len=60)
            if nome:
                t.name = nome
        if color:
            t.color = color
        t.updated_at = datetime.now(timezone.utc).isoformat()
        sess.commit()
        sess.refresh(t)
        return t


def delete_team(team_id: int, workspace_id: int):
    with Session(get_engine()) as sess:
        t = sess.query(Team).filter_by(id=team_id, workspace_id=workspace_id).first()
        if not t:
            return False
        sess.query(TeamMember).filter_by(team_id=team_id).delete()
        sess.query(ScheduleEntry).filter_by(team_id=team_id).delete()
        sess.delete(t)
        sess.commit()
        return True


def get_team_members(team_id: int):
    with Session(get_engine()) as sess:
        return list(sess.query(TeamMember).filter_by(team_id=team_id).all())


def get_workspace_team_members(workspace_id: int):
    with Session(get_engine()) as sess:
        return list(
            sess.query(TeamMember)
            .join(Team, TeamMember.team_id == Team.id)
            .filter(Team.workspace_id == workspace_id)
            .all()
        )


def add_team_member(team_id: int, member_id: int, is_leader: bool = False):
    with Session(get_engine()) as sess:
        tm = (
            sess.query(TeamMember)
            .filter_by(team_id=team_id, member_id=member_id)
            .first()
        )
        if tm:
            if is_leader and not tm.is_leader:
                tm.is_leader = True
                sess.commit()
                sess.refresh(tm)
            return tm
        tm = TeamMember(team_id=team_id, member_id=member_id, is_leader=bool(is_leader))
        sess.add(tm)
        sess.commit()
        sess.refresh(tm)
        return tm


def remove_team_member(team_id: int, member_id: int):
    with Session(get_engine()) as sess:
        tm = (
            sess.query(TeamMember)
            .filter_by(team_id=team_id, member_id=member_id)
            .first()
        )
        if not tm:
            return False
        sess.delete(tm)
        sess.commit()
        return True


def set_team_leader(team_id: int, member_id: int, is_leader: bool):
    with Session(get_engine()) as sess:
        tm = (
            sess.query(TeamMember)
            .filter_by(team_id=team_id, member_id=member_id)
            .first()
        )
        if not tm:
            return None
        tm.is_leader = bool(is_leader)
        sess.commit()
        sess.refresh(tm)
        return tm


def is_team_leader(team_id: int, user_id: int, workspace_id: int) -> bool:
    """True se o usuário é líder de uma equipe do workspace."""
    with Session(get_engine()) as sess:
        row = (
            sess.query(TeamMember.id)
            .join(Member, TeamMember.member_id == Member.id)
            .filter(
                TeamMember.team_id == team_id,
                TeamMember.is_leader.is_(True),
                Member.user_id == user_id,
                Member.workspace_id == workspace_id,
            )
            .first()
        )
        return row is not None


def get_leading_team_ids(workspace_id: int, user_id: int):
    """Conjunto de IDs de equipes que o usuário lidera neste workspace."""
    with Session(get_engine()) as sess:
        rows = (
            sess.query(TeamMember.team_id)
            .join(Member, TeamMember.member_id == Member.id)
            .filter(
                TeamMember.is_leader.is_(True),
                Member.user_id == user_id,
                Member.workspace_id == workspace_id,
            )
            .all()
        )
        return {r[0] for r in rows}


# ─── Escala de trabalho ─────────────────────────────────────────────────────


def get_schedule_entries(workspace_id: int):
    with Session(get_engine()) as sess:
        return list(
            sess.query(ScheduleEntry)
            .filter_by(workspace_id=workspace_id)
            .order_by(ScheduleEntry.day, ScheduleEntry.slot, ScheduleEntry.id)
            .all()
        )


def get_schedule_entry(entry_id: int):
    with Session(get_engine()) as sess:
        return sess.query(ScheduleEntry).filter_by(id=entry_id).first()


def add_schedule_entry(
    workspace_id: int,
    team_id: int,
    day: int,
    slot: int,
    task: str = "",
    status: str = "planejado",
    created_by_user_id: int | None = None,
):
    with Session(get_engine()) as sess:
        entry = ScheduleEntry(
            workspace_id=workspace_id,
            team_id=team_id,
            day=day,
            slot=slot,
            task=_sanitize_texto(task, max_len=200),
            status=status,
            created_by_user_id=created_by_user_id,
        )
        sess.add(entry)
        sess.commit()
        sess.refresh(entry)
        return entry


def update_schedule_entry(
    entry_id: int,
    workspace_id: int,
    task: str | None = None,
    status: str | None = None,
    team_id: int | None = None,
    day: int | None = None,
    slot: int | None = None,
):
    with Session(get_engine()) as sess:
        e = sess.query(ScheduleEntry).filter_by(id=entry_id, workspace_id=workspace_id).first()
        if not e:
            return None
        if task is not None:
            e.task = _sanitize_texto(task, max_len=200)
        if status is not None:
            e.status = status
        if team_id is not None:
            e.team_id = team_id
        if day is not None:
            e.day = day
        if slot is not None:
            e.slot = slot
        e.updated_at = datetime.now(timezone.utc).isoformat()
        sess.commit()
        sess.refresh(e)
        return e


def delete_schedule_entry(entry_id: int, workspace_id: int):
    with Session(get_engine()) as sess:
        e = sess.query(ScheduleEntry).filter_by(id=entry_id, workspace_id=workspace_id).first()
        if not e:
            return False
        sess.delete(e)
        sess.commit()
        return True


def add_schedule_entries_bulk(
    workspace_id: int,
    team_id: int,
    cells,
    task: str = "",
    status: str = "planejado",
    created_by_user_id: int | None = None,
):
    """Insere várias entradas da escala em UMA ida ao banco (INSERT em lote).

    Recebe `cells` como iterável de pares (dia, slot). Retorna a lista de dicts
    das entradas criadas (com id), na ordem recebida.
    """
    task = _sanitize_texto(task, max_len=200)
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "day": d,
            "slot": s,
            "task": task,
            "status": status,
            "created_by_user_id": created_by_user_id,
            "created_at": now,
            "updated_at": now,
        }
        for (d, s) in cells
    ]
    if not rows:
        return []
    with Session(get_engine()) as sess:
        # execute() com lista de parâmetros => executemany: UMA ida ao banco,
        # tanto no SQLite quanto no Postgres (não um INSERT por célula).
        sess.execute(insert(ScheduleEntry.__table__), rows)
        sess.commit()
        # Busca as entradas recém-criadas em UMA query (para devolver os ids).
        pares = or_(
            *[and_(ScheduleEntry.day == d, ScheduleEntry.slot == s) for (d, s) in cells]
        )
        criadas = (
            sess.query(ScheduleEntry)
            .filter(
                ScheduleEntry.workspace_id == workspace_id,
                ScheduleEntry.team_id == team_id,
                pares,
            )
            .all()
        )
        return [e.to_dict() for e in criadas]


def delete_schedule_entries_bulk(entry_ids, workspace_id: int) -> int:
    """Apaga várias entradas da escala em UMA query (DELETE ... WHERE id IN).

    Retorna quantas linhas foram apagadas."""
    ids = [int(i) for i in entry_ids]
    if not ids:
        return 0
    with Session(get_engine()) as sess:
        n = (
            sess.query(ScheduleEntry)
            .filter(ScheduleEntry.workspace_id == workspace_id, ScheduleEntry.id.in_(ids))
            .delete(synchronize_session=False)
        )
        sess.commit()
        return int(n or 0)
