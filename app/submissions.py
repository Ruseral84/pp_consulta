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
BASE_DIR = Path(__file__).resolve().parent              # app/
ROOT_DIR = BASE_DIR.parent                               # raíz del proyecto
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
    from urllib.parse import quote_plus as _qp
    parts = []
    for k in FIELD_ORDER:
        v = params[k]
        parts.append(f"{k}={_qp(v)}")
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
    *, base_url: str, mid: str, season: str, date: str, division: str, j1: str, j2: str
) -> str:
    """Construye una URL firmada (ruta /submit)."""
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

    return {"mid": mid, "season": season, "date": date, "division": division, "j1": j1, "j2": j2, "sig": sig or ""}


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
    """Convierte el registro al formato que espera admin_review.html."""
    out = {
        "id": it.get("mid", ""),
        "fecha": it.get("date", ""),
        "division": it.get("division", ""),
        "j1": it.get("j1", ""),
        "j2": it.get("j2", ""),
    }
    sets = it.get("sets")
    if isinstance(sets, dict):
        for i in range(1, 5 + 1):
            pair = sets.get(f"s{i}", ["", ""])
            a, b = (pair + ["", ""])[:2]
            out[f"s{i}_j1"] = a
            out[f"s{i}_j2"] = b
    else:
        for i in range(1, 5 + 1):
            out[f"s{i}_j1"] = it.get(f"s{i}_j1", "")
            out[f"s{i}_j2"] = it.get(f"s{i}_j2", "")
    return out


@router.get("/admin/review", response_class=HTMLResponse)
def admin_review(request: Request):
    _require_admin(request)
    pending = _load_json(PENDING_FILE)
    items = [_flatten_item(x) for x in pending]
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
    token = request.query_params.get("token", "")
    return RedirectResponse(url=f"/admin/review?token={quote_plus(token)}", status_code=302)


# ------- VOLCADO A EXCEL AL APROBAR --------
def _season_to_results_path(season_str: str) -> Path:
    """
    'Temporada 5' -> ROOT_DIR / 'RESULTADOS T5.xlsx'
    Si no reconoce el número, lanza 400.
    """
    import re
    m = re.search(r"(\d+)", season_str or "")
    if not m:
        raise HTTPException(status_code=400, detail="Temporada inválida")
    n = m.group(1)
    return ROOT_DIR / f"RESULTADOS T{n}.xlsx"


def _normalize_header(s: str) -> str:
    """normaliza para casar columnas: sin espacios, guiones ni acentos, en minúsculas"""
    import unicodedata, re
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[\s\-_/]+", "", s)
    return s


def _apply_to_excel(record: Dict[str, Any]) -> None:
    """
    Escribe en el Excel de resultados de la temporada indicada los puntos
    de S1..S5 para j1 y j2, buscando la fila por (Fecha, División, Jugador 1, Jugador 2).
    """
    from openpyxl import load_workbook

    xlsx = _season_to_results_path(record["season"])
    if not xlsx.exists():
        raise HTTPException(status_code=400, detail=f"No existe el Excel de resultados: {xlsx.name}")

    wb = load_workbook(filename=str(xlsx))
    ws = wb.active  # todos tus Excels tienen una sola hoja

    # Mapeo de cabeceras a índice de columna
    headers = {}
    for col in range(1, ws.max_column + 1):
        val = ws.cell(row=1, column=col).value
        if isinstance(val, str):
            headers[_normalize_header(val)] = col

    def _col(name_variants: List[str]) -> int | None:
        for v in name_variants:
            c = headers.get(_normalize_header(v))
            if c:
                return c
        return None

    # columnas clave (tolerando variantes de nombre)
    c_fecha = _col(["Fecha"])
    c_div = _col(["División", "Division"])
    c_j1 = _col(["Jugador 1", "J1", "Jugador1"])
    c_j2 = _col(["Jugador 2", "J2", "Jugador2"])

    need = [c_fecha, c_div, c_j1, c_j2]
    if any(c is None for c in need):
        wb.close()
        raise HTTPException(status_code=500, detail="No encuentro columnas clave en el Excel (Fecha/División/Jugador 1/Jugador 2)")

    # localizar fila del partido
    target_row = None
    for r in range(2, ws.max_row + 1):
        v_fecha = str(ws.cell(row=r, column=c_fecha).value or "").strip()
        v_div = str(ws.cell(row=r, column=c_div).value or "").strip()
        v1 = str(ws.cell(row=r, column=c_j1).value or "").strip()
        v2 = str(ws.cell(row=r, column=c_j2).value or "").strip()

        if (
            v_fecha == record["date"].strip()
            and v_div == record["division"].strip()
            and v1 == record["j1"].strip()
            and v2 == record["j2"].strip()
        ):
            target_row = r
            break

    if not target_row:
        wb.close()
        raise HTTPException(status_code=400, detail="No encuentro el partido en el Excel (fecha/división/jugadores)")

    # columnas de sets
    set_cols = []
    for i in range(1, 5 + 1):
        cj1 = _col([f"S{i}-J1", f"S{i} J1", f"S{i}_J1", f"S{i}J1"])
        cj2 = _col([f"S{i}-J2", f"S{i} J2", f"S{i}_J2", f"S{i}J2"])
        set_cols.append((cj1, cj2))

    # escribir valores
    for i, (cj1, cj2) in enumerate(set_cols, start=1):
        if cj1 and cj2:
            a, b = (record["sets"].get(f"s{i}", ["", ""]) + ["", ""])[:2]
            ws.cell(row=target_row, column=cj1).value = a or None
            ws.cell(row=target_row, column=cj2).value = b or None

    wb.save(str(xlsx))
    wb.close()


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
        # 1) volcar al Excel de la temporada
        try:
            _apply_to_excel(moved[-1])  # por si hubiera más de uno con el mismo id, usamos el último
        except HTTPException:
            # Re-lanzamos para que el usuario vea el error en pantalla de forma clara
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error escribiendo en Excel: {e}")

        # 2) registrar en approved_submissions.json (histórico)
        approved = _load_json(APPROVED_FILE)
        approved.extend(moved)
        _save_json(APPROVED_FILE, approved)

    _save_json(PENDING_FILE, keep)

    # back to list
    token = request.query_params.get("token", "")
    return RedirectResponse(url=f"/admin/review?token={quote_plus(token)}", status_code=302)
