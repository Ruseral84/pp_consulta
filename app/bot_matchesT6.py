# bot_matches.py
from __future__ import annotations

import os
import argparse
import hashlib
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import urllib.parse as urlparse

import pandas as pd
import requests


# ---------------------------
# Cargar .env (local/dev)
# ---------------------------
try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).with_name(".env")
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH)
except Exception:
    pass


# ---------------------------
# Configuración
# ---------------------------
ROOT = Path(__file__).resolve().parents[1]

SEASON_NAME = os.getenv("SEASON_NAME", "").strip()
ACTIVE_SEASON = os.getenv("ACTIVE_SEASON", "").strip()

if not SEASON_NAME:
    SEASON_NAME = ACTIVE_SEASON

RESULTS_XLSX = ROOT / f"RESULTADOS T{SEASON_NAME.split()[-1]}.xlsx"
PLAYERS_XLSX = ROOT / f"JUGADORES T{SEASON_NAME.split()[-1]}.xlsx"

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SITE_BASE_URL = os.getenv("SITE_BASE_URL", "").rstrip("/")
SUBMIT_PATH = "/submit-result"
SUBMIT_SALT = os.getenv("SUBMIT_SALT", "pp-at3w-salt").strip()


# ---------------------------
# Utilidades
# ---------------------------
def _norm_name(s: str) -> str:
    return (s or "").strip().lower()


def _looks_like_time(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (dtime, datetime, pd.Timestamp)):
        return True
    s = str(v).strip()
    if not s:
        return False
    if ":" in s:
        p = s.split(":")
        if len(p) >= 2 and p[0].isdigit() and p[1].isdigit():
            return True
    return False


def _infer_layout(df: pd.DataFrame) -> dict:
    """
    Legacy (T1–T5):
      0 fecha | 1 div | 2 j1 | 3 j2 | 4.. sets

    T6:
      0 fecha | 1 hora | 2 div | 3 j1 | 4 j2 | 5.. sets
    """
    has_time = False
    for i in range(min(len(df), 15)):
        try:
            v = df.iloc[i, 1]
        except Exception:
            v = None
        if _looks_like_time(v):
            has_time = True
            break

    if has_time:
        return {"date": 0, "time": 1, "div": 2, "j1": 3, "j2": 4, "sets": 5}
    return {"date": 0, "time": None, "div": 1, "j1": 2, "j2": 3, "sets": 4}


def _norm_date(ts) -> Optional[pd.Timestamp]:
    if pd.isna(ts):
        return None
    try:
        t = pd.to_datetime(ts, errors="coerce")
        if pd.isna(t):
            return None
        return t.normalize()
    except Exception:
        return None


def _row_is_unplayed(row: pd.Series, sets_start: int) -> bool:
    for i in range(sets_start, sets_start + 10):
        if i < len(row) and pd.notna(row.iloc[i]) and str(row.iloc[i]).strip() != "":
            return False
    return True


def _read_players_map(path: Path) -> Dict[str, str]:
    df = pd.read_excel(path, sheet_name=0, header=0)
    ncols = df.shape[1]

    if ncols >= 5:
        labels = ["División 1", "División 2", "División 3", "División 4 - A", "División 4 - B"]
    elif ncols == 4:
        labels = ["División 1", "División 2", "División 3 - A", "División 3 - B"]
    else:
        labels = ["División 1", "División 2", "División 3"]

    mp: Dict[str, str] = {}
    for i, lab in enumerate(labels):
        if i >= ncols:
            continue
        for v in df.iloc[:, i].dropna():
            name = _norm_name(str(v))
            if name:
                mp[name] = lab
    return mp


def _division_for_players(pmap: Dict[str, str], j1: str, j2: str) -> str:
    return pmap.get(_norm_name(j1)) or pmap.get(_norm_name(j2)) or "Desconocida"


def _build_match_link(fecha: str, division: str, j1: str, j2: str) -> str:
    token_src = f"{SEASON_NAME}|{fecha}|{division}|{j1}|{j2}|{SUBMIT_SALT}"
    mid = hashlib.sha1(token_src.encode()).hexdigest()[:16]

    params = {
        "season": SEASON_NAME,
        "date": fecha,
        "div": division,
        "j1": j1,
        "j2": j2,
        "id": mid,
    }
    return f"{SITE_BASE_URL}{SUBMIT_PATH}?{urlparse.urlencode(params)}"


def _fmt_line(fecha, hora, division, j1, j2):
    base = f"{fecha}"
    if hora:
        base += f" {hora}"
    text = f"{base} - {division} - {j1} vs {j2}"
    return f'<a href="{_build_match_link(fecha, division, j1, j2)}">{text}</a>'


# ---------------------------
# Lógica principal
# ---------------------------
def _collect_matches(today: date) -> Tuple[List[dict], List[dict]]:
    df = pd.read_excel(RESULTS_XLSX, sheet_name=0, header=0)
    layout = _infer_layout(df)
    pmap = _read_players_map(PLAYERS_XLSX)

    today_ts = pd.Timestamp(today)
    delayed, today_list = [], []

    for _, row in df.iterrows():
        fecha = _norm_date(row.iloc[layout["date"]])
        if not fecha:
            continue

        j1 = str(row.iloc[layout["j1"]]).strip()
        j2 = str(row.iloc[layout["j2"]]).strip()
        if not j1 or not j2:
            continue

        if _row_is_unplayed(row, layout["sets"]):
            div = _division_for_players(pmap, j1, j2)
            hora = ""
            if layout["time"] is not None:
                v = row.iloc[layout["time"]]
                if _looks_like_time(v):
                    try:
                        hora = pd.to_datetime(v).strftime("%H:%M")
                    except Exception:
                        hora = str(v)

            item = {
                "fecha": fecha.strftime("%Y-%m-%d"),
                "hora": hora,
                "division": div,
                "j1": j1,
                "j2": j2,
            }

            if fecha == today_ts:
                today_list.append(item)
            elif fecha < today_ts:
                delayed.append(item)

    delayed.sort(key=lambda x: (x["fecha"], x["hora"]))
    today_list.sort(key=lambda x: (x["fecha"], x["hora"]))
    return delayed, today_list


def _send_telegram(html: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )


def run_once(today: Optional[date] = None):
    t = today or date.today()
    delayed, today_list = _collect_matches(t)

    if delayed:
        msg = "📋 <b>PARTIDOS RETRASADOS</b>:\n" + "\n".join(
            _fmt_line(m["fecha"], m["hora"], m["division"], m["j1"], m["j2"])
            for m in delayed
        )
    else:
        msg = "📋 <b>PARTIDOS RETRASADOS</b>:\n(no hay)"
    _send_telegram(msg)

    if today_list:
        msg = f"📅 <b>PARTIDOS DE HOY ({t.isoformat()})</b>:\n" + "\n".join(
            _fmt_line(m["fecha"], m["hora"], m["division"], m["j1"], m["j2"])
            for m in today_list
        )
    else:
        msg = f"📅 <b>PARTIDOS DE HOY ({t.isoformat()})</b>:\n(no hay)"
    _send_telegram(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        while True:
            run_once()
            time.sleep(60 * 60 * 24)


if __name__ == "__main__":
    main()
