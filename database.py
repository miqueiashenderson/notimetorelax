import json, os, re, hashlib, hmac, secrets, unicodedata, socket
from urllib.parse import urlparse, urlunparse
from dotenv import load_dotenv
from sqlalchemy import create_engine, ForeignKey, text
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
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "has_password": bool(self.password_hash),
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
        return json.loads(self.schedule)

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
            "total": len(self.get_all_busy()),
            "user_id": self.user_id,
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
    if "members" not in set(insp.get_table_names()):
        return
    member_cols = {c["name"] for c in insp.get_columns("members")}
    with engine.begin() as conn:
        if "extra_busy" not in member_cols:
            conn.execute(text("ALTER TABLE members ADD COLUMN extra_busy TEXT DEFAULT '[]'"))
        if "user_id" not in member_cols:
            conn.execute(text("ALTER TABLE members ADD COLUMN user_id INTEGER"))


def create_workspace(name: str, password: str | None = None):
    base_slug = slugify(name)
    slug = base_slug
    with Session(get_engine()) as sess:
        counter = 1
        while sess.query(Workspace).filter_by(slug=slug).first():
            slug = f"{base_slug}-{counter}"
            counter += 1
        pw_hash = hash_password(password) if password else None
        ws = Workspace(slug=slug, name=name, password_hash=pw_hash)
        sess.add(ws)
        sess.commit()
        sess.refresh(ws)
        return ws


def get_workspace(slug: str):
    with Session(get_engine()) as sess:
        return sess.query(Workspace).filter_by(slug=slug).first()


def get_members(workspace_id: int):
    with Session(get_engine()) as sess:
        return list(
            sess.query(Member).filter_by(workspace_id=workspace_id).all()
        )


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


def update_own_extra_busy(member_id: int, extra_busy: list, workspace_id: int, owner_user_id: int):
    """Atualiza o extra_busy de um membro do próprio usuário em UMA ida ao banco.
    Retorna dict do membro atualizado, None se não existir neste workspace,
    ou False se o membro não pertence ao usuário."""
    payload = json.dumps(extra_busy)
    now = datetime.now(timezone.utc).isoformat()
    stmt = text(
        "UPDATE members SET extra_busy = :eb, updated_at = :now "
        "WHERE id = :mid AND workspace_id = :wid AND user_id = :uid "
        "RETURNING id, workspace_id, name, course, color, schedule, "
        "extra_busy, user_id, created_at, updated_at"
    )
    with get_engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        row = conn.execute(
            stmt,
            {"eb": payload, "now": now, "mid": member_id, "wid": workspace_id, "uid": owner_user_id},
        ).first()
    if row is None:
        m = get_member(member_id)
        if m is None or m.workspace_id != workspace_id:
            return None
        return False
    busy = json.loads(row.schedule)
    extra = json.loads(row.extra_busy)
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "name": row.name,
        "course": row.course,
        "color": row.color,
        "busy": busy,
        "extra_busy": extra,
        "total": len(busy) + len(extra),
        "user_id": row.user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


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
            sess.delete(m)
            sess.commit()
            return True
        return False
