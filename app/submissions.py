# app/submissions.py
from __future__ import annotations
import os, hmac, hashlib, sqlite3, datetime as dt
from pathlib import Path
from typing import Optional, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import pandas as pd
from openpyxl import load_workbook

# ========= Config =========
BASE_DIR = Path(__file__).resolve().parent.parent  # carpeta raíz del proyecto
DATA_DIR = BASE_DIR                                  # Excels están en la raíz
DB_PATH = BASE_DIR / "submissions.db"
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

# Clave para firmar/enlazar partidos (para que no se manipulen parámetros)
LINK_SECRET = os.getenv("LINK_SECRET", "dev-secret-change-me").encode("utf-8")

# Si usas Render, expón tu dominio en una env var para generar enlaces en el bot
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# Nombre del excel de resultados (temporada activa)
ACTIVE_SEASON = os.getenv("ACTIVE_SEASON", "Temporada 5")
RESULTS_XLSX = DATA_DIR / "RESULTADOS T5.xlsx"

# Columnas (por posición, tal como definiste)
#   0: Fecha, 2: J1, 3: J2, 4..13 sets-puntos (S1-J1, S1-J2, ..., S5-J2)
COL_FECHA = 0
COL_J1    = 2
COL_J2    = 3
COL_SETS_START = 4
COL_SETS_END   = 14  # sin incluir

# ========= Router =========
router = APIRouter(tags=["submissions"])


# ========= DB helpers =========
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = _db()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            season TEXT NOT NULL,
            fecha TEXT NOT NULL,            -- ISO yyyy-mm-dd
            division TEXT NOT NULL,
            j1 TEXT NOT NULL,
            j2 TEXT NOT NULL,
            s1j1 INTEGER, s1j2 INTEGER,
            s2j1 INTEGER, s2j2 INTEGER,
            s3j1 INTEGER, s3j2 INTEGER,
            s4j1 INTEGER, s4j2 INTEGER,
            s5j1 INTEGER, s5j2 INTEGER,
            status TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected
            submitted_at TEXT NOT NULL,     -- ISO timestamp
            submitted_by TEXT               -- opcional (nick)
        );
        """
    )
    conn.commit()
    conn.close()

init_db()


# ========= Link signing / verification =========
def make_match_id(fecha: str, division: str, j1: str, j2: str) -> str:
    """ID determinista del partido (estable)."""
    base = f"{fecha}|{division}|{j1}|{j2}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()
    return digest[:16]

def sign_link(mid: str) -> str:
    sig = hmac.new(LINK_SECRET, mid.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
    return sig

def check_sig(mid: str, sig: str) -> bool:
    return hmac.compare_digest(sign_link(mid), sig)


# ========= Excel helpers =========
def _norm_name(s: str) -> str:
    return " ".join(str(s).strip().lower().split())

def _to_date_str(value) -> str:
    """Devuelve yyyy-mm-dd (los Excels pueden traer Timestamp / datetime / str)."""
    if pd.isna(value):
        return ""
    if isinstance(value, dt.date):
        return value.isoformat()
    try:
        return pd.to_datetime(value).date().isoformat()
    except Exception:
        return str(value)

def _find_result_row(fecha_iso: str, j1: str, j2: str) -> Optional[int]:
    """
    Devuelve el índice de fila (0-based) en el DataFrame de RESULTADOS T5.xlsx
    que coincide con fecha + j1 + j2. Si no encuentra, None.
    """
    if not RESULTS_XLSX.exists():
        return None

    df = pd.read_excel(RESULTS_XLSX, header=None)
    # normaliza
    j1n = _norm_name(j1)
    j2n = _norm_name(j2)

    for i in range(len(df)):
        f = _to_date_str(df.iat[i, COL_FECHA])
        j1v = _norm_name(df.iat[i, COL_J1]) if COL_J1 < df.shape[1] else ""
        j2v = _norm_name(df.iat[i, COL_J2]) if COL_J2 < df.shape[1] else ""
        if f == fecha_iso and j1v == j1n and j2v == j2n:
            return i
    return None

def _write_results_excel(fecha_iso: str, j1: str, j2: str, sets: List[Optional[int]]) -> None:
    """
    Escribe los 10 valores (S1-J1, S1-J2, ..., S5-J2) en la fila del partido.
    """
    row_idx = _find_result_row(fecha_iso, j1, j2)
    if row_idx is None:
        raise RuntimeError("No se encontró el partido en el Excel (fecha/j1/j2).")

    # Abrimos con openpyxl para escribir preservando formato
    wb = load_workbook(RESULTS_XLSX)
    ws = wb.active

    # Pandas suele considerar la primera fila como 0, openpyxl es 1-based y tiene cabecera real.
    # Como cargamos el excel “tal cual” sin header, fila real = row_idx + 1
    excel_row = row_idx + 1

    # Columnas 5..14 (1-based: col = inicial + offset)
    for k, value in enumerate(sets):  # 0..9
        if value is None: 
            v = None
        else:
            v = int(value)
        ws.cell(row=excel_row, column=(COL_SETS_START + 1) + k, value=v)

    wb.save(RESULTS_XLSX)


# ========= Formularios =========
@router.get("/submit", response_class=HTMLResponse)
def submit_form(request: Request,
                mid: str,
                sig: str,
                fecha: str,
                division: str,
                j1: str,
                j2: str,
                season: str = ACTIVE_SEASON):
    """
    Formulario de alta de resultados (solo visualiza si la firma es válida).
    """
    if not check_sig(mid, sig):
        raise HTTPException(status_code=403, detail="Enlace inválido o caducado")

    return TEMPLATES.TemplateResponse(
        "submit_match.html",
        {
            "request": request,
            "mid": mid,
            "sig": sig,
            "fecha": fecha,
            "division": division,
            "j1": j1,
            "j2": j2,
            "season": season,
            "title": f"Introducir resultado — {fecha} — {division}"
        }
    )


@router.post("/submit", response_class=HTMLResponse)
def submit_post(
    request: Request,
    mid: str = Form(...),
    sig: str = Form(...),
    fecha: str = Form(...),
    division: str = Form(...),
    j1: str = Form(...),
    j2: str = Form(...),
    s1j1: Optional[int] = Form(None),
    s1j2: Optional[int] = Form(None),
    s2j1: Optional[int] = Form(None),
    s2j2: Optional[int] = Form(None),
    s3j1: Optional[int] = Form(None),
    s3j2: Optional[int] = Form(None),
    s4j1: Optional[int] = Form(None),
    s4j2: Optional[int] = Form(None),
    s5j1: Optional[int] = Form(None),
    s5j2: Optional[int] = Form(None),
    who: Optional[str]  = Form(None),
    season: str = Form(ACTIVE_SEASON),
):
    if not check_sig(mid, sig):
        raise HTTPException(status_code=403, detail="Enlace inválido")

    payload = [s1j1, s1j2, s2j1, s2j2, s3j1, s3j2, s4j1, s4j2, s5j1, s5j2]
    if all(v is None for v in payload):
        raise HTTPException(status_code=400, detail="Debes introducir al menos un set")

    conn = _db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO submissions (
            match_id, season, fecha, division, j1, j2,
            s1j1, s1j2, s2j1, s2j2, s3j1, s3j2, s4j1, s4j2, s5j1, s5j2,
            status, submitted_at, submitted_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            mid, season, fecha, division, j1, j2,
            s1j1, s1j2, s2j1, s2j2, s3j1, s3j2, s4j1, s4j2, s5j1, s5j2,
            dt.datetime.utcnow().isoformat(timespec="seconds"), who
        )
    )
    conn.commit()
    conn.close()

    return TEMPLATES.TemplateResponse(
        "submit_thanks.html",
        {"request": request, "j1": j1, "j2": j2, "fecha": fecha, "division": division}
    )


# ========= Panel admin =========
def _require_admin(request: Request):
    # Sencillo: cabecera X-Admin-Token igual a ADMIN_TOKEN
    admin_token = os.getenv("ADMIN_TOKEN")
    if not admin_token:
        return  # sin protección si no has definido token (local dev)
    if request.headers.get("X-Admin-Token") != admin_token:
        raise HTTPException(status_code=401, detail="Admin token incorrecto")

@router.get("/admin/review", response_class=HTMLResponse)
def admin_review(request: Request):
    _require_admin(request)
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM submissions WHERE status='pending' ORDER BY submitted_at ASC"
    ).fetchall()
    conn.close()
    return TEMPLATES.TemplateResponse(
        "admin_review.html", {"request": request, "subs": rows}
    )

@router.post("/admin/approve/{sub_id}")
def admin_approve(sub_id: int, request: Request):
    _require_admin(request)
    conn = _db()
    cur = conn.cursor()
    sub = cur.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
    if not sub: 
        raise HTTPException(status_code=404, detail="No existe")

    # Construimos vector de sets en orden
    sets = [
        sub["s1j1"], sub["s1j2"], sub["s2j1"], sub["s2j2"], sub["s3j1"], sub["s3j2"],
        sub["s4j1"], sub["s4j2"], sub["s5j1"], sub["s5j2"]
    ]
    try:
        _write_results_excel(sub["fecha"], sub["j1"], sub["j2"], sets)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"No se pudo escribir Excel: {e}")

    cur.execute("UPDATE submissions SET status='approved' WHERE id=?", (sub_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/review", status_code=303)

@router.post("/admin/reject/{sub_id}")
def admin_reject(sub_id: int, request: Request):
    _require_admin(request)
    conn = _db()
    conn.execute("UPDATE submissions SET status='rejected' WHERE id=?", (sub_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/review", status_code=303)


# ========= Utilidad para que el bot genere enlaces =========
def build_submit_link(fecha: str, division: str, j1: str, j2: str) -> str:
    mid = make_match_id(fecha, division, j1, j2)
    sig = sign_link(mid)
    return (
        f"{PUBLIC_BASE_URL}/submit"
        f"?mid={mid}&sig={sig}"
        f"&fecha={fecha}&division={division}"
        f"&j1={j1}&j2={j2}&season={ACTIVE_SEASON}"
    )
