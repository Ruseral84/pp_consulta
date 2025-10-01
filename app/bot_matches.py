# app/bot_matches.py
import os
import argparse
import hashlib
import time
from datetime import datetime, date
import urllib.parse as urlparse

import pandas as pd
import schedule
import requests
from dotenv import load_dotenv

# =========================
# Configuración y utilidades
# =========================

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SITE_BASE_URL    = os.getenv("SITE_BASE_URL", "").rstrip("/")   # p.ej. https://pp-consulta.onrender.com
SEASON_NAME      = os.getenv("SEASON_NAME", "Temporada 5")
SUBMIT_SALT      = os.getenv("SUBMIT_SALT", "pp-at3w-salt")     # opcional, solo para generar un id reproducible

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
RESULTS_FILE = os.path.join(BASE_DIR, "..", "RESULTADOS T5.xlsx")


def _norm_date(ts) -> pd.Timestamp | None:
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


def _is_unplayed(row) -> bool:
    """Devuelve True si el partido no tiene ningún set informado."""
    if len(row) <= 4:
        return True
    # Si hay algún valor numérico o no vacío a partir de la col 4, lo consideramos 'jugado'
    for x in row[4:]:
        if pd.notna(x) and str(x).strip() != "":
            return False
    return True


def _build_submit_link(date_str: str, division: str, j1: str, j2: str) -> str:
    """
    Construye el enlace para introducir resultados.
    GET /submit-result?season=...&date=...&div=...&j1=...&j2=...&id=...
    """
    if not SITE_BASE_URL:
        return ""  # si no hay base URL, no enlazamos

    # ID reproducible basado en los datos del partido + salt
    token_src = f"{SEASON_NAME}|{date_str}|{division}|{j1}|{j2}|{SUBMIT_SALT}"
    match_id = hashlib.sha1(token_src.encode("utf-8")).hexdigest()[:16]

    params = {
        "season": SEASON_NAME,
        "date": date_str,
        "div": division,
        "j1": j1,
        "j2": j2,
        "id": match_id,
    }
    query = urlparse.urlencode(params, doseq=False, safe=" ")
    return f"{SITE_BASE_URL}/submit-result?{query}"


def _fmt_line_link(date_str: str, division: str, j1: str, j2: str) -> str:
    """
    Devuelve la línea formateada como enlace clicable para Telegram (parse_mode=HTML).
    """
    href = _build_submit_link(date_str, division, j1, j2)
    text = f"{date_str} - {division} - {j1} vs {j2}"
    if href:
        # Toda la línea clicable
        return f"<a href=\"{href}\">{text}</a>"
    return text


def send_telegram_message(message: str):
    """Enviar mensaje al grupo de Telegram (HTML)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, data=payload, timeout=15)
        if r.status_code != 200:
            print(f"⚠️ Error Telegram {r.status_code}: {r.text}")
    except Exception as e:
        print(f"⚠️ Error enviando a Telegram: {e}")


# =========================
# Núcleo del bot
# =========================

def build_messages():
    """
    Lee el Excel y construye los dos mensajes (retrasados y hoy) con enlaces clicables.
    Retorna (msg_retrasados, msg_hoy).
    """
    # Leemos tal cual, sin cabeceras (formato fijo por columnas)
    df = pd.read_excel(RESULTS_FILE, header=None)

    today_ts  = pd.Timestamp(date.today())
    today_str = today_ts.strftime("%Y-%m-%d")

    partidos_hoy = []
    partidos_retrasados = []

    for _, row in df.iterrows():
        fecha = _norm_date(row[0])
        if not fecha:
            continue

        division = str(row[1]).strip() if len(row) > 1 and pd.notna(row[1]) else "Desconocida"
        j1 = str(row[2]).strip() if len(row) > 2 and pd.notna(row[2]) else "?"
        j2 = str(row[3]).strip() if len(row) > 3 and pd.notna(row[3]) else "?"

        if _is_unplayed(row):
            if fecha == today_ts:
                partidos_hoy.append(_fmt_line_link(today_str, division, j1, j2))
            elif fecha < today_ts:
                partidos_retrasados.append(_fmt_line_link(fecha.strftime("%Y-%m-%d"), division, j1, j2))

    # Mensajes HTML
    msg_retrasados = "📋 <b>PARTIDOS RETRASADOS</b>:\n" + ("\n".join(partidos_retrasados) if partidos_retrasados else "Ninguno")
    msg_hoy        = f"📅 <b>PARTIDOS DE HOY ({today_str})</b>:\n" + ("\n".join(partidos_hoy) if partidos_hoy else "Ninguno")

    return msg_retrasados, msg_hoy


def check_matches():
    """Construye y envía los dos mensajes."""
    msg_retrasados, msg_hoy = build_messages()

    # Mostrar por consola
    print(msg_retrasados)
    print()
    print(msg_hoy)

    # Enviar a Telegram (si hay token / chat)
    send_telegram_message(msg_retrasados)
    send_telegram_message(msg_hoy)


def run_scheduler():
    """Programar ejecución de lunes a jueves a las 09:00."""
    schedule.every().monday.at("09:00").do(check_matches)
    schedule.every().tuesday.at("09:00").do(check_matches)
    schedule.every().wednesday.at("09:00").do(check_matches)
    schedule.every().thursday.at("09:00").do(check_matches)

    print("⏳ Bot en ejecución (lunes–jueves 09:00). Ctrl+C para salir.")
    while True:
        schedule.run_pending()
        time.sleep(30)


# =========================
# CLI
# =========================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot de partidos AT3W")
    parser.add_argument("--once", action="store_true", help="Ejecutar una vez y salir")
    args = parser.parse_args()

    if args.once:
        check_matches()
    else:
        run_scheduler()
