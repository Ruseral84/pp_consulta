from __future__ import annotations

import hmac
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Dict, Tuple

from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import quote_plus, unquote_plus

# === Config ===
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SUBMIT_SECRET = os.getenv("SUBMIT_SECRET", "devsecret")  # misma clave en bot y servidor
PENDING_FILE = BASE_DIR / "pending_submissions.json"


# ---------- Utilidades de firma ----------
FIELD_ORDER = ("mid", "season", "date", "division", "j1", "j2")


def _canonical_query(params: Dict[str, str]) -> str:
    """
    Construye la 'query' canónica (valores urlencoded con quote_plus y
    claves en orden fijo) para firmar/verificar.
    """
    parts = []
    for k in FIELD_ORDER:
        v = params[k]
        parts.append(f"{k}={quote_plus(v)}")
    return "&".join(parts)


def _make_sig(params: Dict[str, str]) -> str:
    canon = _canonical_query(params).encode("utf-8")
    return hmac.new(SUBMIT_SECRET.encode("utf-8"), canon, sha256).hexdigest()


def _verify_sig(params: Dict[str, str], sig: str) -> None:
    expected = _make_sig(params)
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Enlace inválido o manipulado")


# ---------- API Router ----------
router = APIRouter()


def build_submit_link(
    *,
    base_url: str,
    mid: str,
    season: str,
    date: str,
    division: str,
    j1: str,
    j2: str,
) -> str:
    """
    Construye la URL firmada que publicamos en Telegram.
    ¡Usada por el bot! Debe estar en este módulo y con esta firma.
    """
    params = {
        "mid": mid,
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
    }
    sig = _make_sig(params)
    query = _canonical_query(params) + f"&sig={sig}"
    return f"{base_url.rstrip('/')}/submit?{query}"


@router.get("/submit", response_class=HTMLResponse)
def submit_get(
    request: Request,
    mid: str,
    season: str,
    date: str,
    division: str,
    j1: str,
    j2: str,
    sig: str,
):
    """
    Muestra el formulario. Verifica la firma *antes*.
    Los valores que se presentan están ya decodificados (sin %20).
    """
    raw_params = {
        "mid": mid,
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
    }
    _verify_sig(raw_params, sig)

    ctx = {
        "request": request,
        # para mostrar bonitos:
        "mid": mid,
        "season": unquote_plus(season),
        "date": unquote_plus(date),
        "division": unquote_plus(division),
        "j1": unquote_plus(j1),
        "j2": unquote_plus(j2),
        "sig": sig,
    }
    return TEMPLATES.TemplateResponse("submit_result.html", ctx)


@router.post("/submit", response_class=HTMLResponse)
def submit_post(
    request: Request,
    mid: str = Form(...),
    season: str = Form(...),
    date: str = Form(...),
    division: str = Form(...),
    j1: str = Form(...),
    j2: str = Form(...),
    sig: str = Form(...),
    s1_j1: str = Form(""),
    s1_j2: str = Form(""),
    s2_j1: str = Form(""),
    s2_j2: str = Form(""),
    s3_j1: str = Form(""),
    s3_j2: str = Form(""),
    s4_j1: str = Form(""),
    s4_j2: str = Form(""),
    s5_j1: str = Form(""),
    s5_j2: str = Form(""),
    submitter: str = Form(""),
):
    """
    Recibe el formulario, vuelve a verificar la firma y deja el registro en un JSON
    para revisión/aceptación posterior (flujo admin que ya tenías).
    """
    raw_params = {
        "mid": mid,
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
    }
    _verify_sig(raw_params, sig)

    record = {
        "mid": mid,
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
        "sets": {
            "s1": [s1_j1, s1_j2],
            "s2": [s2_j1, s2_j2],
            "s3": [s3_j1, s3_j2],
            "s4": [s4_j1, s4_j2],
            "s5": [s5_j1, s5_j2],
        },
        "submitter": submitter,
    }

    existing = []
    if PENDING_FILE.exists():
        try:
            existing = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = []
    existing.append(record)
    PENDING_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    return TEMPLATES.TemplateResponse(
        "submit_result_done.html",
        {
            "request": request,
            "j1": unquote_plus(j1),
            "j2": unquote_plus(j2),
            "date": unquote_plus(date),
            "division": unquote_plus(division),
        },
    )
