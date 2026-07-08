import os, time, hashlib, hmac, secrets
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from database import init_db, create_workspace, get_workspace, get_members, add_member, remove_member, check_password
from extractor import extrair_de_pdf_bytes

SESSION_TTL = 86400 * 30
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)

_db_ready = False


def _ensure_db():
    global _db_ready
    if not _db_ready:
        try:
            init_db()
        except Exception:
            pass
        _db_ready = True


def _make_session_token(slug: str) -> str:
    payload = f"{slug}:{int(time.time()) + SESSION_TTL}"
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}.{sig}"


def _check_session(request: Request, slug: str) -> bool:
    cookie = request.cookies.get(f"ws_{slug}")
    if not cookie:
        return False
    try:
        payload, sig = cookie.rsplit(".", 1)
        expected = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return False
        data_slug, expiry = payload.rsplit(":", 1)
        return data_slug == slug and time.time() < float(expiry)
    except Exception:
        return False


def _require_auth(request: Request, ws) -> bool:
    return not ws.password_hash or _check_session(request, ws.slug)


app = FastAPI(title="NoTimeToRelax")


@app.middleware("http")
async def ensure_db(request: Request, call_next):
    _ensure_db()
    return await call_next(request)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ─── Frontend ────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/workspace/{slug}", response_class=HTMLResponse)
async def dashboard(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return templates.TemplateResponse(
            "landing.html", {"request": request, "erro": "Workspace não encontrado."}
        )
    if not _require_auth(request, ws):
        return RedirectResponse(url=f"/workspace/{slug}/login", status_code=302)
    members = get_members(ws.id)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "workspace": ws.to_dict(),
            "members": [m.to_dict() for m in members],
        },
    )


@app.get("/workspace/{slug}/login", response_class=HTMLResponse)
async def workspace_login(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return templates.TemplateResponse(
            "landing.html", {"request": request, "erro": "Workspace não encontrado."}
        )
    if not ws.password_hash:
        return RedirectResponse(url=f"/workspace/{slug}", status_code=302)
    return templates.TemplateResponse(
        "workspace-login.html",
        {"request": request, "workspace": ws.to_dict()},
    )


@app.get("/workspace/{slug}/upload", response_class=HTMLResponse)
async def upload_page(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return templates.TemplateResponse(
            "landing.html", {"request": request, "erro": "Workspace não encontrado."}
        )
    if not _require_auth(request, ws):
        return RedirectResponse(url=f"/workspace/{slug}/login", status_code=302)
    return templates.TemplateResponse(
        "upload.html", {"request": request, "workspace": ws.to_dict()}
    )


# ─── API ─────────────────────────────────────────────────────────────────────


@app.post("/api/workspace")
async def api_create_workspace(name: str = Form(...), password: str = Form("")):
    if not name or not name.strip():
        return JSONResponse({"erro": "Nome é obrigatório."}, status_code=400)
    pw = password.strip() if password else ""
    ws = create_workspace(name.strip(), pw if pw else None)
    resp = JSONResponse(ws.to_dict())
    if pw:
        token = _make_session_token(ws.slug)
        resp.set_cookie(
            key=f"ws_{ws.slug}", value=token,
            httponly=True, max_age=SESSION_TTL, samesite="lax",
        )
    return resp


@app.get("/api/workspace/{slug}/members")
async def api_get_members(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    members = get_members(ws.id)
    return JSONResponse([m.to_dict() for m in members])


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

        member, erro = add_member(ws.id, dados["nome"], dados["curso"], dados["busy"], force=force)

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
    if not ws.password_hash:
        return JSONResponse({"erro": "Workspace não possui senha."}, status_code=400)
    body = await request.json()
    password = body.get("password", "")
    if not check_password(password, ws.password_hash):
        return JSONResponse({"erro": "Senha incorreta."}, status_code=401)
    token = _make_session_token(ws.slug)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=f"ws_{ws.slug}", value=token,
        httponly=True, max_age=SESSION_TTL, samesite="lax",
    )
    return resp


@app.post("/api/workspace/{slug}/members")
async def api_add_member(slug: str, request: Request):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    body = await request.json()
    nome = body.get("nome", "").strip()
    curso = body.get("curso", "")
    busy = body.get("busy", [])
    force = body.get("force", False)
    if not nome:
        return JSONResponse({"erro": "Nome é obrigatório."}, status_code=400)
    member, erro = add_member(ws.id, nome, curso, busy, force=force)
    if erro:
        return JSONResponse({"erro": erro, "nome_existente": True}, status_code=409)
    return JSONResponse(member.to_dict())


@app.delete("/api/workspace/{slug}/members/{member_id}")
async def api_remove_member(request: Request, slug: str, member_id: int):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    if not _require_auth(request, ws):
        return JSONResponse({"erro": "Acesso negado."}, status_code=403)
    ok = remove_member(member_id)
    if not ok:
        return JSONResponse({"erro": "Membro não encontrado."}, status_code=404)
    return JSONResponse({"ok": True})
