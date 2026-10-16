import os, re, time, hashlib, hmac, secrets, urllib.parse, unicodedata, csv, io, logging, threading
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

load_dotenv()

logger = logging.getLogger("notimetorelax")

from database import (
    init_db, create_workspace, get_workspace, get_members, get_member,
    add_member, remove_member, delete_workspace, claim_workspace,
    check_password, get_or_create_user, get_user,
    update_extra_busy, update_own_extra_busy, update_own_busy,
    get_teams, get_team, create_team, update_team, delete_team,
    get_team_members, get_workspace_team_members, add_team_member,
    remove_team_member, set_team_leader, is_team_leader, get_leading_team_ids,
    get_schedule_entries, get_schedule_entries_por_equipe, get_schedule_entry, add_schedule_entry,
    add_schedule_entries_bulk, update_schedule_entry, delete_schedule_entry,
    delete_schedule_entries_bulk, SCHEDULE_STATUSES,
    carregar_snapshot_workspace,
)
from extractor import extrair_de_pdf_bytes, title_case, SLOTS

ALL_DAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

STATUS_LABELS = {
    "planejado": "Planejado",
    "em_andamento": "Em andamento",
    "concluido": "Concluído",
}

SESSION_TTL = 86400 * 30

DEBUG = os.environ.get("DEBUG", "").strip().lower() in {"1", "true", "yes"}

_cfg_secret = os.environ.get("SESSION_SECRET", "").strip()
if _cfg_secret:
    SESSION_SECRET = _cfg_secret
elif DEBUG:
    SESSION_SECRET = secrets.token_hex(32)
else:
    raise RuntimeError(
        "SESSION_SECRET não configurada. Defina a variável de ambiente "
        "SESSION_SECRET (ou rode com DEBUG=1 apenas em desenvolvimento)."
    )

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

_db_ready = False


def _ensure_db():
    global _db_ready
    if not _db_ready:
        # Verifica se a tabela workspaces já existe antes de rodar init_db
        from sqlalchemy import inspect as sa_inspect
        from database import get_engine
        engine = get_engine()
        inspector = sa_inspect(engine)
        if "workspaces" not in inspector.get_table_names():
            init_db()
        _db_ready = True


def _hmac_sig(payload: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


# ── Rate limiting simples em memória (sem dependência externa) ──────────────
_RL: dict[str, list[float]] = {}
_RL_LOCK = threading.Lock()


def _rate_limit(key: str, limit: int, window: float) -> bool:
    """True se a requisição passa; False se estourou o limite de tentativas."""
    now = time.time()
    with _RL_LOCK:
        hits = _RL.setdefault(key, [])
        hits[:] = [t for t in hits if now - t < window]
        if len(hits) >= limit:
            return False
        hits.append(now)
        if len(_RL) > 10_000:
            for k in [k for k, v in list(_RL.items()) if not v or v[-1] < now - 600]:
                del _RL[k]
        return True


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _is_same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    o = urllib.parse.urlparse(origin)
    b = urllib.parse.urlparse(str(request.base_url))
    return (o.scheme, o.netloc) == (b.scheme, b.netloc)


def _csv_safe(texto: str) -> str:
    """Neutraliza fórmulas de planilha (CSV injection)."""
    if not texto:
        return texto
    return "'" + texto if texto[0] in "=+-@\t\r" else texto


def _make_session_token(slug: str) -> str:
    payload = f"{slug}:{int(time.time()) + SESSION_TTL}"
    sig = _hmac_sig(payload)
    return f"{payload}.{sig}"


def _check_session(request: Request, slug: str) -> bool:
    cookie = request.cookies.get(f"ws_{slug}")
    if not cookie:
        return False
    try:
        payload, sig = cookie.rsplit(".", 1)
        expected = _hmac_sig(payload)
        if not hmac.compare_digest(sig, expected):
            return False
        data_slug, expiry = payload.rsplit(":", 1)
        return data_slug == slug and time.time() < float(expiry)
    except Exception:
        return False


def _require_auth(request: Request, ws) -> bool:
    return not ws.password_hash or _check_session(request, ws.slug)


def _make_user_session_token(user_id: int) -> str:
    payload = f"{user_id}:{int(time.time()) + SESSION_TTL}"
    sig = _hmac_sig(payload)
    return f"{payload}.{sig}"


def _get_current_user(request: Request):
    cookie = request.cookies.get("user_session")
    if not cookie:
        return None
    try:
        payload, sig = cookie.rsplit(".", 1)
        expected = _hmac_sig(payload)
        if not hmac.compare_digest(sig, expected):
            return None
        user_id, expiry = payload.rsplit(":", 1)
        if time.time() >= float(expiry):
            return None
        return get_user(int(user_id))
    except Exception:
        return None


def _require_google(request: Request) -> bool:
    if not GOOGLE_CLIENT_ID:
        return True
    return _current_user_id(request) is not None


def _current_user_id(request: Request):
    """Valida o cookie de sessão sem consultar o banco (identidade assinada por HMAC)."""
    cookie = request.cookies.get("user_session")
    if not cookie:
        return None
    try:
        payload, sig = cookie.rsplit(".", 1)
        expected = _hmac_sig(payload)
        if not hmac.compare_digest(sig, expected):
            return None
        user_id, expiry = payload.rsplit(":", 1)
        if time.time() >= float(expiry):
            return None
        return int(user_id)
    except Exception:
        return None


def _google_redirect_uri(request: Request) -> str:
    if GOOGLE_REDIRECT_URI:
        return GOOGLE_REDIRECT_URI
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/google/callback"


def _redirect_url(path: str, erro: str = "") -> str:
    if not erro:
        return path
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}erro={erro}"


# ── Equipes e escala: helpers de serialização e permissão ────────────────────

_VALID_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _teams_payload(ws, snapshot=None) -> list:
    """Equipes do workspace com seus integrantes (e marcação de líder)."""
    if snapshot is None:
        members_dict, team_members_list, teams_list, _ = carregar_snapshot_workspace(ws.id)
    else:
        members_dict, team_members_list, teams_list, _ = snapshot
    by_team: dict[int, list] = {}
    for tm in team_members_list:
        if tm.member_id in members_dict:
            by_team.setdefault(tm.team_id, []).append(tm)
    out = []
    for t in teams_list:
        tms = []
        for tm in by_team.get(t.id, []):
            m = members_dict[tm.member_id]
            tms.append({
                "member_id": m.id,
                "name": m.name,
                "color": m.color,
                "user_id": m.user_id,
                "is_leader": bool(tm.is_leader),
            })
        tms.sort(key=lambda x: (not x["is_leader"], x["name"].lower()))
        d = t.to_dict()
        d["members"] = tms
        out.append(d)
    return out


def _schedule_payload(ws, snapshot=None, busy=None) -> list:
    """Entradas da escala com os conflitos (integrantes ocupados) de cada uma."""
    if snapshot is None:
        members_dict, team_members_list, _, schedule_entries_list = carregar_snapshot_workspace(ws.id)
    else:
        members_dict, team_members_list, _, schedule_entries_list = snapshot
    if busy is None:
        busy = _busy_por_membro(members_dict)
    team_members: dict[int, list] = {}
    for tm in team_members_list:
        team_members.setdefault(tm.team_id, []).append(tm.member_id)
    out = []
    for e in schedule_entries_list:
        conflitos = []
        for mid in team_members.get(e.team_id, []):
            m = members_dict.get(mid)
            if m and (e.day, e.slot) in busy.get(mid, set()):
                conflitos.append({"id": m.id, "name": m.name})
        d = e.to_dict()
        d["conflicts"] = conflitos
        out.append(d)
    return out


def _can_manage_team(ws, team, user_id) -> bool:
    if not user_id or team is None or team.workspace_id != ws.id:
        return False
    if ws.owner_id == user_id:
        return True
    return is_team_leader(team.id, user_id, ws.id)


def _coerce_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_authed_ws(request: Request, slug: str):
    """Retorna (workspace, None) quando autenticado, ou (None, JSONResponse) de erro."""
    ws = get_workspace(slug)
    if not ws:
        return None, JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return None, JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return None, JSONResponse({"erro": "Acesso negado."}, status_code=403)
    return ws, None


def _busy_por_membro(members_dict) -> dict:
    """Calcula o conjunto de horários ocupados (busy) de cada membro uma única vez.
    Retorna {member_id: set((dia, slot))}."""
    return {
        mid: {(b[0], b[1]) for b in m.get_all_busy()}
        for mid, m in members_dict.items()
    }


def _conflicts_for_cells(ws, team_id: int, cells, busy=None) -> dict:
    """Conflitos de VÁRIAS células de uma equipe em uma só passada ao banco.

    Busca apenas os integrantes da equipe (filtro por team_id) e avalia todas as
    células em memória. Retorna {(dia, slot): [{"id", "name"}, ...]}.
    """
    cells = list(cells)
    if not cells:
        return {}
    team_members = get_team_members(team_id)
    member_ids = {tm.member_id for tm in team_members}
    # Busca apenas os membros que são integrantes da equipe
    members = {m.id: m for m in get_members(ws.id) if m.id in member_ids}
    if busy is None:
        busy = _busy_por_membro(members)
    out: dict = {}
    for (d, s) in cells:
        key = (d, s)
        conflitos = []
        for tm in team_members:
            m = members.get(tm.member_id)
            if m and key in busy.get(tm.member_id, set()):
                conflitos.append({"id": m.id, "name": m.name})
        out[key] = conflitos
    return out


def _conflicts_for(ws, team_id: int, day: int, slot: int) -> list:
    """Integrantes da equipe ocupados (aula/outro) no dia/slot informado."""
    return _conflicts_for_cells(ws, team_id, [(day, slot)]).get((day, slot), [])


def _normalizar_slots(raw) -> list | None:
    """Valida uma lista de pares [dia, slot] (0..6 / 0..13), descartando
    entradas inválidas. Retorna None se o valor não for uma lista."""
    if not isinstance(raw, list):
        return None
    return [
        [item[0], item[1]]
        for item in raw
        if isinstance(item, list) and len(item) == 2
        and isinstance(item[0], int) and isinstance(item[1], int)
        and not isinstance(item[0], bool) and not isinstance(item[1], bool)
        and 0 <= item[0] <= 6 and 0 <= item[1] <= 13
    ]


app = FastAPI(title="NoTimeToRelax")


_ERRO_GENERICO_PAGE = (
    "<!doctype html><html lang='pt-BR'><meta charset='utf-8'>"
    "<title>NoTimeToRelax</title>"
    "<body style='font-family:sans-serif;max-width:640px;margin:60px auto;padding:0 20px'>"
    "<h2>Erro interno</h2>"
    "<p>Algo deu errado. Tente novamente em instantes.</p>"
    "</body></html>"
)


@app.middleware("http")
async def ensure_db(request: Request, call_next):
    try:
        _ensure_db()
    except Exception:
        logger.exception("Falha ao inicializar o banco de dados")
        if request.url.path.startswith("/api"):
            return JSONResponse({"erro": "Banco de dados indisponível."}, status_code=500)
        return HTMLResponse(_ERRO_GENERICO_PAGE, status_code=500)
    if (
        request.method in ("POST", "PATCH", "DELETE", "PUT")
        and request.url.path.startswith("/api")
        and not _is_same_origin(request)
    ):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Erro interno na aplicação")
        if request.url.path.startswith("/api"):
            return JSONResponse({"erro": "Erro interno."}, status_code=500)
        return HTMLResponse(_ERRO_GENERICO_PAGE, status_code=500)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https:; connect-src 'self'",
    )
    if not DEBUG:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ─── Frontend ────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request, erro: str = ""):
    user = _get_current_user(request)
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "user": user.to_dict() if user else None,
            "google_login": bool(GOOGLE_CLIENT_ID),
            "erro": erro,
        },
    )


# ─── Google OAuth ────────────────────────────────────────────────────────────


@app.get("/auth/google/login")
def google_login(request: Request, next: str = Query("/")):
    if not GOOGLE_CLIENT_ID:
        return RedirectResponse(url="/", status_code=302)
    next_path = next if next.startswith("/") and not next.startswith("//") else "/"
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(key="oauth_state", value=state, httponly=True, max_age=600, path="/", samesite="lax", secure=not DEBUG)
    resp.set_cookie(key="oauth_next", value=next_path, httponly=True, max_age=600, path="/", samesite="lax", secure=not DEBUG)
    return resp


@app.get("/auth/google/callback")
def google_callback(request: Request):
    if request.query_params.get("error"):
        return RedirectResponse(url=_redirect_url("/", "login_cancelado"), status_code=302)

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    if not code or not state or not hmac.compare_digest(state, request.cookies.get("oauth_state", "")):
        return RedirectResponse(url=_redirect_url("/", "falha_na_autenticacao"), status_code=302)

    try:
        token_resp = httpx.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": _google_redirect_uri(request),
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        token_resp.raise_for_status()
        token = token_resp.json()
    except Exception:
        return RedirectResponse(url=_redirect_url("/", "falha_na_autenticacao"), status_code=302)

    access_token = token.get("access_token")
    if not access_token:
        return RedirectResponse(url=_redirect_url("/", "falha_na_autenticacao"), status_code=302)

    try:
        userinfo_resp = httpx.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        userinfo_resp.raise_for_status()
        info = userinfo_resp.json()
    except Exception:
        return RedirectResponse(url=_redirect_url("/", "falha_na_autenticacao"), status_code=302)

    sub = info.get("sub")
    if not sub:
        return RedirectResponse(url=_redirect_url("/", "perfil_invalido"), status_code=302)

    user = get_or_create_user(
        sub, info.get("email", ""), info.get("name", ""), info.get("picture", "")
    )

    next_path = request.cookies.get("oauth_next") or "/"
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"

    resp = RedirectResponse(url=next_path, status_code=302)
    resp.set_cookie(
        key="user_session", value=_make_user_session_token(user.id),
        httponly=True, max_age=SESSION_TTL, path="/", samesite="lax", secure=not DEBUG,
    )
    resp.delete_cookie(key="oauth_state", path="/")
    resp.delete_cookie(key="oauth_next", path="/")
    return resp


@app.get("/auth/logout")
def auth_logout():
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie(key="user_session", path="/")
    return resp


@app.get("/workspace/{slug}", response_class=HTMLResponse)
async def dashboard(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return templates.TemplateResponse(
            request=request, name="landing.html", context={"erro": "Workspace não encontrado."}
        )
    if not _require_google(request):
        return RedirectResponse(url=f"/auth/google/login?next={urllib.parse.quote(f'/workspace/{slug}')}", status_code=302)
    if not _require_auth(request, ws):
        return RedirectResponse(url=f"/workspace/{slug}/login", status_code=302)
    members = get_members(ws.id)
    user = _get_current_user(request)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "workspace": ws.to_dict(),
            "members": [m.to_dict() for m in members],
            "user": user.to_dict() if user else None,
            "slots": SLOTS,
        },
    )


@app.get("/workspace/{slug}/login", response_class=HTMLResponse)
async def workspace_login(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return templates.TemplateResponse(
            request=request, name="landing.html", context={"erro": "Workspace não encontrado."}
        )
    if not _require_google(request):
        return RedirectResponse(url=f"/auth/google/login?next={urllib.parse.quote(f'/workspace/{slug}/login')}", status_code=302)
    if not ws.password_hash:
        return RedirectResponse(url=f"/workspace/{slug}", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="workspace-login.html",
        context={"workspace": ws.to_dict()},
    )


@app.get("/workspace/{slug}/upload", response_class=HTMLResponse)
async def upload_page(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return templates.TemplateResponse(
            request=request, name="landing.html", context={"erro": "Workspace não encontrado."}
        )
    if not _require_google(request):
        return RedirectResponse(url=f"/auth/google/login?next={urllib.parse.quote(f'/workspace/{slug}/upload')}", status_code=302)
    if not _require_auth(request, ws):
        return RedirectResponse(url=f"/workspace/{slug}/login", status_code=302)
    user = _get_current_user(request)
    return templates.TemplateResponse(
        request=request, name="upload.html", context={
            "workspace": ws.to_dict(),
            "user_name": user.google_name if user else "",
        }
    )


@app.get("/workspace/{slug}/escala", response_class=HTMLResponse)
async def escala_page(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return templates.TemplateResponse(
            request=request, name="landing.html", context={"erro": "Workspace não encontrado."}
        )
    if not _require_google(request):
        return RedirectResponse(
            url=f"/auth/google/login?next={urllib.parse.quote(f'/workspace/{slug}/escala')}",
            status_code=302,
        )
    if not _require_auth(request, ws):
        return RedirectResponse(url=f"/workspace/{slug}/login", status_code=302)
    user = _get_current_user(request)
    uid = user.id if user else None
    snapshot = carregar_snapshot_workspace(ws.id)
    members_dict = snapshot[0]
    response = templates.TemplateResponse(
        request=request,
        name="escala.html",
        context={
            "workspace": ws.to_dict(),
            "members": [m.to_dict() for m in members_dict.values()],
            "teams": _teams_payload(ws, snapshot),
            "schedule": _schedule_payload(ws, snapshot),
            "status_labels": STATUS_LABELS,
            "user": user.to_dict() if user else None,
            "is_owner": bool(uid and ws.owner_id == uid),
            "leading_team_ids": sorted(get_leading_team_ids(ws.id, uid)) if uid else [],
            "slots": SLOTS,
        },
    )
    # Evita que o navegador sirva uma versão antiga da página (JS/CSS) após
    # atualizações; a escala depende de JS e o cache causava botões "mortos".
    response.headers["Cache-Control"] = "no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


# ─── API ─────────────────────────────────────────────────────────────────────


@app.post("/api/workspace")
async def api_create_workspace(request: Request, name: str = Form(...), password: str = Form("")):
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not name or not name.strip():
        return JSONResponse({"erro": "Nome é obrigatório."}, status_code=400)
    pw = password.strip() if password else ""
    ws = create_workspace(name.strip(), pw if pw else None, owner_id=_current_user_id(request))
    resp = JSONResponse(ws.to_dict())
    if pw:
        token = _make_session_token(ws.slug)
        resp.set_cookie(
            key=f"ws_{ws.slug}", value=token,
            httponly=True, max_age=SESSION_TTL, path="/", samesite="lax", secure=not DEBUG,
        )
    return resp


@app.get("/api/workspace/{slug}/exists")
async def api_workspace_exists(slug: str, request: Request):
    if not _rate_limit(f"exists:{_client_ip(request)}", limit=60, window=60):
        return JSONResponse({"erro": "Muitas requisições."}, status_code=429)
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"exists": False})
    return JSONResponse({
        "exists": True,
        "slug": ws.slug,
        "name": ws.name,
        "has_password": bool(ws.password_hash),
    })


@app.get("/api/workspace/{slug}/members")
async def api_get_members(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    members = get_members(ws.id)
    return JSONResponse([m.to_dict() for m in members])


@app.get("/api/workspace/{slug}/export.csv")
async def api_export_csv(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)

    membros = [m.to_dict() for m in get_members(ws.id)]
    tem_fim_de_semana = any(
        any(b[0] in (5, 6) for b in (mb["busy"] + mb["extra_busy"]))
        for mb in membros
    )
    dias = ALL_DAYS if tem_fim_de_semana else ALL_DAYS[:5]

    def _livre(mb: dict, d: int, s: int) -> bool:
        return not any(b[0] == d and b[1] == s for b in (mb["busy"] + mb["extra_busy"]))

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(["Horário"] + dias)
    for s, slot in enumerate(SLOTS):
        linha = [slot]
        for d in range(len(dias)):
            livres = [_csv_safe(mb["name"]) for mb in membros if _livre(mb, d, s)]
            linha.append(f"{len(livres)} livres: " + ", ".join(livres) if livres else "0 livres")
        writer.writerow(linha)

    conteudo = "\ufeff" + out.getvalue()
    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="disponibilidade-{slug}.csv"'
        },
    )


@app.post("/api/workspace/{slug}/upload")
async def api_upload(
    request: Request,
    slug: str,
    file: UploadFile = File(...),
    force: bool = Query(False),
    preview: bool = Query(False),
):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"erro": "Apenas arquivos PDF são aceitos."}, status_code=400)

    if file.size and file.size > 10 * 1024 * 1024:
        return JSONResponse({"erro": "Arquivo muito grande. Máximo 10 MB."}, status_code=400)

    try:
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            return JSONResponse({"erro": "Arquivo muito grande. Máximo 10 MB."}, status_code=400)

        dados = extrair_de_pdf_bytes(content)

        if dados is None:
            return JSONResponse(
                {"erro": "Não foi possível extrair dados deste PDF. Pode ser um documento escaneado (sem texto) ou inválido."},
                status_code=400,
            )

        if preview:
            return JSONResponse(dados)

        user = _get_current_user(request)
        member, erro = add_member(ws.id, dados["nome"], dados["curso"], dados["busy"], force=force, user_id=(user.id if user else None))

        if erro:
            return JSONResponse(
                {"erro": erro, "preview": dados, "nome_existente": True}, status_code=409
            )

        return JSONResponse(member.to_dict())

    except Exception:
        logger.exception("Falha no upload do PDF")
        return JSONResponse({"erro": "Erro interno ao processar o arquivo."}, status_code=500)


@app.post("/api/workspace/{slug}/auth")
async def api_auth(slug: str, request: Request):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not ws.password_hash:
        return JSONResponse({"erro": "Workspace não possui senha."}, status_code=400)
    if not _rate_limit(f"auth:{slug}", limit=10, window=60):
        return JSONResponse({"erro": "Muitas tentativas. Tente novamente em instantes."}, status_code=429)
    body = await request.json()
    password = body.get("password", "")
    if not check_password(password, ws.password_hash):
        return JSONResponse({"erro": "Senha incorreta."}, status_code=401)
    token = _make_session_token(ws.slug)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=f"ws_{ws.slug}", value=token,
        httponly=True, max_age=SESSION_TTL, path="/", samesite="lax", secure=not DEBUG,
    )
    return resp


@app.post("/api/workspace/{slug}/members")
async def api_add_member(slug: str, request: Request):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    body = await request.json()
    nome_raw = body.get("nome", "")
    curso = body.get("curso", "")
    busy = body.get("busy", [])
    force = body.get("force", False)
    if not isinstance(nome_raw, str) or not nome_raw.strip():
        return JSONResponse({"erro": "Nome é obrigatório."}, status_code=400)
    if not isinstance(curso, str):
        curso = ""
    if not isinstance(busy, list):
        return JSONResponse({"erro": "busy deve ser uma lista de horários."}, status_code=400)
    for item in busy:
        if (
            not isinstance(item, list) or len(item) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in item)
            or not (0 <= item[0] <= 6 and 0 <= item[1] <= 13)
        ):
            return JSONResponse({"erro": "busy contém horários inválidos."}, status_code=400)
    nome_norm = title_case(unicodedata.normalize("NFC", nome_raw.lower().strip()))
    user = _get_current_user(request)
    member, erro = add_member(ws.id, nome_norm, curso, busy, force=force, user_id=(user.id if user else None))
    if erro:
        return JSONResponse({"erro": erro, "nome_existente": True}, status_code=409)
    return JSONResponse(member.to_dict())


@app.patch("/api/workspace/{slug}/members/{member_id}/extra-busy")
async def api_update_extra_busy(slug: str, member_id: int, request: Request):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    body = await request.json()
    extra_busy = body.get("extra_busy")
    normalizados = _normalizar_slots(extra_busy)
    if normalizados is None:
        return JSONResponse({"erro": "extra_busy deve ser uma lista."}, status_code=400)
    user_id = _current_user_id(request)
    if not user_id:
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    res = update_own_extra_busy(member_id, normalizados, workspace_id=ws.id, owner_user_id=user_id)
    if res is None:
        return JSONResponse({"erro": "Membro não encontrado."}, status_code=404)
    if res is False:
        return JSONResponse({"erro": "Você não pode editar os horários deste membro."}, status_code=403)
    return JSONResponse(res)


@app.patch("/api/workspace/{slug}/members/{member_id}/busy")
async def api_update_busy(slug: str, member_id: int, request: Request):
    """Edita os horários do próprio membro: aulas apagadas (removed_busy) e
    compromissos extras (extra_busy). Ambos os campos são opcionais; quando
    omitidos, o valor atual é mantido."""
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    body = await request.json()
    if "extra_busy" not in body and "removed_busy" not in body:
        return JSONResponse(
            {"erro": "Informe extra_busy e/ou removed_busy."}, status_code=400
        )
    extra_busy = removed_busy = None
    if "extra_busy" in body:
        extra_busy = _normalizar_slots(body.get("extra_busy"))
        if extra_busy is None:
            return JSONResponse({"erro": "extra_busy deve ser uma lista."}, status_code=400)
    if "removed_busy" in body:
        removed_busy = _normalizar_slots(body.get("removed_busy"))
        if removed_busy is None:
            return JSONResponse({"erro": "removed_busy deve ser uma lista."}, status_code=400)
    user_id = _current_user_id(request)
    if not user_id:
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    res = update_own_busy(
        member_id,
        removed_busy,
        extra_busy,
        workspace_id=ws.id,
        owner_user_id=user_id,
    )
    if res is None:
        return JSONResponse({"erro": "Membro não encontrado."}, status_code=404)
    if res is False:
        return JSONResponse({"erro": "Você não pode editar os horários deste membro."}, status_code=403)
    return JSONResponse(res)


@app.delete("/api/workspace/{slug}/members/{member_id}")
async def api_remove_member(request: Request, slug: str, member_id: int):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    ok = remove_member(member_id, ws.id)
    if not ok:
        return JSONResponse({"erro": "Membro não encontrado."}, status_code=404)
    return JSONResponse({"ok": True})


# ─── API: Equipes e escala de trabalho ───────────────────────────────────────


@app.get("/api/workspace/{slug}/teams")
async def api_get_teams(request: Request, slug: str):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    return JSONResponse(_teams_payload(ws))


@app.post("/api/workspace/{slug}/teams")
async def api_create_team(request: Request, slug: str):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    if ws.owner_id is None or ws.owner_id != user_id:
        return JSONResponse(
            {"erro": "Apenas o criador do workspace pode criar equipes."}, status_code=403
        )
    body = await request.json()
    name = body.get("name", "")
    if not isinstance(name, str) or not name.strip():
        return JSONResponse({"erro": "Nome da equipe é obrigatório."}, status_code=400)
    color = body.get("color")
    if not (isinstance(color, str) and _VALID_COLOR.match(color)):
        color = None
    team = create_team(ws.id, name.strip(), color)
    payload = next((t for t in _teams_payload(ws) if t["id"] == team.id), team.to_dict())
    return JSONResponse(payload)


@app.patch("/api/workspace/{slug}/teams/{team_id}")
async def api_update_team(request: Request, slug: str, team_id: int):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    if ws.owner_id is None or ws.owner_id != user_id:
        return JSONResponse(
            {"erro": "Apenas o criador do workspace pode editar equipes."}, status_code=403
        )
    team = get_team(team_id)
    if not team or team.workspace_id != ws.id:
        return JSONResponse({"erro": "Equipe não encontrada."}, status_code=404)
    body = await request.json()
    name = body.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        return JSONResponse({"erro": "Nome inválido."}, status_code=400)
    color = body.get("color")
    if not (isinstance(color, str) and _VALID_COLOR.match(color)):
        color = None
    update_team(team_id, ws.id, name=name if isinstance(name, str) else None, color=color)
    payload = next((t for t in _teams_payload(ws) if t["id"] == team_id), None)
    return JSONResponse(payload)


@app.delete("/api/workspace/{slug}/teams/{team_id}")
async def api_delete_team(request: Request, slug: str, team_id: int):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    if ws.owner_id is None or ws.owner_id != user_id:
        return JSONResponse(
            {"erro": "Apenas o criador do workspace pode apagar equipes."}, status_code=403
        )
    if not delete_team(team_id, ws.id):
        return JSONResponse({"erro": "Equipe não encontrada."}, status_code=404)
    return JSONResponse({"ok": True})


@app.post("/api/workspace/{slug}/teams/{team_id}/members")
async def api_add_team_member(request: Request, slug: str, team_id: int):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    team = get_team(team_id)
    if not team or team.workspace_id != ws.id:
        return JSONResponse({"erro": "Equipe não encontrada."}, status_code=404)
    if not _can_manage_team(ws, team, user_id):
        return JSONResponse({"erro": "Você não pode editar esta equipe."}, status_code=403)
    body = await request.json()
    member_id = _coerce_int(body.get("member_id"))
    if member_id is None:
        return JSONResponse({"erro": "Membro inválido."}, status_code=400)
    member = get_member(member_id)
    if not member or member.workspace_id != ws.id:
        return JSONResponse({"erro": "Membro não encontrado."}, status_code=404)
    is_leader = bool(body.get("is_leader", False))
    if is_leader and ws.owner_id != user_id:
        is_leader = False
    add_team_member(team_id, member_id, is_leader=is_leader)
    payload = next((t for t in _teams_payload(ws) if t["id"] == team_id), None)
    return JSONResponse(payload)


@app.delete("/api/workspace/{slug}/teams/{team_id}/members/{member_id}")
async def api_remove_team_member(request: Request, slug: str, team_id: int, member_id: int):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    team = get_team(team_id)
    if not team or team.workspace_id != ws.id:
        return JSONResponse({"erro": "Equipe não encontrada."}, status_code=404)
    if not _can_manage_team(ws, team, user_id):
        return JSONResponse({"erro": "Você não pode editar esta equipe."}, status_code=403)
    if not remove_team_member(team_id, member_id):
        return JSONResponse({"erro": "Integrante não encontrado."}, status_code=404)
    return JSONResponse({"ok": True})


@app.patch("/api/workspace/{slug}/teams/{team_id}/leaders/{member_id}")
async def api_set_team_leader(request: Request, slug: str, team_id: int, member_id: int):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    if ws.owner_id is None or ws.owner_id != user_id:
        return JSONResponse(
            {"erro": "Apenas o criador do workspace pode definir líderes."}, status_code=403
        )
    team = get_team(team_id)
    if not team or team.workspace_id != ws.id:
        return JSONResponse({"erro": "Equipe não encontrada."}, status_code=404)
    body = await request.json()
    tm = set_team_leader(team_id, member_id, bool(body.get("is_leader", True)))
    if tm is None:
        return JSONResponse({"erro": "Integrante não encontrado na equipe."}, status_code=404)
    payload = next((t for t in _teams_payload(ws) if t["id"] == team_id), None)
    return JSONResponse(payload)


@app.get("/api/workspace/{slug}/schedule")
async def api_get_schedule(request: Request, slug: str):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    return JSONResponse(_schedule_payload(ws))


@app.post("/api/workspace/{slug}/schedule")
async def api_add_schedule_entry(request: Request, slug: str):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    body = await request.json()
    team_id = _coerce_int(body.get("team_id"))
    team = get_team(team_id) if team_id is not None else None
    if not team or team.workspace_id != ws.id:
        return JSONResponse({"erro": "Equipe não encontrada."}, status_code=404)
    if not _can_manage_team(ws, team, user_id):
        return JSONResponse({"erro": "Você não pode editar esta equipe."}, status_code=403)
    day = _coerce_int(body.get("day"))
    slot = _coerce_int(body.get("slot"))
    if day is None or not (0 <= day <= 6) or slot is None or not (0 <= slot <= len(SLOTS) - 1):
        return JSONResponse({"erro": "Dia ou horário inválido."}, status_code=400)
    status = body.get("status", "planejado")
    if status not in SCHEDULE_STATUSES:
        return JSONResponse({"erro": "Status inválido."}, status_code=400)
    task = body.get("task", "")
    if not isinstance(task, str):
        task = ""
    conflicts = _conflicts_for(ws, team_id, day, slot)
    if conflicts and not bool(body.get("force")):
        return JSONResponse(
            {"erro": "Há integrantes ocupados nesse horário.", "conflicts": conflicts, "needs_confirm": True},
            status_code=409,
        )
    entry = add_schedule_entry(
        ws.id, team_id, day, slot, task=task, status=status, created_by_user_id=user_id
    )
    d = entry.to_dict()
    d["conflicts"] = conflicts
    return JSONResponse(d)


@app.post("/api/workspace/{slug}/schedule/bulk")
async def api_add_schedule_bulk(request: Request, slug: str):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    body = await request.json()
    team_id = _coerce_int(body.get("team_id"))
    team = get_team(team_id) if team_id is not None else None
    if not team or team.workspace_id != ws.id:
        return JSONResponse({"erro": "Equipe não encontrada."}, status_code=404)
    if not _can_manage_team(ws, team, user_id):
        return JSONResponse({"erro": "Você não pode editar esta equipe."}, status_code=403)

    status = body.get("status", "planejado")
    if status not in SCHEDULE_STATUSES:
        return JSONResponse({"erro": "Status inválido."}, status_code=400)
    task = body.get("task", "")
    if not isinstance(task, str):
        task = ""

    cells_raw = body.get("cells")
    if not isinstance(cells_raw, list) or not cells_raw:
        return JSONResponse({"erro": "Nenhum horário selecionado."}, status_code=400)
    cells, seen = [], set()
    for c in cells_raw:
        if not isinstance(c, list) or len(c) != 2:
            continue
        d, s = _coerce_int(c[0]), _coerce_int(c[1])
        if d is None or s is None or not (0 <= d <= 6 and 0 <= s <= len(SLOTS) - 1):
            continue
        if (d, s) in seen:
            continue
        seen.add((d, s))
        cells.append((d, s))
    if not cells:
        return JSONResponse({"erro": "Horários inválidos."}, status_code=400)

    existing = {
        (e.day, e.slot) for e in get_schedule_entries_por_equipe(ws.id, team_id)
    }
    new_cells = sorted(c for c in cells if c not in existing)

    # Calcula busy uma vez e reutiliza em _conflicts_for_cells
    members_dict = {m.id: m for m in get_members(ws.id)}
    busy = _busy_por_membro(members_dict)

    # Conflitos de todas as células em uma só passada ao banco.
    conflict_map = _conflicts_for_cells(ws, team_id, new_cells, busy=busy)
    conflicts_map: dict[int, dict] = {}
    for conflitos in conflict_map.values():
        for c in conflitos:
            conflicts_map[c["id"]] = c
    conflicts = list(conflicts_map.values())
    if conflicts and not bool(body.get("force")):
        return JSONResponse(
            {
                "erro": "Há integrantes ocupados em horários selecionados.",
                "conflicts": conflicts,
                "needs_confirm": True,
                "cells": len(new_cells),
            },
            status_code=409,
        )

    # INSERT em lote: uma ida ao banco para todas as células.
    created = add_schedule_entries_bulk(
        ws.id, team_id, new_cells, task=task, status=status, created_by_user_id=user_id
    )
    for entry in created:
        entry["conflicts"] = conflict_map.get((entry["day"], entry["slot"]), [])
    return JSONResponse(
        {
            "created": len(created),
            "skipped": len(cells) - len(created),
            "conflicts": conflicts,
            "entries": created,
        }
    )


@app.patch("/api/workspace/{slug}/schedule/{entry_id}")
async def api_update_schedule_entry(request: Request, slug: str, entry_id: int):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    entry = get_schedule_entry(entry_id)
    if not entry or entry.workspace_id != ws.id:
        return JSONResponse({"erro": "Entrada não encontrada."}, status_code=404)
    team = get_team(entry.team_id)
    if not _can_manage_team(ws, team, user_id):
        return JSONResponse({"erro": "Você não pode editar esta equipe."}, status_code=403)
    body = await request.json()

    team_id = entry.team_id
    if body.get("team_id") is not None:
        ntid = _coerce_int(body.get("team_id"))
        nteam = get_team(ntid) if ntid is not None else None
        if not nteam or nteam.workspace_id != ws.id:
            return JSONResponse({"erro": "Equipe não encontrada."}, status_code=404)
        if not _can_manage_team(ws, nteam, user_id):
            return JSONResponse({"erro": "Você não pode editar esta equipe."}, status_code=403)
        team_id = ntid

    day = entry.day
    if body.get("day") is not None:
        day = _coerce_int(body.get("day"))
        if day is None or not (0 <= day <= 6):
            return JSONResponse({"erro": "Dia inválido."}, status_code=400)
    slot = entry.slot
    if body.get("slot") is not None:
        slot = _coerce_int(body.get("slot"))
        if slot is None or not (0 <= slot <= len(SLOTS) - 1):
            return JSONResponse({"erro": "Horário inválido."}, status_code=400)

    status = entry.status
    if body.get("status") is not None:
        if body.get("status") not in SCHEDULE_STATUSES:
            return JSONResponse({"erro": "Status inválido."}, status_code=400)
        status = body.get("status")

    task = body.get("task") if isinstance(body.get("task"), str) else None

    conflicts = _conflicts_for(ws, team_id, day, slot)
    if conflicts and not bool(body.get("force")):
        return JSONResponse(
            {"erro": "Há integrantes ocupados nesse horário.", "conflicts": conflicts, "needs_confirm": True},
            status_code=409,
        )
    e = update_schedule_entry(
        entry_id, ws.id, task=task, status=status, team_id=team_id, day=day, slot=slot
    )
    d = e.to_dict()
    d["conflicts"] = conflicts
    return JSONResponse(d)


@app.delete("/api/workspace/{slug}/schedule/{entry_id}")
async def api_delete_schedule_entry(request: Request, slug: str, entry_id: int):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    entry = get_schedule_entry(entry_id)
    if not entry or entry.workspace_id != ws.id:
        return JSONResponse({"erro": "Entrada não encontrada."}, status_code=404)
    team = get_team(entry.team_id)
    if not _can_manage_team(ws, team, user_id):
        return JSONResponse({"erro": "Você não pode editar esta equipe."}, status_code=403)
    delete_schedule_entry(entry_id, ws.id)
    return JSONResponse({"ok": True})


@app.post("/api/workspace/{slug}/schedule/bulk-delete")
async def api_delete_schedule_bulk(request: Request, slug: str):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    user_id = _current_user_id(request)
    body = await request.json()
    ids_raw = body.get("ids")
    if not isinstance(ids_raw, list) or not ids_raw:
        return JSONResponse({"erro": "Nenhuma entrada selecionada."}, status_code=400)

    entries = {e.id: e for e in get_schedule_entries(ws.id)}
    # Permissão resolvida uma vez: dono pode tudo; líder só as equipes que lidera.
    if user_id and ws.owner_id == user_id:
        manageable = None  # None => qualquer equipe do workspace
    else:
        manageable = set(get_leading_team_ids(ws.id, user_id)) if user_id else set()

    to_delete, seen, skipped = [], set(), 0
    for raw in ids_raw:
        entry_id = _coerce_int(raw)
        if entry_id is None or entry_id in seen:
            skipped += 1
            continue
        seen.add(entry_id)
        entry = entries.get(entry_id)
        if not entry or (manageable is not None and entry.team_id not in manageable):
            skipped += 1
            continue
        to_delete.append(entry_id)

    # DELETE em lote: uma query para apagar todas as entradas.
    deleted = delete_schedule_entries_bulk(to_delete, ws.id)
    skipped += len(to_delete) - deleted
    return JSONResponse({"deleted": deleted, "skipped": skipped})


@app.get("/api/workspace/{slug}/schedule.csv")
async def api_export_schedule_csv(request: Request, slug: str):
    ws, err = _load_authed_ws(request, slug)
    if err:
        return err
    teams = {t.id: t for t in get_teams(ws.id)}
    entries = get_schedule_entries(ws.id)
    grid: dict[tuple, list] = {}
    for e in entries:
        grid.setdefault((e.slot, e.day), []).append(e)
    tem_fim_de_semana = any(e.day in (5, 6) for e in entries)
    dias = ALL_DAYS if tem_fim_de_semana else ALL_DAYS[:5]

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(["Horário"] + dias)
    for s, slot in enumerate(SLOTS):
        linha = [slot]
        for d in range(len(dias)):
            partes = []
            for e in grid.get((s, d), []):
                t = teams.get(e.team_id)
                nome = t.name if t else "?"
                txt = nome
                if e.task:
                    txt += f" — {e.task}"
                txt += f" [{STATUS_LABELS.get(e.status, e.status)}]"
                partes.append(_csv_safe(txt))
            linha.append(" | ".join(partes))
        writer.writerow(linha)

    conteudo = "\ufeff" + out.getvalue()
    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="escala-{slug}.csv"'
        },
    )


@app.delete("/api/workspace/{slug}")
async def api_delete_workspace(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    user_id = _current_user_id(request)
    if ws.owner_id is None:
        return JSONResponse(
            {"erro": "Este workspace não tem criador vinculado."}, status_code=403
        )
    if ws.owner_id != user_id:
        return JSONResponse(
            {"erro": "Apenas o criador do workspace pode deletá-lo."}, status_code=403
        )
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    delete_workspace(slug)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(f"ws_{slug}")
    return resp


@app.post("/api/workspace/{slug}/claim")
async def api_claim_workspace(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    ws = claim_workspace(slug, _current_user_id(request))
    if ws is None:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if ws is False:
        return JSONResponse(
            {"erro": "Este workspace já tem um criador."}, status_code=409
        )
    return JSONResponse(ws.to_dict())
