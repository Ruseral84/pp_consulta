from __future__ import annotations

import hmac
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import quote_plus, unquote_plus

# === Config ===
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Misma clave en bot y servidor (si lo usas con firma)
SUBMIT_SECRET = os.getenv("SUBMIT_SECRET", "devsecret")
PENDING_FILE = BASE_DIR / "pending_submissions.json"

# ---------- Utilidades de firma ----------
FIELD_ORDER = ("mid", "season", "date", "division", "j1", "j2")


def _canonical_query(params: Dict[str, str]) -> str:
    """Construye la query canónica con valores urlencoded (quote_plus)."""
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


# ---------- Router ----------
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
    Construye una URL firmada (ruta /submit). Úsala en el bot cuando
    decidas migrar a enlaces con firma.
    """
    params = {"mid": mid, "season": season, "date": date, "division": division, "j1": j1, "j2": j2}
    sig = _make_sig(params)
    query = _canonical_query(params) + f"&sig={sig}"
    return f"{base_url.rstrip('/')}/submit?{query}"


# --------- Helpers de compatibilidad ---------
def _norm_qs(request: Request) -> Dict[str, str]:
    """
    Normaliza query params procedentes de /submit y de /submit-result (legacy):
    - 'fecha' -> 'date'
    - 'div'   -> 'division'
    - 'id'    -> 'mid'
    Devuelve SIEMPRE mid/season/date/division/j1/j2 y, si existe, 'sig'.
    """
    q = request.query_params
    mid = q.get("mid") or q.get("id") or ""
    season = q.get("season") or ""
    date = q.get("date") or q.get("fecha") or ""
    division = q.get("division") or q.get("div") or ""
    j1 = q.get("j1") or ""
    j2 = q.get("j2") or ""
    sig = q.get("sig")  # puede no venir en enlaces antiguos

    return {
        "mid": mid,
        "season": season,
        "date": date,
        "division": division,
        "j1": j1,
        "j2": j2,
        "sig": sig or "",
    }


def _decoded_ctx(params: Dict[str, str]) -> Dict[str, str]:
    """Decodifica para mostrar bonito en la plantilla (sin %20)."""
    return {
        "mid": params["mid"],
        "season": unquote_plus(params["season"]),
        "date": unquote_plus(params["date"]),
        "division": unquote_plus(params["division"]),
        "j1": unquote_plus(params["j1"]),
        "j2": unquote_plus(params["j2"]),
        "sig": params.get("sig", ""),
    }


# --------- Rutas OFICIALES (/submit) ---------

@router.get("/submit", response_class=HTMLResponse)
def submit_get(
    request: Request,
    mid: str,
    season: str,
    date: str,
    division: str,
    j1: str,
    j2: str,
    sig: str = "",  # puede venir vacío si usas enlaces sin firma
):
    params = {"mid": mid, "season": season, "date": date, "division": division, "j1": j1, "j2": j2}
    # Verifica firma sólo si llega 'sig'
    if sig:
        _verify_sig(params, sig)

    ctx = {"request": request, **_decoded_ctx({**params, "sig": sig})}
    return TEMPLATES.TemplateResponse("submit_result.html", ctx)


@router.post("/submit", response_class=HTMLResponse)
async def submit_post(
    request: Request,
    mid: str = Form(...),
    season: str = Form(...),
    date: str = Form(...),
    division: str = Form(...),
    j1: str = Form(...),
    j2: str = Form(...),
    sig: str = Form("", description="Signature (optional for legacy links)"),
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
    params = {"mid": mid, "season": season, "date": date, "division": division, "j1": j1, "j2": j2}
    if sig:
        _verify_sig(params, sig)

    record = {
        **params,
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
        {"request": request, **_decoded_ctx({**params, "sig": sig})},
    )


# --------- Rutas LEGACY (/submit-result) ---------
# Aceptan los nombres antiguos (id/fecha/div) y enlaces sin firma.

@router.get("/submit-result", response_class=HTMLResponse)
def submit_result_get_legacy(request: Request):
    params = _norm_qs(request)
    # Si el enlace incluye firma, se verifica; si no, se acepta (compatibilidad)
    sig = params.pop("sig", "")
    if sig:
        _verify_sig(params, sig)
    ctx = {"request": request, **_decoded_ctx({**params, "sig": sig})}
    return TEMPLATES.TemplateResponse("submit_result.html", ctx)


@router.post("/submit-result", response_class=HTMLResponse)
async def submit_result_post_legacy(request: Request):
    form = await request.form()
    # Normaliza claves de formulario legacy -> oficiales
    mid = form.get("mid") or form.get("id") or ""
    season = form.get("season") or ""
    date = form.get("date") or form.get("fecha") or ""
    division = form.get("division") or form.get("div") or ""
    j1 = form.get("j1") or ""
    j2 = form.get("j2") or ""
    sig = form.get("sig", "")

    # sets
    def g(k: str) -> str:
        return form.get(k, "")

    params = {"mid": mid, "season": season, "date": date, "division": division, "j1": j1, "j2": j2}
    if sig:
        _verify_sig(params, sig)

    record = {
        **params,
        "sets": {
            "s1": [g("s1_j1"), g("s1_j2")],
            "s2": [g("s2_j1"), g("s2_j2")],
            "s3": [g("s3_j1"), g("s3_j2")],
            "s4": [g("s4_j1"), g("s4_j2")],
            "s5": [g("s5_j1"), g("s5_j2")],
        },
        "submitter": form.get("submitter", ""),
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
        {"request": request, **_decoded_ctx({**params, "sig": sig})},
    )
