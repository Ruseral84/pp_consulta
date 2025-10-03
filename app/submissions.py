# app/submissions.py
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi import Depends
from fastapi.staticfiles import StaticFiles
import secrets
from urllib.parse import urlencode

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Tokens en memoria para validar submissions
PENDING_SUBMISSIONS = {}

def build_submit_link(season: str, date: str, division: str, j1: str, j2: str, base_url: str) -> str:
    """Construye un enlace seguro a la ruta /submit en Render."""
    token = secrets.token_hex(8)
    mid = secrets.token_hex(8)
    sig = secrets.token_hex(8)

    PENDING_SUBMISSIONS[mid] = {
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
        "sig": sig,
    }

    params = {
        "mid": mid,
        "sig": sig,
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
    }
    return f"{base_url}/submit?{urlencode(params)}"

@router.get("/submit")
def submit_result(request: Request, mid: str, sig: str, season: str, date: str, division: str, j1: str, j2: str):
    if mid not in PENDING_SUBMISSIONS or PENDING_SUBMISSIONS[mid]["sig"] != sig:
        return templates.TemplateResponse("submit_result_done.html", {"request": request, "error": "Enlace inválido o expirado."})

    return templates.TemplateResponse("submit_result.html", {
        "request": request,
        "mid": mid,
        "sig": sig,
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2
    })

@router.post("/submit")
def submit_result_done(
    request: Request,
    mid: str = Form(...),
    sig: str = Form(...),
    season: str = Form(...),
    date: str = Form(...),
    division: str = Form(...),
    j1: str = Form(...),
    j2: str = Form(...),
    set1_j1: int = Form(...),
    set1_j2: int = Form(...),
    set2_j1: int = Form(...),
    set2_j2: int = Form(...),
    set3_j1: int = Form(0),
    set3_j2: int = Form(0),
):
    if mid not in PENDING_SUBMISSIONS or PENDING_SUBMISSIONS[mid]["sig"] != sig:
        return templates.TemplateResponse("submit_result_done.html", {"request": request, "error": "Token inválido."})

    # Aquí guardas resultados en tu Excel (omitido)
    del PENDING_SUBMISSIONS[mid]

    return templates.TemplateResponse("submit_result_done.html", {"request": request, "ok": True})
