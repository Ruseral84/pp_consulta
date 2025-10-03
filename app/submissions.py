from __future__ import annotations

import os
import hmac
import hashlib
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import unquote_plus, urlencode

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

# Carpeta de plantillas (mantén esta ruta como en tu proyecto)
TEMPLATES = Jinja2Templates(directory="app/templates")

# Temporada activa por defecto (se puede sobreescribir por querystring)
ACTIVE_SEASON = os.getenv("ACTIVE_SEASON", "Temporada 5")

# Secreto para firmar/enlazar envíos
SUBMIT_SECRET = os.getenv("SUBMIT_SECRET", "PLEASE_SET_SUBMIT_SECRET")

# Router para enganchar desde app.main
router = APIRouter()


# ---------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------

def _hexdigest(data: str) -> str:
    return hmac.new(SUBMIT_SECRET.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()


def check_sig(mid: str, sig: str) -> bool:
    """Valida la firma del enlace de envío de resultado."""
    try:
        expected = _hexdigest(mid)
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


def build_submit_link(
    base_url: str,
    *,
    season: str,
    fecha: str,
    division: str,
    j1: str,
    j2: str,
    mid: str,
) -> str:
    """
    Construye el enlace /submit firmado que usa el bot en Telegram.

    base_url: por ejemplo "https://pp-consulta.onrender.com"
    """
    base = base_url.rstrip("/")
    sig = _hexdigest(mid)
    qs = urlencode(
        {
            "mid": mid,
            "sig": sig,
            "fecha": fecha,
            "division": division,
            "j1": j1,
            "j2": j2,
            "season": season,
        }
    )
    return f"{base}/submit?{qs}"


# ---------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------

@router.get("/submit", response_class=HTMLResponse)
def submit_get(
    request: Request,
    mid: str,
    sig: str,
    fecha: str,
    division: str,
    j1: str,
    j2: str,
    season: str = ACTIVE_SEASON,
):
    """
    Muestra el formulario para introducir el resultado de un partido.
    - Decodificamos parámetros (unquote_plus) para que NO aparezca %20.
    - El H1 ahora es "Introducir resultado" (sin repetir fecha/división).
    """
    if not check_sig(mid, sig):
        raise HTTPException(status_code=403, detail="Enlace inválido")

    # Decodificar %20, +, etc. para que se muestren nombres bonitos
    fecha = unquote_plus(fecha)
    division = unquote_plus(division)
    j1 = unquote_plus(j1)
    j2 = unquote_plus(j2)

    ctx = {
        "request": request,
        "title": "Introducir resultado",  # H1 limpio
        "mid": mid,
        "sig": sig,
        "fecha": fecha,
        "division": division,
        "j1": j1,
        "j2": j2,
        "season": season,
    }
    return TEMPLATES.TemplateResponse("submit_result.html", ctx)


@router.post("/submit", response_class=HTMLResponse)
def submit_post(
    request: Request,
    mid: str = Form(...),
    sig: str = Form(...),
    fecha: str = Form(...),
    division: str = Form(...),
    j1: str = Form(...),
    j2: str = Form(...),
    season: str = Form(ACTIVE_SEASON),
    s1j1: Optional[str] = Form(None),
    s1j2: Optional[str] = Form(None),
    s2j1: Optional[str] = Form(None),
    s2j2: Optional[str] = Form(None),
    s3j1: Optional[str] = Form(None),
    s3j2: Optional[str] = Form(None),
    s4j1: Optional[str] = Form(None),
    s4j2: Optional[str] = Form(None),
    s5j1: Optional[str] = Form(None),
    s5j2: Optional[str] = Form(None),
    sender_name: Optional[str] = Form(None),
):
    """
    Recibe el resultado introducido. No cambiamos tu flujo:
    - Valida la firma.
    - Pasa los datos a la plantilla de confirmación/agradecimiento que ya usas.
    """
    if not check_sig(mid, sig):
        raise HTTPException(status_code=403, detail="Enlace inválido")

    # Decodificar por si llega con encoding desde el formulario
    fecha = unquote_plus(fecha)
    division = unquote_plus(division)
    j1 = unquote_plus(j1)
    j2 = unquote_plus(j2)

    # Normalizamos casillas vacías a ""
    def norm(v: Optional[str]) -> str:
        return (v or "").strip()

    payload = {
        "mid": mid,
        "season": season,
        "fecha": fecha,
        "division": division,
        "j1": j1,
        "j2": j2,
        "sets": [
            [norm(s1j1), norm(s1j2)],
            [norm(s2j1), norm(s2j2)],
            [norm(s3j1), norm(s3j2)],
            [norm(s4j1), norm(s4j2)],
            [norm(s5j1), norm(s5j2)],
        ],
        "sender_name": norm(sender_name),
    }

    ctx = {"request": request, "title": "Resultado enviado", **payload}
    return TEMPLATES.TemplateResponse("submit_thanks.html", ctx)


@router.get("/submit/done", response_class=HTMLResponse)
def submit_done(request: Request):
    return TEMPLATES.TemplateResponse(
        "submit_result_done.html",
        {"request": request, "title": "Resultado guardado"}
    )
