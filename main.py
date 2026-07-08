import os, json, re
from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager

from database import init_db, create_workspace, get_workspace, get_members, add_member, remove_member
from extractor import extrair_de_pdf


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    os.makedirs("uploads", exist_ok=True)
    yield


app = FastAPI(title="NoTimeToRelax", lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ─── Frontend ───────────────────────────────────────────────────────────────


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
    members = get_members(ws.id)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "workspace": ws.to_dict(),
            "members_json": json.dumps([m.to_dict() for m in members]),
        },
    )


@app.get("/workspace/{slug}/upload", response_class=HTMLResponse)
async def upload_page(request: Request, slug: str):
    ws = get_workspace(slug)
    if not ws:
        return templates.TemplateResponse(
            "landing.html", {"request": request, "erro": "Workspace não encontrado."}
        )
    return templates.TemplateResponse(
        "upload.html", {"request": request, "workspace": ws.to_dict()}
    )


# ─── API ────────────────────────────────────────────────────────────────────


@app.post("/api/workspace")
async def api_create_workspace(name: str = Form(...)):
    if not name or not name.strip():
        return JSONResponse({"erro": "Nome é obrigatório."}, status_code=400)
    ws = create_workspace(name.strip())
    return JSONResponse(ws.to_dict())


@app.get("/api/workspace/{slug}/members")
async def api_get_members(slug: str):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    members = get_members(ws.id)
    return JSONResponse([m.to_dict() for m in members])


@app.post("/api/workspace/{slug}/upload")
async def api_upload(
    slug: str,
    file: UploadFile = File(...),
    force: bool = Query(False),
    preview: bool = Query(False),
):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"erro": "Apenas arquivos PDF são aceitos."}, status_code=400)

    tmp_path = os.path.join(UPLOAD_DIR, f"_tmp_{ws.id}.pdf")
    try:
        content = await file.read()
        with open(tmp_path, "wb") as f:
            f.write(content)

        dados = extrair_de_pdf(tmp_path)

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
        return JSONResponse({"erro": str(e)}, status_code=500)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/workspace/{slug}/members")
async def api_add_member(slug: str, request: Request):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
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
async def api_remove_member(slug: str, member_id: int):
    ws = get_workspace(slug)
    if not ws:
        return JSONResponse({"erro": "Workspace não encontrado."}, status_code=404)
    ok = remove_member(member_id)
    if not ok:
        return JSONResponse({"erro": "Membro não encontrado."}, status_code=404)
    return JSONResponse({"ok": True})
