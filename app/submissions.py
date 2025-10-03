import os
import uuid
import hmac
import hashlib
from urllib.parse import urlencode, quote, unquote

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Clave para firmar los enlaces
SECRET_KEY = os.getenv("SECRET_KEY", "supersecret")

# Base pública donde vive tu servicio (Render). Si no está, cae a localhost.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def build_submit_link(
    *,
    # Admite español ("fecha") y lo mapea a "date" para la URL
    fecha: str | None = None,
    date: str | None = None,
    division: str,
    j1: str,
    j2: str,
    season: str | None = None,
    base_url: str | None = None,
) -> str:
    """
    Genera un enlace firmado al formulario /submit. Pensado para ser llamado
    desde el bot en local, apuntando a Render. Si se pasa `fecha`, se mapea a `date`.
    """
    # Resolver base URL: prioridad al argumento, luego .env
    base = (base_url or PUBLIC_BASE_URL).rstrip("/")

    # Normalizar 'date' a partir de 'fecha' si viene en español
    if not date and fecha:
        date = fecha
    if not date:
        raise ValueError("Falta el parámetro 'date'/'fecha' para el enlace de submit.")

    # La temporada es opcional; si no llega, intentamos tomarla del entorno
    season = season or os.getenv("ACTIVE_SEASON", "Temporada 5")

    match_id = uuid.uuid4().hex[:16]

    params = {
        "mid": match_id,
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
    }
    # Importante: quote_via=quote para no dejar %20 en nombres
    query_string = urlencode(params, quote_via=quote)

    signature = hmac.new(
        SECRET_KEY.encode(),
        query_string.encode(),
        hashlib.sha256
    ).hexdigest()[:16]

    return f"{base}/submit?{query_string}&sig={signature}"


def verify_link(params: dict) -> bool:
    """Verifica la firma HMAC del enlace de /submit."""
    sig = params.pop("sig", "")
    query_string = urlencode(params, quote_via=quote)
    expected_sig = hmac.new(
        SECRET_KEY.encode(),
        query_string.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return hmac.compare_digest(sig, expected_sig)


@router.get("/submit", response_class=HTMLResponse)
async def submit_form(
    request: Request,
    season: str,
    date: str,
    division: str,
    j1: str,
    j2: str,
    mid: str,
    sig: str
):
    params = {
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
        "mid": mid,
        "sig": sig,
    }
    if not verify_link(params.copy()):
        return HTMLResponse("❌ Enlace inválido o manipulado", status_code=400)

    # Decodificar los parámetros para quitar %20 en pantalla
    season = unquote(season)
    division = unquote(division)
    j1 = unquote(j1)
    j2 = unquote(j2)

    return templates.TemplateResponse(
        "submit_result.html",
        {
            "request": request,
            "season": season,
            "date": date,
            "division": division,
            "j1": j1,
            "j2": j2,
            "mid": mid,
            "sig": sig,
        },
    )


@router.post("/submit", response_class=HTMLResponse)
async def submit_result(
    request: Request,
    season: str = Form(...),
    date: str = Form(...),
    division: str = Form(...),
    j1: str = Form(...),
    j2: str = Form(...),
    mid: str = Form(...),
    sig: str = Form(...),
    set1_j1: int = Form(...),
    set1_j2: int = Form(...),
    set2_j1: int = Form(...),
    set2_j2: int = Form(...),
    set3_j1: int = Form(...),
    set3_j2: int = Form(...),
    set4_j1: int = Form(...),
    set4_j2: int = Form(...),
    set5_j1: int = Form(...),
    set5_j2: int = Form(...),
):
    params = {
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
        "mid": mid,
        "sig": sig,
    }
    if not verify_link(params.copy()):
        return HTMLResponse("❌ Enlace inválido o manipulado", status_code=400)

    result = {
        "season": unquote(season),
        "date": date,
        "division": unquote(division),
        "j1": unquote(j1),
        "j2": unquote(j2),
        "sets": [
            (set1_j1, set1_j2),
            (set2_j1, set2_j2),
            (set3_j1, set3_j2),
            (set4_j1, set4_j2),
            (set5_j1, set5_j2),
        ],
    }

    # Aquí se podría guardar en BD o archivo temporal
    return templates.TemplateResponse("submit_thanks.html", {"request": request, "result": result})


@router.get("/admin/review", response_class=HTMLResponse)
async def review_results(request: Request):
    # Aquí se cargarían resultados pendientes de revisar
    results = []
    return templates.TemplateResponse("admin_review.html", {"request": request, "results": results})


@router.post("/admin/approve", response_class=HTMLResponse)
async def approve_result(request: Request, mid: str = Form(...)):
    # Aquí se aprobaría un resultado pendiente
    return templates.TemplateResponse("submit_result_done.html", {"request": request, "mid": mid})
