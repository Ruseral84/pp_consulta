from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any
import pandas as pd
from datetime import datetime, timedelta, time as dtime


# -------------------------
# Utilidades internas
# -------------------------

def _is_number(x) -> bool:
    try:
        return pd.notna(x) and str(x).strip() != "" and float(x) == float(x)
    except Exception:
        return False


def _looks_like_time(v: Any) -> bool:
    """Detecta si un valor parece una hora (time/datetime o string tipo HH:MM)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    if isinstance(v, dtime):
        return True
    if isinstance(v, datetime):
        return True
    if isinstance(v, pd.Timestamp):
        return True
    s = str(v).strip()
    if not s:
        return False
    # casos típicos: "09:00", "9:00", "09:00:00"
    if ":" in s:
        parts = s.split(":")
        if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]):
            return True
    return False


def _infer_layout(df: pd.DataFrame) -> dict:
    """
    Devuelve índices de columnas en el Excel de resultados.

    Formato legacy (T1..T5):
      0: Fecha
      1: División (o índice)
      2: Jugador 1
      3: Jugador 2
      4..: sets (10 celdas)

    Formato T6 (nuevo):
      0: Fecha
      1: Hora
      2: División (o índice)
      3: Jugador 1
      4: Jugador 2
      5..: sets (10 celdas)

    Detectamos T6 si la columna 1 parece "hora" (en alguna de las primeras filas con datos).
    """
    has_time = False
    # buscamos un ejemplo en las primeras filas para evitar nulos
    for i in range(min(len(df), 15)):
        try:
            v = df.iloc[i, 1] if df.shape[1] > 1 else None
        except Exception:
            v = None
        if _looks_like_time(v):
            has_time = True
            break

    if has_time:
        return {"date": 0, "time": 1, "div": 2, "j1": 3, "j2": 4, "sets_start": 5}
    return {"date": 0, "time": None, "div": 1, "j1": 2, "j2": 3, "sets_start": 4}


def _played_row(row: pd.Series, sets_start: int) -> bool:
    """
    Un partido cuenta como 'jugado' si existe al menos un set con tanteo numérico en ambas columnas.
    """
    pairs = [(sets_start + i, sets_start + i + 1) for i in range(0, 10, 2)]  # 5 sets -> 10 celdas
    for j1, j2 in pairs:
        a = row.iloc[j1] if j1 < len(row) else None
        b = row.iloc[j2] if j2 < len(row) else None
        if _is_number(a) and _is_number(b):
            return True
    return False


def _tally_match(row: pd.Series, j1_idx: int, j2_idx: int, sets_start: int) -> Optional[Tuple[str, str, int, int, int, int]]:
    """
    Devuelve (j1, j2, sets1, sets2, pts1, pts2) SOLO si el partido tiene resultado (jugado).
    """
    if not _played_row(row, sets_start):
        return None

    j1 = str(row.iloc[j1_idx]).strip() if j1_idx < len(row) else ""
    j2 = str(row.iloc[j2_idx]).strip() if j2_idx < len(row) else ""
    if not j1 or not j2:
        return None

    sets1 = sets2 = pts1 = pts2 = 0

    pairs = [(sets_start + i, sets_start + i + 1) for i in range(0, 10, 2)]
    for j1c, j2c in pairs:
        a = row.iloc[j1c] if j1c < len(row) else None
        b = row.iloc[j2c] if j2c < len(row) else None
        if _is_number(a) and _is_number(b):
            a = int(float(a))
            b = int(float(b))
            pts1 += a
            pts2 += b
            if a > b:
                sets1 += 1
            elif b > a:
                sets2 += 1

    return j1, j2, sets1, sets2, pts1, pts2


def _excel_date_to_str(v: Any) -> str:
    """
    Normaliza la fecha a 'YYYY-MM-DD' desde:
    - pandas.Timestamp / datetime
    - números Excel (días desde 1899-12-30)
    - strings con o sin hora (intenta parseo y devuelve solo la fecha)
    """
    if isinstance(v, pd.Timestamp):
        return v.date().isoformat()
    if isinstance(v, datetime):
        return v.date().isoformat()

    s = str(v).strip() if pd.notna(v) else ""
    if not s:
        return ""

    # Número Excel (días desde 1899-12-30)
    if _is_number(s):
        try:
            base = datetime(1899, 12, 30)
            dt = base + timedelta(days=int(float(s)))
            return dt.date().isoformat()
        except Exception:
            pass

    # Si viene como string, intenta convertir y devolver SOLO la fecha
    try:
        dt = pd.to_datetime(s, dayfirst=False, errors="raise")
        return dt.date().isoformat()
    except Exception:
        for sep in ("T", " "):
            if sep in s:
                return s.split(sep, 1)[0]
        return s


def _excel_time_to_str(v: Any) -> str:
    """Normaliza una hora a 'HH:MM' (si no se puede, devuelve string limpio o '')."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, dtime):
        return v.strftime("%H:%M")
    if isinstance(v, pd.Timestamp):
        return v.strftime("%H:%M")
    if isinstance(v, datetime):
        return v.strftime("%H:%M")
    s = str(v).strip()
    if not s:
        return ""
    # si viene "09:00:00" => "09:00"
    if ":" in s:
        parts = s.split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    return s


@dataclass
class LeagueData:
    base_dir: str

    def __post_init__(self):
        self._players_by_season: Dict[str, Dict[str, List[str]]] = {}
        self._results_by_season: Dict[str, pd.DataFrame] = {}
        self._season_names: List[str] = []
        self._discover()

    # -------------------------
    # Descubrimiento de ficheros
    # -------------------------
    def _discover(self):
        seasons = []
        for fname in os.listdir(self.base_dir):
            low = fname.lower()
            if low.startswith("resultados t") and (low.endswith(".xlsx") or low.endswith(".xls")):
                seasons.append(fname.split(".")[0].split(" ", 1)[-1].strip())  # "T6", "T5", etc.
        seasons = sorted(seasons, key=lambda s: (len(s), s))
        self._season_names = [f"Temporada {s[1:]}" for s in seasons]

    # -------------------------
    # API PÚBLICA
    # -------------------------

    def seasons(self) -> List[str]:
        return ["(General)"] + self._season_names

    def divisions_for(self, season_label: str) -> List[str]:
        players = self._load_players_for(season_label)
        return list(players.keys())

    # ---- GENERAL (histórica)

    def general_rows(self) -> List[dict]:
        agg_points: Dict[str, int] = {}
        agg_wins: Dict[str, int] = {}

        for season_label in self._season_names:
            for division in self.divisions_for(season_label):
                table = self.season_division_table(season_label, division)

                if division.startswith("División 1"):
                    base = [34, 31, 29, 27]

                    def puntos_pos(pos):
                        if pos <= 4:
                            return base[pos - 1]
                        return 27 - (pos - 4)  # 26,25,24,...

                elif division.startswith("División 2"):
                    def puntos_pos(pos):
                        return max(20 - (pos - 1), 0)
                else:
                    def puntos_pos(pos):
                        return max(10 - (pos - 1), 0)

                for idx, row in enumerate(table, start=1):
                    name = row["jugador"]
                    pts = puntos_pos(idx)
                    agg_points[name] = agg_points.get(name, 0) + pts
                    agg_wins[name] = agg_wins.get(name, 0) + row["V"]

        rows = []
        for name, pts in agg_points.items():
            rows.append({
                "jugador": name,
                "PUNTOS_LIGA": pts,
                "V": agg_wins.get(name, 0),
            })

        rows.sort(key=lambda r: (-r["PUNTOS_LIGA"], -r["V"], r["jugador"]))
        return rows

    # ---- TABLA por temporada/división

    def season_division_table(self, season_label: str, division: str) -> List[dict]:
        players_by_div = self._load_players_for(season_label)
        if division not in players_by_div:
            return []

        players = [p for p in players_by_div[division] if p and str(p).strip() != ""]
        stats = {p: {"PJ": 0, "V": 0, "sets_f": 0, "sets_c": 0, "pts_f": 0, "pts_c": 0} for p in players}

        df = self._load_results_for(season_label)
        if df.empty:
            return []

        layout = _infer_layout(df)

        for _, row in df.iterrows():
            try:
                j1 = str(row.iloc[layout["j1"]]).strip() if layout["j1"] < len(row) else ""
                j2 = str(row.iloc[layout["j2"]]).strip() if layout["j2"] < len(row) else ""
            except Exception:
                continue

            if j1 not in players and j2 not in players:
                continue

            tallied = _tally_match(row, layout["j1"], layout["j2"], layout["sets_start"])
            if not tallied:
                continue  # NO cuenta si no está jugado

            j1n, j2n, s1, s2, p1, p2 = tallied

            if j1n in stats:
                stats[j1n]["PJ"] += 1
                stats[j1n]["sets_f"] += s1
                stats[j1n]["sets_c"] += s2
                stats[j1n]["pts_f"] += p1
                stats[j1n]["pts_c"] += p2
                if s1 > s2:
                    stats[j1n]["V"] += 1

            if j2n in stats:
                stats[j2n]["PJ"] += 1
                stats[j2n]["sets_f"] += s2
                stats[j2n]["sets_c"] += s1
                stats[j2n]["pts_f"] += p2
                stats[j2n]["pts_c"] += p1
                if s2 > s1:
                    stats[j2n]["V"] += 1

        rows = []
        for p in players:
            st = stats[p]
            rows.append({
                "jugador": p,
                "PJ": st["PJ"],
                "V": st["V"],
                "dsets": st["sets_f"] - st["sets_c"],
                "dpuntos": st["pts_f"] - st["pts_c"],
            })

        rows.sort(key=lambda r: (-r["V"], -r["dsets"], -r["dpuntos"], r["jugador"]))
        return rows

    # ---- RESULTADOS por temporada

    def results_rows(self, season_label: str) -> List[dict]:
        """
        Devuelve filas con todas las claves que la plantilla puede usar.
        Ahora incluye también:
          - hora / time (si existe columna de hora en la temporada)
        """
        df = self._load_results_for(season_label)
        if df.empty:
            return []

        layout = _infer_layout(df)
        players_by_div = self._load_players_for(season_label)

        def _cell(row: pd.Series, i: int):
            if i is None:
                return None
            if i >= len(row):
                return None
            v = row.iloc[i]
            if pd.isna(v):
                return None
            if _is_number(v):
                f = float(v)
                return int(f) if f.is_integer() else f
            return str(v).strip()

        rows: List[dict] = []

        for _, row in df.iterrows():
            raw_date = _cell(row, layout["date"])
            fecha_str = _excel_date_to_str(raw_date)

            raw_time = _cell(row, layout["time"]) if layout["time"] is not None else None
            hora_str = _excel_time_to_str(raw_time) if layout["time"] is not None else ""

            raw_div = _cell(row, layout["div"])  # puede venir vacío o como número
            j1 = (_cell(row, layout["j1"]) or "") or ""
            j2 = (_cell(row, layout["j2"]) or "") or ""

            # sets (5 sets -> 10 celdas)
            ss = []
            for i in range(layout["sets_start"], layout["sets_start"] + 10):
                ss.append(_cell(row, i))

            s1j1, s1j2, s2j1, s2j2, s3j1, s3j2, s4j1, s4j2, s5j1, s5j2 = ss + [None] * (10 - len(ss))

            # Resumen en sets
            resumen = ""
            tall = _tally_match(row, layout["j1"], layout["j2"], layout["sets_start"])
            if tall:
                _, _, ss1, ss2, _, _ = tall
                resumen = f"{ss1}-{ss2}"

            # Deducción de división si la celda no trae el nombre (o es numérica)
            division = ""
            if isinstance(raw_div, str) and raw_div and not raw_div.isdigit():
                division = raw_div
            else:
                found = None
                for div_name, lst in players_by_div.items():
                    if (j1 in lst) and (j2 in lst):
                        found = div_name
                        break
                if not found:
                    for div_name, lst in players_by_div.items():
                        if (j1 in lst) or (j2 in lst):
                            found = div_name
                            break
                division = found or ""

            row_dict = {
                "date": fecha_str,
                "fecha": fecha_str,
                "time": hora_str,
                "hora": hora_str,

                "division": division,

                "name1": j1, "j1": j1, "jugador1": j1, "player1": j1,
                "name2": j2, "j2": j2, "jugador2": j2, "player2": j2,

                "result_sets": resumen,
                "resumen_sets": resumen,

                "s1_j1": s1j1, "s1_j2": s1j2,
                "s2_j1": s2j1, "s2_j2": s2j2,
                "s3_j1": s3j1, "s3_j2": s3j2,
                "s4_j1": s4j1, "s4_j2": s4j2,
                "s5_j1": s5j1, "s5_j2": s5j2,

                "s1j1": s1j1, "s1j2": s1j2,
                "s2j1": s2j1, "s2j2": s2j2,
                "s3j1": s3j1, "s3j2": s3j2,
                "s4j1": s4j1, "s4j2": s4j2,
                "s5j1": s5j1, "s5j2": s5j2,
            }
            rows.append(row_dict)

        return rows

    # -------------------------
    # Carga de datos (excel)
    # -------------------------

    def _label_to_Tn(self, season_label: str) -> str:
        n = season_label.split()[-1]
        return f"T{n}"

    def _load_results_for(self, season_label: str) -> pd.DataFrame:
        if season_label not in self._results_by_season:
            Tn = self._label_to_Tn(season_label)  # T6, T5...
            df = None
            for ext in (".xlsx", ".xls"):
                path = os.path.join(self.base_dir, f"RESULTADOS {Tn}{ext}")
                if os.path.exists(path):
                    df = pd.read_excel(path, header=None)
                    break
            if df is None:
                for ext in (".xlsx", ".xls"):
                    path = os.path.join(self.base_dir, f"RESULTADOS {season_label}{ext}")
                    if os.path.exists(path):
                        df = pd.read_excel(path, header=None)
                        break
            if df is None:
                self._results_by_season[season_label] = pd.DataFrame()
            else:
                self._results_by_season[season_label] = df.fillna(value=pd.NA)
        return self._results_by_season[season_label]

    def _load_players_for(self, season_label: str) -> Dict[str, List[str]]:
        if season_label in self._players_by_season:
            return self._players_by_season[season_label]

        Tn = self._label_to_Tn(season_label)
        df = None
        for ext in (".xlsx", ".xls"):
            path = os.path.join(self.base_dir, f"JUGADORES {Tn}{ext}")
            if os.path.exists(path):
                df = pd.read_excel(path, header=None)
                break
        if df is None:
            for ext in (".xlsx", ".xls"):
                path = os.path.join(self.base_dir, f"JUGADORES {season_label}{ext}")
                if os.path.exists(path):
                    df = pd.read_excel(path, header=None)
                    break
        if df is None:
            self._players_by_season[season_label] = {}
            return {}

        # Elegimos etiquetas según nº de columnas:
        # - 4 cols (T5): Div1, Div2, Div3-A, Div3-B
        # - 5+ cols (T6): Div1, Div2, Div3, Div4-A, Div4-B
        # - 3 cols: Div1, Div2, Div3
        ncols = df.shape[1]
        if ncols >= 5:
            names = ["División 1", "División 2", "División 3", "División 4 - A", "División 4 - B"]
        elif ncols == 4:
            names = ["División 1", "División 2", "División 3 - A", "División 3 - B"]
        else:
            names = ["División 1", "División 2", "División 3"]

        mapping: Dict[str, List[str]] = {}
        for i, div_name in enumerate(names):
            if i < df.shape[1]:
                col = df.iloc[:, i].dropna().astype(str).str.strip().tolist()
                col = [x for x in col if x not in ("", "nan")]
                if col:
                    mapping[div_name] = col

        self._players_by_season[season_label] = mapping
        return mapping
