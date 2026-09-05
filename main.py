import os, time, hashlib, hmac, secrets, urllib.parse, unicodedata, csv, io, logging, threading
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

load_dotenv()

logger = logging.getLogger("notimetorelax")

from database import (
    init_db, create_workspace, get_workspace, get_members, get_member,
    add_member, remove_member, check_password, get_or_create_user, get_user,
    update_extra_busy, update_own_extra_busy,
)
from extractor import extrair_de_pdf_bytes, title_case, SLOTS

ALL_DAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

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


# ─── API ─────────────────────────────────────────────────────────────────────


@app.post("/api/workspace")
async def api_create_workspace(request: Request, name: str = Form(...), password: str = Form("")):
    if not _require_google(request):
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    if not name or not name.strip():
        return JSONResponse({"erro": "Nome é obrigatório."}, status_code=400)
    pw = password.strip() if password else ""
    ws = create_workspace(name.strip(), pw if pw else None)
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

    except Exception as e:
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
    if not isinstance(extra_busy, list):
        return JSONResponse({"erro": "extra_busy deve ser uma lista."}, status_code=400)
    normalizados = [
        [item[0], item[1]]
        for item in extra_busy
        if isinstance(item, list) and len(item) == 2
        and isinstance(item[0], int) and isinstance(item[1], int)
        and not isinstance(item[0], bool) and not isinstance(item[1], bool)
        and 0 <= item[0] <= 6 and 0 <= item[1] <= 13
    ]
    user_id = _current_user_id(request)
    if not user_id:
        return JSONResponse({"erro": "Faça login com Google."}, status_code=401)
    res = update_own_extra_busy(member_id, normalizados, workspace_id=ws.id, owner_user_id=user_id)
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
