# app/bot_matches.py
from __future__ import annotations
import os
import sys
import time
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import pandas as pd
import requests
from dotenv import load_dotenv

# --- Cargar .env desde app/ (solo para el bot) ---
THIS_DIR = Path(__file__).resolve().parent
load_dotenv(THIS_DIR / ".env")  # TELEGRAM_* y PUBLIC_BASE_URL aquí

# --- Config ---
ACTIVE_SEASON = os.getenv("ACTIVE_SEASON", "Temporada 5")
# Excels están en la raíz del repo (un nivel arriba de /app)
BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_XLSX = BASE_DIR / f"RESULTADOS T{ACTIVE_SEASON.split()[-1]}.xlsx"
PLAYERS_XLSX = BASE_DIR / f"JUGADORES T{ACTIVE_SEASON.split()[-1]}.xlsx"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Usamos el generador de enlaces del router de envíos
# (/submit?mid=...&sig=...&fecha=...&division=...&j1=...&j2=...&season=...)
from .submissions import build_submit_link  # :contentReference[oaicite:1]{index=1}


# --- Utilidades parsing (coinciden con las de la web) ---
def _is_number(x) -> bool:
    try:
        return pd.notna(x) and str(x).strip() != "" and float(x) == float(x)
    except Exception:
        return False

def _played_row(row: pd.Series) -> bool:
    """Hay resultado si existe algún set con tanteo numérico en ambas columnas."""
    for j1, j2 in [(4,5), (6,7), (8,9), (10,11), (12,13)]:
        a = row.iloc[j1] if j1 < len(row) else None
        b = row.iloc[j2] if j2 < len(row) else None
        if _is_number(a) and _is_number(b):
            return True
    return False

def _excel_date_to_ymd(v) -> Optional[str]:
    if pd.isna(v):
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.date().isoformat()
    try:
        return pd.to_datetime(v).date().isoformat()
    except Exception:
        # último recurso: cortar “YYYY-MM-DD HH:MM”
        s = str(v).strip()
        for sep in ("T", " "):
            if sep in s:
                s = s.split(sep, 1)[0]
        return s or None

def _load_players() -> Dict[str, List[str]]:
    """
    Devuelve {'División 1': [...], 'División 2': [...], ...}
    Leyendo columnas del Excel de jugadores (mismo criterio que la web).
    """
    mapping: Dict[str, List[str]] = {}
    if not PLAYERS_XLSX.exists():
        return mapping
    df = pd.read_excel(PLAYERS_XLSX, header=None)
    names = ["División 1", "División 2", "División 3 - A", "División 3 - B", "División 3 - C"]
    for i, div_name in enumerate(names):
        if i < df.shape[1]:
            col = df.iloc[:, i].dropna().astype(str).str.strip().tolist()
            col = [x for x in col if x and x.lower() != "nan"]
            if col:
                mapping[div_name] = col
    return mapping

def _guess_division(j1: str, j2: str, players_by_div: Dict[str, List[str]]) -> str:
    # ambos en la misma división
    for div, lst in players_by_div.items():
        if j1 in lst and j2 in lst:
            return div
    # si no, cualquiera que contenga a uno de los dos
    for div, lst in players_by_div.items():
        if j1 in lst or j2 in lst:
            return div
    return ""

def _collect_matches(today: date) -> Tuple[List[dict], List[dict]]:
    """
    Devuelve (retrasados, hoy) como listas de dicts:
    {'fecha': 'YYYY-MM-DD', 'division': 'División X', 'j1': '...', 'j2': '...'}
    """
    if not RESULTS_XLSX.exists():
        return [], []

    df = pd.read_excel(RESULTS_XLSX, header=None).fillna(value=pd.NA)
    players_by_div = _load_players()

    delayed: List[dict] = []
    today_list: List[dict] = []

    for _, row in df.iterrows():
        # columnas clave
        raw_date = row.iloc[0] if 0 < len(row) else None
        j1 = str(row.iloc[2]).strip() if 2 < len(row) and pd.notna(row.iloc[2]) else ""
        j2 = str(row.iloc[3]).strip() if 3 < len(row) and pd.notna(row.iloc[3]) else ""
        if not j1 or not j2:
            continue

        ymd = _excel_date_to_ymd(raw_date)
        if not ymd:
            continue
        try:
            d = datetime.strptime(ymd, "%Y-%m-%d").date()
        except Exception:
            continue

        # deducir división
        division = _guess_division(j1, j2, players_by_div)

        # ¿jugado ya?
        has_result = _played_row(row)

        if not has_result and d < today:
            delayed.append({"fecha": ymd, "division": division, "j1": j1, "j2": j2})
        elif not has_result and d == today:
            today_list.append({"fecha": ymd, "division": division, "j1": j1, "j2": j2})

    # ordenar por fecha y luego por división/j1
    delayed.sort(key=lambda r: (r["fecha"], r["division"], r["j1"], r["j2"]))
    today_list.sort(key=lambda r: (r["division"], r["j1"], r["j2"]))
    return delayed, today_list

def _fmt_line_with_link(match: dict) -> str:
    # Construimos enlace firmado del router /submit
    link = build_submit_link(
        fecha=match["fecha"],
        division=match["division"],
        j1=match["j1"],
        j2=match["j2"],
    )  # usa PUBLIC_BASE_URL internamente  :contentReference[oaicite:2]{index=2}
    text = f'{match["fecha"]} - {match["division"]} - {match["j1"]} vs {match["j2"]}'
    return f'<a href="{link}">{text}</a>'

def _send_telegram(html: str) -> None:
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

def run_once(today: Optional[date] = None):
    t = today or date.today()
    delayed, today_list = _collect_matches(t)

    # mensaje 1: retrasados
    if delayed:
        lines = "\n".join(_fmt_line_with_link(m) for m in delayed)
    else:
        lines = "(no hay)"
    msg1 = f"📋 <b>PARTIDOS RETRASADOS</b>:\n{lines}"
    print(msg1)
    _send_telegram(msg1)

    # mensaje 2: hoy
    header = f"📅 <b>PARTIDOS DE HOY ({t.isoformat()})</b>:\n"
    if today_list:
        lines2 = "\n".join(_fmt_line_with_link(m) for m in today_list)
    else:
        lines2 = "(no hay)"
    msg2 = header + lines2
    print(msg2)
    _send_telegram(msg2)

def run_scheduler():
    # programa lun-jue a las 09:00
    import schedule
    schedule.every().monday.at("09:00").do(run_once)
    schedule.every().tuesday.at("09:00").do(run_once)
    schedule.every().wednesday.at("09:00").do(run_once)
    schedule.every().thursday.at("09:00").do(run_once)

    print("[BOT] Scheduler activo (lun-jue 09:00). CTRL+C para salir.")
    while True:
        schedule.run_pending()
        time.sleep(1)

def main():
    parser = argparse.ArgumentParser(description="Bot de partidos de hoy y retrasados")
    parser.add_argument("--once", action="store_true", help="Ejecutar una vez y salir")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_scheduler()

if __name__ == "__main__":
    main()
