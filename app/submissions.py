from __future__ import annotations

import hmac
import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Any

from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import quote_plus, unquote_plus

# === Config ===
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Misma clave en bot y servidor (si usas firma)
SUBMIT_SECRET = os.getenv("SUBMIT_SECRET", "devsecret")

# Moderación
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "devadmin")

PENDING_FILE = BASE_DIR / "pending_submissions.json"
APPROVED_FILE = BASE_DIR / "approved_submissions.json"

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
    decidas migrar a enlaces con firma (compatible con /submit-result).
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


def _load_json(path: Path) -> List[Dict[str, Any]]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_json(path: Path, data: List[Dict[str, Any]]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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

    existing = _load_json(PENDING_FILE)
    existing.append(record)
    _save_json(PENDING_FILE, existing)

    return TEMPLATES.TemplateResponse(
        "submit_result_done.html",
        {"request": request, **_decoded_ctx({**params, "sig": sig})},
    )


# --------- Rutas LEGACY (/submit-result) ---------
# Aceptan los nombres antiguos (id/fecha/div) y enlaces sin firma.
@router.get("/submit-result", response_class=HTMLResponse)
def submit_result_get_legacy(request: Request):
    params = _norm_qs(request)
    sig = params.pop("sig", "")
    if sig:
        _verify_sig(params, sig)
    ctx = {"request": request, **_decoded_ctx({**params, "sig": sig})}
    return TEMPLATES.TemplateResponse("submit_result.html", ctx)


@router.post("/submit-result", response_class=HTMLResponse)
async def submit_result_post_legacy(request: Request):
    form = await request.form()
    mid = form.get("mid") or form.get("id") or ""
    season = form.get("season") or ""
    date = form.get("date") or form.get("fecha") or ""
    division = form.get("division") or form.get("div") or ""
    j1 = form.get("j1") or ""
    j2 = form.get("j2") or ""
    sig = form.get("sig", "")

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

    existing = _load_json(PENDING_FILE)
    existing.append(record)
    _save_json(PENDING_FILE, existing)

    return TEMPLATES.TemplateResponse(
        "submit_result_done.html",
        {"request": request, **_decoded_ctx({**params, "sig": sig})},
    )


# ==========================
# ====== ADMIN ROUTES ======
# ==========================
def _require_admin(request: Request) -> None:
    token = request.query_params.get("token") or request.headers.get("x-admin-token") or ""
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Token inválido")


def _flatten_item(it: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convierte el registro (que guarda sets como dict) al formato que
    espera admin_review.html: s1_j1, s1_j2, ...
    También soporta registros viejos que ya vinieran aplanados.
    """
    out = {
        "id": it.get("mid", ""),
        "fecha": it.get("date", ""),
        "division": it.get("division", ""),
        "j1": it.get("j1", ""),
        "j2": it.get("j2", ""),
    }

    sets = it.get("sets")
    if isinstance(sets, dict):
        for i in range(1, 6):
            pair = sets.get(f"s{i}", ["", ""])
            a, b = (pair + ["", ""])[:2]
            out[f"s{i}_j1"] = a
            out[f"s{i}_j2"] = b
    else:
        # Compatibilidad si ya estaba aplanado
        for i in range(1, 6):
            out[f"s{i}_j1"] = it.get(f"s{i}_j1", "")
            out[f"s{i}_j2"] = it.get(f"s{i}_j2", "")

    return out


@router.get("/admin/review", response_class=HTMLResponse)
def admin_review(request: Request):
    _require_admin(request)
    pending = _load_json(PENDING_FILE)
    items = [_flatten_item(x) for x in pending]
    # PASAMOS EL TOKEN AL TEMPLATE para construir los href
    token = request.query_params.get("token", "")
    return TEMPLATES.TemplateResponse(
        "admin_review.html",
        {"request": request, "items": items, "token": token},
    )


@router.get("/admin/reject")
def admin_reject(request: Request, id: str):
    _require_admin(request)
    pending = _load_json(PENDING_FILE)
    pending = [x for x in pending if x.get("mid") != id]
    _save_json(PENDING_FILE, pending)
    # volver a la lista
    token = request.query_params.get("token", "")
    return RedirectResponse(url=f"/admin/review?token={quote_plus(token)}", status_code=302)


@router.get("/admin/approve")
def admin_approve(request: Request, id: str):
    _require_admin(request)
    pending = _load_json(PENDING_FILE)
    keep: List[Dict[str, Any]] = []
    moved: List[Dict[str, Any]] = []

    for x in pending:
        if x.get("mid") == id:
            moved.append(x)
        else:
            keep.append(x)

    if moved:
        approved = _load_json(APPROVED_FILE)
        approved.extend(moved)
        _save_json(APPROVED_FILE, approved)

    _save_json(PENDING_FILE, keep)
    token = request.query_params.get("token", "")
    return RedirectResponse(url=f"/admin/review?token={quote_plus(token)}", status_code=302)
