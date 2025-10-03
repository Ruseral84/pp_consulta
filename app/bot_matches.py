# -*- coding: utf-8 -*-
"""
Bot de partidos (opción A):
- Se ejecuta en local.
- Lee RESULTADOS T5.xlsx de la raíz del repo.
- Deduce la división comparando nombres con JUGADORES T5.xlsx.
- Construye enlaces a /submit del servicio en Render (RENDER_BASE_URL).
- Envía dos mensajes al grupo de Telegram:
  1) PARTIDOS RETRASADOS (sin jugar, fecha < hoy)
  2) PARTIDOS DE HOY (sin jugar, fecha == hoy)
- Formato de línea: "YYYY-MM-DD - División - J1 vs J2 - <a href='...'>Introducir resultado</a>"
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

# NOTE: El envío a Telegram usa la librería "python-telegram-bot" v13 (sin asyncio)
# Si prefieres "telegram" de "python-telegram-bot", este import funciona:
from telegram import Bot  # type: ignore

# Import interno para construir enlaces firmados a /submit en Render
from .submissions import build_submit_link


# --------------------------------------------------------------------------------------
# Rutas y configuración
# --------------------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent  # raíz del proyecto (donde viven los Excel)
# .env dentro de app/ (como en tu proyecto)
load_dotenv(APP_DIR / ".env")

# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
# Base URL del servicio en Render (requerida para construir los enlaces que se envían)
RENDER_BASE_URL = os.getenv("RENDER_BASE_URL", "").rstrip("/")

# Ficheros de datos (en la raíz)
RESULTADOS_XLSX = ROOT_DIR / "RESULTADOS T5.xlsx"
JUGADORES_XLSX = ROOT_DIR / "JUGADORES T5.xlsx"

# Validación mínima de entorno (no abortamos del todo: el bot puede imprimir por consola)
def _warn_env():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en app/.env (se imprimirá por consola).")
    if not RENDER_BASE_URL:
        print("⚠️ Falta RENDER_BASE_URL en app/.env (no se podrán construir enlaces correctos a Render).")


# --------------------------------------------------------------------------------------
# Modelos de datos
# --------------------------------------------------------------------------------------

@dataclass
class Match:
    dt: date
    division: str
    j1: str
    j2: str
    # Resultado presente si hay al menos un set con puntos; para "no jugado" exigimos que TODOS estén vacíos
    sets: List[Tuple[Optional[int], Optional[int]]]  # [(s1j1,s1j2), (s2j1,s2j2), ...]


# --------------------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------------------

def _to_date(x) -> Optional[date]:
    """Convierte una celda a date o None."""
    if pd.isna(x):
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        # Si viene como string "YYYY-MM-DD" u otro formato reconocible
        return pd.to_datetime(x).date()
    except Exception:
        return None


def _clean_name(x) -> str:
    """Limpia un nombre (string) retirando espacios y normalizando NaN."""
    if pd.isna(x):
        return ""
    s = str(x).strip()
    return s


def _all_sets_empty(sets: List[Tuple[Optional[int], Optional[int]]]) -> bool:
    """Devuelve True si TODOS los sets están vacíos (ningún punto informado)."""
    for a, b in sets:
        if pd.notna(a) and a not in ("", None) and str(a).strip() != "":
            return False
        if pd.notna(b) and b not in ("", None) and str(b).strip() != "":
            return False
    return True


def _read_jugadores_div_map(path: Path) -> Dict[str, str]:
    """
    Lee JUGADORES T5.xlsx, asumiendo:
      - Columna 1 => División 1
      - Columna 2 => División 2
      - Columna 3 => División 3 - A
      - Columna 4 => División 3 - B (si existe)
    Devuelve un dict nombre->division_label
    """
    if not path.exists():
        print(f"⚠️ No existe {path.name}, no se podrá deducir división por jugadores.")
        return {}

    # Leemos sin forzar encabezados para ser robustos
    df = pd.read_excel(path, header=None)
    df = df.fillna("")

    div_labels = {
        0: "División 1",
        1: "División 2",
        2: "División 3 - A",
        3: "División 3 - B",
    }

    mapping: Dict[str, str] = {}
    max_col = min(len(df.columns), 4)
    for c in range(max_col):
        label = div_labels.get(c)
        if not label:
            continue
        col_vals = df.iloc[:, c].astype(str).str.strip()
        for name in col_vals:
            n = name.strip()
            if n and n.lower() != "nan":
                mapping[n] = label
    return mapping


def _deduce_division(j1: str, j2: str, player2div: Dict[str, str]) -> str:
    """Deducción simple: prioriza la división donde esté J1; si no, la de J2; si no, 'Desconocida'."""
    d1 = player2div.get(j1, "")
    d2 = player2div.get(j2, "")
    if d1:
        return d1
    if d2:
        return d2
    return "Desconocida"


# --------------------------------------------------------------------------------------
# Lectura del Excel de RESULTADOS T5.xlsx
# --------------------------------------------------------------------------------------

def _read_resultados(path: Path, player2div: Dict[str, str]) -> List[Match]:
    """
    Resultado T5: Formato fijo, SIEMPRE el mismo:

    Col 0: Fecha
    Col 2: Jugador 1
    Col 3: Jugador 2
    Col 4: S1-J1
    Col 5: S1-J2
    Col 6: S2-J1
    Col 7: S2-J2
    Col 8: S3-J1
    Col 9: S3-J2
    Col 10: S4-J1
    Col 11: S4-J2
    Col 12: S5-J1
    Col 13: S5-J2
    """
    if not path.exists():
        print(f"❌ No se encontró {path}")
        return []

    # Leemos sin encabezados para seguir el índice por posición
    df = pd.read_excel(path, header=None)
    rows: List[Match] = []

    for _, row in df.iterrows():
        fecha = _to_date(row.iloc[0])  # Col 0
        j1 = _clean_name(row.iloc[2])  # Col 2
        j2 = _clean_name(row.iloc[3])  # Col 3

        # Saltar filas vacías o sin jugadores
        if not j1 and not j2:
            continue
        if not fecha:
            # Si no hay fecha, no es un partido válido
            continue

        # Sets (5 como máximo)
        sets: List[Tuple[Optional[int], Optional[int]]] = []
        # Pares (4,5), (6,7), (8,9), (10,11), (12,13)
        for base in (4, 6, 8, 10, 12):
            if base + 1 >= len(row):
                break
            s_j1 = row.iloc[base]
            s_j2 = row.iloc[base + 1]
            # Normalizamos a enteros o None
            try:
                a = int(s_j1) if pd.notna(s_j1) and str(s_j1).strip() != "" else None
            except Exception:
                a = None
            try:
                b = int(s_j2) if pd.notna(s_j2) and str(s_j2).strip() != "" else None
            except Exception:
                b = None
            sets.append((a, b))

        division = _deduce_division(j1, j2, player2div)
        rows.append(Match(dt=fecha, division=division, j1=j1, j2=j2, sets=sets))

    return rows


def _split_matches(matches: List[Match], today: date) -> Tuple[List[Match], List[Match]]:
    """
    Devuelve (retrasados_no_jugados, hoy_no_jugados)
    - no jugado => todos los sets vacíos
    """
    delayed: List[Match] = []
    today_list: List[Match] = []

    for m in matches:
        if not _all_sets_empty(m.sets):
            # Tiene algún set informado -> no es "sin jugar"
            continue

        if m.dt < today:
            delayed.append(m)
        elif m.dt == today:
            today_list.append(m)

    # Ordenamos por fecha y nombre para consistencia
    delayed.sort(key=lambda x: (x.dt, x.division, x.j1, x.j2))
    today_list.sort(key=lambda x: (x.dt, x.division, x.j1, x.j2))
    return delayed, today_list


# --------------------------------------------------------------------------------------
# Mensajes y envío a Telegram
# --------------------------------------------------------------------------------------

def _fmt_line_with_link(m: Match) -> str:
    """
    Formato de línea:
    YYYY-MM-DD - División - J1 vs J2 - <a href="...">Introducir resultado</a>
    """
    # Construimos enlace a Render con token/params
    if not RENDER_BASE_URL:
        # Si no hay base url, devolvemos sin link (pero indicándolo)
        return f"{m.dt} - {m.division} - {m.j1} vs {m.j2} (sin enlace: falta RENDER_BASE_URL)"

    # Usamos la temporada fija "Temporada 5" (si quieres dinámico, cámbialo)
    submit_url = build_submit_link(
        season="Temporada 5",
        date=m.dt.isoformat(),
        division=m.division,
        j1=m.j1,
        j2=m.j2,
        base_url=RENDER_BASE_URL,
    )

    return f'{m.dt} - {m.division} - {m.j1} vs {m.j2} - <a href="{submit_url}">Introducir resultado</a>'


def _build_messages(delayed: List[Match], today_list: List[Match]) -> Tuple[str, str]:
    """Construye los dos mensajes HTML."""
    # Retrasados
    header1 = "📋 <b>PARTIDOS RETRASADOS</b>:\n"
    if delayed:
        body1 = "\n".join(_fmt_line_with_link(m) for m in delayed)
    else:
        body1 = "(no hay)"
    msg1 = f"{header1}{body1}"

    # Hoy
    header2 = f"\n📅 <b>PARTIDOS DE HOY ({date.today().isoformat()})</b>:\n"
    if today_list:
        body2 = "\n".join(_fmt_line_with_link(m) for m in today_list)
    else:
        body2 = "(no hay)"
    msg2 = f"{header2}{body2}"

    return msg1, msg2


def _send_telegram(text: str) -> None:
    """Envía un mensaje (HTML) al chat configurado. Si faltan credenciales, lo imprime."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="HTML")


# --------------------------------------------------------------------------------------
# Flujo principal
# --------------------------------------------------------------------------------------

def run_once(dry_run: bool = False) -> None:
    _warn_env()

    if not RESULTADOS_XLSX.exists():
        print(f"❌ No se encontró el Excel de resultados: {RESULTADOS_XLSX}")
        return

    # Construimos mapa jugador->división con JUGADORES T5.xlsx
    player2div = _read_jugadores_div_map(JUGADORES_XLSX)

    # Cargamos partidos de RESULTADOS T5.xlsx
    all_matches = _read_resultados(RESULTADOS_XLSX, player2div)

    # Particionamos en retrasados (sin jugar) y hoy (sin jugar)
    delayed, today_list = _split_matches(all_matches, today=date.today())

    # Construimos los mensajes
    msg1, msg2 = _build_messages(delayed, today_list)

    # Enviamos o imprimimos
    if dry_run or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(msg1)
        print(msg2)
    else:
        _send_telegram(msg1)
        _send_telegram(msg2)


def main():
    parser = argparse.ArgumentParser(description="Bot de partidos (opción A: enlaces a Render).")
    parser.add_argument("--once", action="store_true", help="Ejecutar una vez y terminar.")
    parser.add_argument("--dry-run", action="store_true", help="No envía a Telegram, solo imprime.")
    args = parser.parse_args()

    if args.once:
        run_once(dry_run=args.dry_run)
        return

    # Si en el futuro quieres modo daemon con schedule, podrías usar 'schedule' o 'APScheduler' aquí.
    # Por ahora, solo --once / --dry-run.
    print("Ejecuta con --once para mandarlo ahora. (Modo daemon no implementado).")


if __name__ == "__main__":
    main()
