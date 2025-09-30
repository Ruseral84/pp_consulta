# app/extras.py
from __future__ import annotations
import os
import math
import pandas as pd
from datetime import datetime, date

def _safe_str(x) -> str:
    if isinstance(x, str):
        return x.strip()
    if pd.isna(x):
        return ""
    return str(x).strip()

def _load_players_map(base_dir: str) -> dict[str, str]:
    """
    Construye un diccionario jugador -> división para T5,
    leyendo JUGADORES T5.xlsx por posiciones de columna:
      Col 1 => División 1
      Col 2 => División 2
      Col 3 => División 3 - A
      Col 4 => División 3 - B (si existe)
    """
    path = os.path.join(base_dir, "JUGADORES T5.xlsx")
    # Leemos sin cabeceras para trabajar por índice de columna (0-based)
    df = pd.read_excel(path, header=None)

    div_names = {
        0: "División 1",
        1: "División 2",
        2: "División 3 - A",
        3: "División 3 - B",
    }

    player_to_div: dict[str, str] = {}
    for col_idx, div_label in div_names.items():
        if col_idx >= df.shape[1]:
            # no hay esa columna en el Excel: seguir
            continue
        for val in df.iloc[:, col_idx].dropna():
            name = _safe_str(val)
            if not name:
                continue
            # ultima palabra mayúscula puede venir con espacios: normalizamos a clave exacta
            player_to_div[name] = div_label

    return player_to_div

def _excel_date_to_date(x) -> date | None:
    """
    Convierte lo que venga en la Col1 (fecha) a date (sin hora).
    Admite datetime, date o string. Si no puede, devuelve None.
    """
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, (int, float)) and not math.isnan(x):
        # Algunas veces Excel deja números con formato de fecha; intentamos usar pandas to_datetime
        try:
            return pd.to_datetime(x, origin='1899-12-30', unit='D').date()
        except Exception:
            pass
    s = _safe_str(x)
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    # último intento genérico
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return None

def _row_has_result(row) -> bool:
    """
    Consideramos 'jugado' si hay al menos un número en las columnas de puntos (Set1..Set5).
    RESULTADOS T5:
      Col1: Fecha
      Col3: J1
      Col4: J2
      Col5..Col14: Puntos set (J1/J2 alternando)
    """
    # Índices pandas 0-based: col5 == index 4; col14 == index 13
    for idx in range(4, 14):
        val = row.iloc[idx] if idx < len(row) else None
        if isinstance(val, (int, float)) and not math.isnan(val):
            return True
        # A veces pueden venir strings numéricos
        sval = _safe_str(val)
        if sval.isdigit():
            return True
    return False

def _detect_division(j1: str, j2: str, pmap: dict[str, str]) -> str:
    """
    Determina la división del partido mirando el diccionario jugador->división.
    Si no encuentra a uno, intenta con el otro. Si ninguno, 'División ?'.
    """
    d1 = pmap.get(j1, "")
    d2 = pmap.get(j2, "")
    if d1 and d2 and d1 != d2:
        # Si por algún motivo los tuviera distintos, preferimos d1
        return d1
    return d1 or d2 or "División ?"

def get_today_and_delayed_matches(base_dir: str):
    """
    Lee RESULTADOS T5.xlsx y devuelve:
      - today_matches: partidos de HOY y SIN resultado
      - delayed_matches: partidos con fecha < HOY y SIN resultado
    Campos de salida (diccionarios): fecha (dd/mm/yyyy), division, j1, j2
    """
    # Mapa jugador->división
    pmap = _load_players_map(base_dir)

    # Cargar resultados T5 por posiciones de columna (sin header)
    res_path = os.path.join(base_dir, "RESULTADOS T5.xlsx")
    df = pd.read_excel(res_path, header=None)

    today = date.today()
    today_matches = []
    delayed_matches = []

    for _, row in df.iterrows():
        # Col1 -> fecha (index 0)
        fecha = _excel_date_to_date(row.iloc[0] if len(row) > 0 else None)
        # Col3 -> j1 (index 2), Col4 -> j2 (index 3)
        j1 = _safe_str(row.iloc[2] if len(row) > 2 else "")
        j2 = _safe_str(row.iloc[3] if len(row) > 3 else "")

        if not fecha or not j1 or not j2:
            continue

        played = _row_has_result(row)
        if played:
            # Solo queremos los no jugados
            continue

        division = _detect_division(j1, j2, pmap)
        item = {
            "fecha": fecha.strftime("%d/%m/%Y"),
            "division": division,
            "j1": j1,
            "j2": j2,
        }

        if fecha == today:
            today_matches.append(item)
        elif fecha < today:
            delayed_matches.append(item)
        # Si fecha > hoy, son futuros, no los pide

    return today_matches, delayed_matches
