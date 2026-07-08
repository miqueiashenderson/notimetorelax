import json, os, re, hashlib, secrets
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from datetime import datetime


def get_engine():
    url = os.environ.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return create_engine(url, pool_pre_ping=True, poolclass=NullPool, echo=False)
    db_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(db_dir, "data", "horariolivre.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return create_engine(f"sqlite:///{db_path}", echo=False)


engine = get_engine()


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(unique=True, nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    password_hash: Mapped[str] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.utcnow().isoformat()
    )

    def to_dict(self):
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "has_password": bool(self.password_hash),
            "created_at": self.created_at,
        }


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    course: Mapped[str] = mapped_column(default="")
    schedule: Mapped[str] = mapped_column(default="[]")
    color: Mapped[str] = mapped_column(default="#3ddbd9")
    created_at: Mapped[str] = mapped_column(
        default=lambda: datetime.utcnow().isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        default=lambda: datetime.utcnow().isoformat()
    )

    def get_busy(self):
        return json.loads(self.schedule)

    def to_dict(self):
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "name": self.name,
            "course": self.course,
            "color": self.color,
            "busy": self.get_busy(),
            "total": len(self.get_busy()),
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
        return h == hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    except Exception:
        return False


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[-\s]+", "-", s)
    return s.strip("-")


def init_db():
    Base.metadata.create_all(engine)


def create_workspace(name: str, password: str | None = None):
    base_slug = slugify(name)
    slug = base_slug
    with Session(engine) as sess:
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
    with Session(engine) as sess:
        return sess.query(Workspace).filter_by(slug=slug).first()


def get_members(workspace_id: int):
    with Session(engine) as sess:
        return list(
            sess.query(Member).filter_by(workspace_id=workspace_id).all()
        )


def add_member(
    workspace_id: int, name: str, course: str, busy: list, force: bool = False
):
    with Session(engine) as sess:
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
            existing.updated_at = datetime.utcnow().isoformat()
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
        )
        sess.add(member)
        sess.commit()
        sess.refresh(member)
        return member, None


def remove_member(member_id: int):
    with Session(engine) as sess:
        m = sess.query(Member).filter_by(id=member_id).first()
        if m:
            sess.delete(m)
            sess.commit()
            return True
        return False
