from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import math
import pandas as pd


# ------------------------- utilidades de ruta ------------------------- #
def project_root_from_this_file(this_file: str) -> Path:
    """Carpeta del repo: .../pp_consulta"""
    return Path(this_file).resolve().parent.parent


def _clean_name(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip()
    # coladas típicas de excel como 'nan', etc.
    return "" if s.lower() in {"nan", "none"} else s


def _is_number(x) -> bool:
    try:
        # NaN -> False
        return x == x and isinstance(float(x), (int, float))
    except Exception:
        return False


# ----------------------------- datos base ----------------------------- #
DIV1 = "División 1"
DIV2 = "División 2"
DIV3A = "División 3 - A"
DIV3B = "División 3 - B"

SeasonLabel = str  # "Temporada 1", "Temporada 2", ...


@dataclass
class MatchRow:
    date_str: str
    division: str
    name1: str
    name2: str
    # parciales (pueden venir vacíos)
    s: List[Tuple[Optional[int], Optional[int]]] = field(default_factory=list)

    @property
    def result_sets(self) -> str:
        """Devuelve '3–1' o '' si no hay sets válidos."""
        s1 = s2 = 0
        any_valid = False
        for a, b in self.s:
            if _is_number(a) and _is_number(b):
                any_valid = True
                if int(a) > int(b):
                    s1 += 1
                elif int(b) > int(a):
                    s2 += 1
        return f"{s1}–{s2}" if any_valid else ""

    def as_dict(self) -> Dict:
        # Devuelvo varios alias para que cualquier plantilla los pinte
        d = {
            "date": self.date_str,
            "division": self.division,
            "result_sets": self.result_sets,
            # nombres con alias
            "name1": self.name1,
            "name2": self.name2,
            "j1": self.name1,
            "j2": self.name2,
            "player1": self.name1,
            "player2": self.name2,
            "jugador1": self.name1,
            "jugador2": self.name2,
        }
        # S1-J1, S1-J2, ..., S5-J2
        for i, (a, b) in enumerate(self.s, start=1):
            d[f"s{i}_j1"] = "" if not _is_number(a) else int(a)
            d[f"s{i}_j2"] = "" if not _is_number(b) else int(b)
        return d


@dataclass
class PlayerAgg:
    played: int = 0
    wins: int = 0
    sets_for: int = 0
    sets_against: int = 0
    points_for: int = 0
    points_against: int = 0
    award_points: int = 0  # PUNTOS LIGA (históricos)


class LeagueData:
    """
    Carga los excels por **posición fija**, como acordamos:

    RESULTADOS Tx:
      col0: fecha
      col2: jugador 1
      col3: jugador 2
      col4..13: S1-J1, S1-J2, S2-J1, S2-J2, S3-J1, S3-J2, S4-J1, S4-J2, S5-J1, S5-J2

    JUGADORES Tx:
      col0: División 1
      col1: División 2
      col2: División 3 - A  (si existe)
      col3: División 3 - B  (si existe)
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._seasons: List[SeasonLabel] = []
        # roster por temporada
        self.roster: Dict[SeasonLabel, Dict[str, List[str]]] = {}
        # resultados por temporada
        self.results: Dict[SeasonLabel, List[MatchRow]] = {}

        self._discover_seasons()
        for s in self._seasons:
            self._load_players(s)
            self._load_results(s)

        # pre-cálculo para histórica
        self._historic: Dict[str, PlayerAgg] = {}
        self._compute_historic_points()

    # -------------------- descubrimiento y carga -------------------- #
    def _discover_seasons(self):
        jug = sorted(self.base_dir.glob("JUGADORES T*.xlsx"))
        res = sorted(self.base_dir.glob("RESULTADOS T*.xlsx"))

        nums = set()
        for p in jug + res:
            # "JUGADORES T5.xlsx" -> 5
            digits = "".join(ch for ch in p.stem if ch.isdigit())
            if digits:
                nums.add(int(digits))
        self._seasons = [f"Temporada {n}" for n in sorted(nums)]

    def _load_players(self, season: SeasonLabel):
        n = int(season.split()[-1])
        path = self.base_dir / f"JUGADORES T{n}.xlsx"
        df = pd.read_excel(path, header=None)

        divs: Dict[str, List[str]] = {
            DIV1: [],
            DIV2: [],
        }
        # col0 -> DIV1, col1 -> DIV2
        if df.shape[1] >= 1:
            divs[DIV1] = [_clean_name(x) for x in df.iloc[:, 0].dropna().tolist()]
            divs[DIV1] = [x for x in divs[DIV1] if x]
        if df.shape[1] >= 2:
            divs[DIV2] = [_clean_name(x) for x in df.iloc[:, 1].dropna().tolist()]
            divs[DIV2] = [x for x in divs[DIV2] if x]

        # col2/col3 -> grupos de 3ª
        if df.shape[1] >= 3:
            divs[DIV3A] = [_clean_name(x) for x in df.iloc[:, 2].dropna().tolist()]
            divs[DIV3A] = [x for x in divs[DIV3A] if x]
        if df.shape[1] >= 4:
            divs[DIV3B] = [_clean_name(x) for x in df.iloc[:, 3].dropna().tolist()]
            divs[DIV3B] = [x for x in divs[DIV3B] if x]

        self.roster[season] = divs

    def _division_of(self, season: SeasonLabel, j1: str, j2: str) -> str:
        divs = self.roster.get(season, {})
        for div_name, players in divs.items():
            if j1 in players or j2 in players:
                return div_name
        return "Desconocida"

    def _load_results(self, season: SeasonLabel):
        n = int(season.split()[-1])
        path = self.base_dir / f"RESULTADOS T{n}.xlsx"
        df = pd.read_excel(path, header=None)

        rows: List[MatchRow] = []
        for _, r in df.iterrows():
            date_cell = r.iloc[0] if df.shape[1] > 0 else ""
            # fecha presentada solo como AAAA-MM-DD (sin hora)
            if isinstance(date_cell, pd.Timestamp):
                date_str = str(date_cell.date())
            else:
                date_str = str(date_cell).split()[0].strip()

            name1 = _clean_name(r.iloc[2] if df.shape[1] > 2 else "")
            name2 = _clean_name(r.iloc[3] if df.shape[1] > 3 else "")
            # 10 celdas de sets a partir de col4 (S1-J1..S5-J2)
            sets: List[Tuple[Optional[int], Optional[int]]] = []
            for k in range(5):
                a = r.iloc[4 + 2 * k] if df.shape[1] > (4 + 2 * k) else None
                b = r.iloc[5 + 2 * k] if df.shape[1] > (5 + 2 * k) else None
                a = int(a) if _is_number(a) else None
                b = int(b) if _is_number(b) else None
                sets.append((a, b))

            div_name = self._division_of(season, name1, name2)
            rows.append(MatchRow(date_str=date_str, division=div_name, name1=name1, name2=name2, s=sets))

        # quito filas que no tengan nombres (líneas en blanco)
        rows = [m for m in rows if m.name1 or m.name2]
        # orden por fecha asc
        rows.sort(key=lambda m: (m.date_str, m.division, m.name1, m.name2))
        self.results[season] = rows

    # --------------------------- consultas --------------------------- #
    def seasons_list(self) -> List[SeasonLabel]:
        return list(self._seasons)

    def divisions_for(self, season: SeasonLabel) -> List[str]:
        divs = list(self.roster.get(season, {}).keys())
        # orden fijo
        order = {DIV1: 1, DIV2: 2, DIV3A: 3, DIV3B: 4}
        return sorted(divs, key=lambda d: order.get(d, 99))

    def results_for(self, season: SeasonLabel) -> List[Dict]:
        return [m.as_dict() for m in self.results.get(season, [])]

    # -------------------- standings y puntos liga -------------------- #
    def _calc_standings_one(self, season: SeasonLabel, division: str) -> List[Tuple[str, PlayerAgg]]:
        """Stats por jugador en una temporada/división (solo partidos con sets válidos).
        **Incluye también a los jugadores que aún no han disputado partidos** (PJ=0),
        para que el listado tenga siempre a todos los del roster.
        """
        agg: Dict[str, PlayerAgg] = {}

        # 1) Sembrar con todos los jugadores del roster de esa división
        players_in_div = self.roster.get(season, {}).get(division, [])
        for p in players_in_div:
            if p and p not in agg:
                agg[p] = PlayerAgg()

        # 2) Volcar los partidos con sets válidos
        for m in self.results.get(season, []):
            if m.division != division:
                continue
            # ¿hay al menos un set con números?
            has_valid = any(_is_number(a) and _is_number(b) for a, b in m.s)
            if not has_valid:
                continue

            for name in (m.name1, m.name2):
                if name and name not in agg:
                    agg[name] = PlayerAgg()

            # sets y puntos
            s1 = s2 = 0
            p1 = p2 = 0
            for a, b in m.s:
                if _is_number(a) and _is_number(b):
                    a = int(a)
                    b = int(b)
                    p1 += a
                    p2 += b
                    if a > b:
                        s1 += 1
                    elif b > a:
                        s2 += 1

            # PJ + V (victoria por más sets)
            if m.name1:
                agg[m.name1].played += 1
                agg[m.name1].wins += 1 if s1 > s2 else 0
                agg[m.name1].sets_for += s1
                agg[m.name1].sets_against += s2
                agg[m.name1].points_for += p1
                agg[m.name1].points_against += p2

            if m.name2:
                agg[m.name2].played += 1
                agg[m.name2].wins += 1 if s2 > s1 else 0
                agg[m.name2].sets_for += s2
                agg[m.name2].sets_against += s1
                agg[m.name2].points_for += p2
                agg[m.name2].points_against += p1

        def sort_key(it):
            name, a = it
            return (-a.wins, -(a.sets_for - a.sets_against), -a.points_for, name.lower())

        return sorted(agg.items(), key=sort_key)

    @staticmethod
    def _award_series_for_division(division: str, n_players: int) -> List[int]:
        if division == DIV1:
            start = 34
        elif division == DIV2:
            start = 20
        else:  # DIV3-A / DIV3-B
            start = 10
        # descendente 1 en 1
        return [start - i for i in range(n_players)]

    def _compute_historic_points(self):
        """Calcula award_points por temporada/división y acumula todo."""
        hist: Dict[str, PlayerAgg] = {}

        for season in self._seasons:
            for division in self.divisions_for(season):
                rows = self._calc_standings_one(season, division)
                if not rows:
                    continue
                awards = self._award_series_for_division(division, len(rows))

                for pos, (name, a) in enumerate(rows, start=1):
                    if name not in hist:
                        hist[name] = PlayerAgg()
                    h = hist[name]
                    # acumulo stats básicos
                    h.played += a.played
                    h.wins += a.wins
                    h.sets_for += a.sets_for
                    h.sets_against += a.sets_against
                    h.points_for += a.points_for
                    h.points_against += a.points_against
                    # y puntos de liga de esa posición
                    h.award_points += awards[pos - 1] if pos - 1 < len(awards) else 0

        self._historic = hist

    # públicos para la web
    def standings_division(self, season: SeasonLabel, division: str) -> List[Dict]:
        rows = self._calc_standings_one(season, division)
        out = []
        for name, a in rows:
            out.append(
                {
                    "player": name,
                    "played": a.played,
                    "wins": a.wins,
                    "sets_for": a.sets_for,
                    "sets_against": a.sets_against,
                    "points_for": a.points_for,
                    "points_against": a.points_against,
                }
            )
        return out

    def standings_general(self) -> List[Dict]:
        """Tabla general histórica, ORDENADA por award_points y después por desempates."""
        def sort_key(item):
            name, a = item
            return (-a.award_points, -a.wins, -(a.sets_for - a.sets_against), -a.points_for, name.lower())

        rows = sorted(self._historic.items(), key=sort_key)
        out = []
        for name, a in rows:
            out.append(
                {
                    "player": name,
                    "played": a.played,
                    "wins": a.wins,
                    "sets_for": a.sets_for,
                    "sets_against": a.sets_against,
                    "points_for": a.points_for,
                    "points_against": a.points_against,
                    "award_points": a.award_points,  # <<<<<<<<<<<<<<<<<<<<<<  PUNTOS LIGA
                }
            )
        return out
