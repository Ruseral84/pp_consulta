# app/bot_matches.py
from __future__ import annotations

"""
Bot de partidos (AT3W) — versión estable, completa y comentada.

- Lee RESULTADOS T5.xlsx (primera hoja).
- Deduce división usando JUGADORES T5.xlsx (primera hoja, 4 columnas de divisiones).
- Construye 2 mensajes:
    1) PARTIDOS RETRASADOS (sin jugar, fecha < hoy)
    2) PARTIDOS DE HOY (sin jugar, fecha == hoy)
- Cada línea incluye enlace clicable a /submit-result del servicio en Render.
- Ejecutable manualmente con --once o con scheduler L-J 09:00.
"""

import os
import argparse
import hashlib
import time
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import urllib.parse as urlparse

import pandas as pd
import requests

# ---------------------------
# Cargar .env desde app/ (NO tocar rutas de Excel)
# ---------------------------
try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).with_name(".env")
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH)
except Exception:
    # Si no hay dotenv, seguimos (variables pueden venir del entorno)
    pass

# ---------------------------
# Rutas y configuración
# ---------------------------
ROOT = Path(__file__).resolve().parents[1]  # raíz del repo (carpeta padre de app/)
RESULTS_XLSX = ROOT / "RESULTADOS T5.xlsx"
PLAYERS_XLSX = ROOT / "JUGADORES T5.xlsx"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Base pública para construir enlaces clicables (Render)
# Ej: https://pp-consulta.onrender.com
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "").rstrip("/")

# Ruta de submit (en el servidor FastAPI)
# Hemos estandarizado a /submit-result según tu backend actual
SUBMIT_PATH = "/submit-result"

SEASON_NAME = os.getenv("SEASON_NAME", "Temporada 5").strip()
SUBMIT_SALT = os.getenv("SUBMIT_SALT", "pp-at3w-salt").strip()

# ---------------------------
# Utilidades de lectura
# ---------------------------

def _norm_name(s: str) -> str:
    return (s or "").strip().lower()

def _read_players_map(path: Path) -> Dict[str, str]:
    """
    Devuelve un dict {nombre_normalizado: etiqueta_division}
    Primera hoja del Excel de jugadores:
        Col0=División 1, Col1=División 2, Col2=División 3 - A, Col3=División 3 - B
    """
    try:
        df = pd.read_excel(path, sheet_name=0, header=0)
    except Exception:
        return {}

    col2label = {
        0: "División 1",
        1: "División 2",
        2: "División 3 - A",
        3: "División 3 - B",
    }

    mp: Dict[str, str] = {}
    for col_idx, label in col2label.items():
        if col_idx >= df.shape[1]:
            continue
        col = df.iloc[:, col_idx]
        for val in col.dropna():
            name = _norm_name(str(val))
            if name:
                mp[name] = label
    return mp

def _read_results_first_sheet(path: Path) -> pd.DataFrame:
    """
    Lee SOLO la PRIMERA hoja del Excel de Resultados T5.
    Se asume el siguiente formato por POSICIÓN de columnas (como acordamos):
      - 0: Fecha
      - 1: (número/índice del partido) -> NO usar para división
      - 2: Jugador 1 (texto)
      - 3: Jugador 2 (texto)
      - 4..: Celdas de sets (puntos por set J1/J2, hasta 5 sets)
    """
    df = pd.read_excel(path, sheet_name=0, header=0)
    # Protección mínima por si vienen menos columnas (raro, pero robusto)
    if df.shape[1] <= 3:
        for _ in range(4 - df.shape[1]):
            df[f"_pad_{_}"] = pd.NA
    return df

# ---------------------------
# Lógica de negocio
# ---------------------------

def _norm_date(ts) -> Optional[pd.Timestamp]:
    """Convierte a Timestamp y normaliza a 00:00 (sin hora)."""
    if pd.isna(ts):
        return None
    try:
        t = pd.to_datetime(ts, errors="coerce")
        if pd.isna(t):
            return None
        return t.normalize()
    except Exception:
        return None

def _row_is_unplayed(row: pd.Series) -> bool:
    """
    Partido NO jugado = no hay puntuaciones en columnas de sets a partir de la 4.
    Consideramos jugado si hay algún número > 0 o texto no vacío en esas columnas.
    """
    if len(row) <= 4:
        return True
    for x in row.iloc[4:]:
        if pd.notna(x) and str(x).strip() != "":
            # Cualquier valor relleno marca que hay resultado puesto
            return False
    return True

def _division_for_players(pmap: Dict[str, str], j1: str, j2: str) -> str:
    """
    Determina la división comparando ambos jugadores contra el listado de JUGADORES.
    Si ambos están en la misma división, se devuelve esa; si no, el primero que case;
    en último extremo 'Desconocida'.
    """
    j1d = pmap.get(_norm_name(j1), None)
    j2d = pmap.get(_norm_name(j2), None)
    if j1d and j2d:
        if j1d == j2d:
            return j1d
        # Si no coinciden, priorizamos j1 por simplicidad (caso raro)
        return j1d
    return j1d or j2d or "Desconocida"

def _build_match_link(fecha_iso: str, division: str, j1: str, j2: str) -> str:
    """
    Construye la URL a /submit-result con parámetros en query-string.
    El texto visible NO lleva %20; la URL sí va codificada (espacios como '+').
    """
    if not SITE_BASE_URL:
        return ""  # si no hay base URL, no enlazamos

    # ID reproducible basado en datos del partido + salt (opcional)
    token_src = f"{SEASON_NAME}|{fecha_iso}|{division}|{j1}|{j2}|{SUBMIT_SALT}"
    match_id = hashlib.sha1(token_src.encode("utf-8")).hexdigest()[:16]

    params = {
        "season": SEASON_NAME,
        "date": fecha_iso,
        "div": division,
        "j1": j1,
        "j2": j2,
        "id": match_id,
    }
    # urlencode con quote_plus => espacios como '+', no como '%20'
    query = urlparse.urlencode(params, doseq=False)
    return f"{SITE_BASE_URL}{SUBMIT_PATH}?{query}"

def _fmt_line_with_link(fecha_iso: str, division: str, j1: str, j2: str) -> str:
    """
    Línea de salida para Telegram (HTML), toda la línea clicable.
    """
    text = f"{fecha_iso} - {division} - {j1} vs {j2}"
    href = _build_match_link(fecha_iso, division, j1, j2)
    if href:
        return f'<a href="{href}">{text}</a>'
    return text

def _collect_matches(t: date) -> Tuple[List[Dict], List[Dict]]:
    """
    Recorre el Excel y devuelve (retrasados, hoy) como listas de dicts:
    {fecha: 'YYYY-MM-DD', division: 'División X', j1: '...', j2: '...'}
    """
    # Cargar datos
    df = _read_results_first_sheet(RESULTS_XLSX)
    pmap = _read_players_map(PLAYERS_XLSX)

    today_ts = pd.Timestamp(t)
    delayed: List[Dict] = []
    today_list: List[Dict] = []

    for _, row in df.iterrows():
        fecha = _norm_date(row.iloc[0])
        if not fecha:
            continue

        # Jugadores
        j1 = str(row.iloc[2]).strip() if len(row) > 2 and pd.notna(row.iloc[2]) else ""
        j2 = str(row.iloc[3]).strip() if len(row) > 3 and pd.notna(row.iloc[3]) else ""
        if not j1 or not j2:
            continue

        if _row_is_unplayed(row):
            div = _division_for_players(pmap, j1, j2)
            if fecha == today_ts:
                today_list.append({
                    "fecha": fecha.strftime("%Y-%m-%d"),
                    "division": div,
                    "j1": j1,
                    "j2": j2,
                })
            elif fecha < today_ts:
                delayed.append({
                    "fecha": fecha.strftime("%Y-%m-%d"),
                    "division": div,
                    "j1": j1,
                    "j2": j2,
                })

    # Ordenar por fecha
    delayed.sort(key=lambda x: x["fecha"])
    today_list.sort(key=lambda x: x["fecha"])
    return delayed, today_list

# ---------------------------
# Mensajes y Telegram
# ---------------------------

def _compose_messages(delayed: List[Dict], today_list: List[Dict], today: date) -> Tuple[str, str]:
    """
    Devuelve (msg_retrasados, msg_hoy) en HTML para Telegram.
    """
    # Retrasados
    if delayed:
        m1_lines = []
        for m in delayed:
            m1_lines.append(_fmt_line_with_link(m["fecha"], m["division"], m["j1"], m["j2"]))
        m1 = "📋 <b>PARTIDOS RETRASADOS</b>:\n" + "\n".join(m1_lines)
    else:
        m1 = "📋 <b>PARTIDOS RETRASADOS</b>:\n(no hay)"

    # Hoy
    if today_list:
        m2_lines = []
        for m in today_list:
            m2_lines.append(_fmt_line_with_link(m["fecha"], m["division"], m["j1"], m["j2"]))
        m2 = f"📅 <b>PARTIDOS DE HOY ({today.isoformat()})</b>:\n" + "\n".join(m2_lines)
    else:
        m2 = f"📅 <b>PARTIDOS DE HOY ({today.isoformat()})</b>:\n(no hay)"

    return m1, m2

def _send_telegram(html: str) -> None:
    """
    Envía un mensaje HTML al grupo de Telegram.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env")
        print("⚠️ Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[BOT] Error enviando a Telegram: {e}")

# ---------------------------
# Flujo principal
# ---------------------------

def run_once(today: Optional[date] = None) -> None:
    t = today or date.today()
    try:
        delayed, today_list = _collect_matches(t)
        msg_retrasados, msg_hoy = _compose_messages(delayed, today_list, t)

        # Consola
        print(msg_retrasados)
        print()
        print(msg_hoy)

        # Telegram
        _send_telegram(msg_retrasados)
        _send_telegram(msg_hoy)
    except Exception as e:
        print(f"⚠️ Error en bot_matches: {e}")

def run_scheduler() -> None:
    """
    Planificador L-J 09:00. Ctrl+C para salir si se ejecuta en primer plano.
    """
    try:
        import schedule
    except ImportError:
        print("⚠️ Falta 'schedule'. Instálalo con: pip install schedule")
        return

    schedule.every().monday.at("09:00").do(run_once)
    schedule.every().tuesday.at("09:00").do(run_once)
    schedule.every().wednesday.at("09:00").do(run_once)
    schedule.every().thursday.at("09:00").do(run_once)

    print("⏰ Bot de partidos programado L-J a las 09:00. (Ctrl+C para salir)")
    while True:
        schedule.run_pending()
        time.sleep(1)

def main():
    parser = argparse.ArgumentParser(description="Bot de partidos (Resultados T5)")
    parser.add_argument("--once", action="store_true", help="Ejecuta solo una vez y sale")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_scheduler()

if __name__ == "__main__":
    main()
