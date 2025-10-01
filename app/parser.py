from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any
import pandas as pd
from datetime import datetime, timedelta


# -------------------------
# Utilidades internas
# -------------------------

def _is_number(x) -> bool:
    try:
        return pd.notna(x) and str(x).strip() != "" and float(x) == float(x)
    except Exception:
        return False


def _played_row(row: pd.Series) -> bool:
    """
    Un partido cuenta como 'jugado' si existe al menos un set con tanteo numérico en ambas columnas.
    (S1-J1, S1-J2) ó (S2-J1, S2-J2) ... hasta S5.
    """
    for j1, j2 in [(4, 5), (6, 7), (8, 9), (10, 11), (12, 13)]:
        a = row.iloc[j1] if j1 < len(row) else None
        b = row.iloc[j2] if j2 < len(row) else None
        if _is_number(a) and _is_number(b):
            return True
    return False


def _tally_match(row: pd.Series) -> Optional[Tuple[str, str, int, int, int, int]]:
    """
    Devuelve (j1, j2, sets1, sets2, pts1, pts2) SOLO si el partido tiene resultado (jugado).
    """
    if not _played_row(row):
        return None

    j1 = str(row.iloc[2]).strip()
    j2 = str(row.iloc[3]).strip()

    sets1 = sets2 = pts1 = pts2 = 0

    for j1c, j2c in [(4, 5), (6, 7), (8, 9), (10, 11), (12, 13)]:
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
        # Si no se puede parsear, quita posibles horas por patrones comunes
        for sep in ("T", " "):
            if sep in s:
                return s.split(sep, 1)[0]
        return s  # último recurso (ya sin tocar)


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
                seasons.append(fname.split(".")[0].split(" ", 1)[-1].strip())  # "T5", "T4", etc.
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

        for _, row in df.iterrows():
            try:
                j1 = str(row.iloc[2]).strip()
                j2 = str(row.iloc[3]).strip()
            except Exception:
                continue

            if j1 not in players and j2 not in players:
                continue

            tallied = _tally_match(row)
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
        Devuelve filas con todas las claves que la plantilla puede usar:
        - date / fecha
        - division (si falta en Excel, se deduce por los jugadores)
        - name1/j1/jugador1/player1  y  name2/j2/jugador2/player2
        - result_sets / resumen_sets
        - s1_j1, s1_j2, ..., s5_j1, s5_j2  (y alias s1j1, s1j2, ...)
        """
        df = self._load_results_for(season_label)
        if df.empty:
            return []

        players_by_div = self._load_players_for(season_label)

        def _cell(row: pd.Series, i: int):
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
            raw_date = _cell(row, 0)
            fecha_str = _excel_date_to_str(raw_date)

            raw_div = _cell(row, 1)  # puede venir vacío o como número
            j1 = (_cell(row, 2) or "") or ""
            j2 = (_cell(row, 3) or "") or ""

            # sets
            s = [_cell(row, i) for i in range(4, 14)]
            s1j1, s1j2, s2j1, s2j2, s3j1, s3j2, s4j1, s4j2, s5j1, s5j2 = s + [None] * (10 - len(s))

            # Resumen en sets
            resumen = ""
            tall = _tally_match(row)
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
                    # si solo uno de los dos aparece en una división, usa esa
                    for div_name, lst in players_by_div.items():
                        if (j1 in lst) or (j2 in lst):
                            found = div_name
                            break
                division = found or ""

            # fila con alias de claves para que la plantilla siempre encuentre algo
            row_dict = {
                "date": fecha_str,
                "fecha": fecha_str,
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

                # alias sin guion bajo (por si alguna plantilla antigua los usa)
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
            Tn = self._label_to_Tn(season_label)  # T5
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

        mapping: Dict[str, List[str]] = {}
        names = ["División 1", "División 2", "División 3 - A", "División 3 - B", "División 3 - C"]
        for i, div_name in enumerate(names):
            if i < df.shape[1]:
                col = df.iloc[:, i].dropna().astype(str).str.strip().tolist()
                col = [x for x in col if x not in ("", "nan")]
                if col:
                    mapping[div_name] = col

        self._players_by_season[season_label] = mapping
        return mapping
