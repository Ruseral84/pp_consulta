import os, re, glob
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set

import pandas as pd
from dateutil import parser as dateparser

# -------------------- Estructuras --------------------

@dataclass
class Match:
    season: str
    division: Optional[str]  # se asigna según pertenencia de ambos jugadores
    j1: str
    j2: str
    sets: List[Tuple[int, int]]  # [(p1,p2), ...]
    date: Optional[str]

@dataclass
class PlayerStats:
    played: int = 0
    wins: int = 0
    losses: int = 0
    sets_for: int = 0
    sets_against: int = 0
    points_for: int = 0
    points_against: int = 0

# -------------------- Utilidades --------------------

SET_COLS = [("S1-J1","S1-J2"),("S2-J1","S2-J2"),("S3-J1","S3-J2"),("S4-J1","S4-J2"),("S5-J1","S5-J2")]

def _to_int(x):
    s = str(x).strip()
    if s=="" or s.lower()=="nan":
        return None
    try:
        return int(float(s.replace(",", ".")))
    except:
        return None

def _parse_date(v):
    if pd.isna(v) or str(v).strip()=="":
        return None
    try:
        return str(pd.to_datetime(v, dayfirst=True).date())
    except:
        try:
            return str(dateparser.parse(str(v), dayfirst=True).date())
        except:
            return None

# -------------------- Reglas de puntos (General) --------------------

def award_points_for_rank(division_index_1based: int, rank_1based: int) -> int:
    # 1ª: base 30 + bonus 4/2/1; 2ª: base 20; 3ª: base 10
    base_map = {1: 30, 2: 20, 3: 10}
    base = base_map.get(division_index_1based, 0)
    pts = max(0, base - (rank_1based - 1))
    if division_index_1based == 1 and rank_1based in (1,2,3):
        pts += {1:4, 2:2, 3:1}[rank_1based]
    return pts

# -------------------- Carga y cálculos --------------------

class LeagueData:
    def __init__(self):
        self.seasons: List[str] = []
        self.matches: List[Match] = []

        self.divisions_by_season: Dict[str, List[str]] = {}       # temporada -> [División 1, División 2, ...]
        self.membership_by_season: Dict[str, Dict[str, str]] = {} # temporada -> {jugador: división}

        self.standings_by_season_div: Dict[Tuple[str, str], Dict[str, PlayerStats]] = {}
        self.total_awards: Dict[str, int] = {}
        self.total_tb: Dict[str, Tuple[int,int,int]] = {}          # (W, Sets+, Pts+) acumulados

    # --- Lectura de ficheros ---

    def _load_players_file(self, path: str) -> Tuple[List[str], Dict[str,str]]:
        # Espera hoja 'Hoja1' y columnas: DIV 1, DIV 2, DIV 3
        df = pd.ExcelFile(path).parse('Hoja1')
        divisions: List[str] = []
        membership: Dict[str,str] = {}
        for col in df.columns:
            m = re.match(r'^\s*DIV\s*([123])\s*$', str(col), re.IGNORECASE)
            if not m:
                continue
            idx = int(m.group(1))
            div_name = f"División {idx}"
            if div_name not in divisions:
                divisions.append(div_name)
            for v in df[col].fillna('').astype(str):
                name = v.strip()
                if name and name.lower() != 'nan':
                    membership[name] = div_name
        return divisions, membership

    def _load_results_file(self, path: str) -> List[Match]:
        # Espera hoja 'Hoja1' con columnas J1, J2 y Sx-Jy
        df = pd.ExcelFile(path).parse('Hoja1')
        out: List[Match] = []
        for _, row in df.iterrows():
            j1 = str(row.get("J1","")).strip()
            j2 = str(row.get("J2","")).strip()
            if not j1 or not j2 or j1.lower()=="nan" or j2.lower()=="nan":
                continue
            sets=[]
            for a,b in SET_COLS:
                p1=_to_int(row.get(a)); p2=_to_int(row.get(b))
                if p1 is None and p2 is None:
                    continue
                sets.append((p1 or 0, p2 or 0))
            if not sets:
                continue
            # la fecha suele venir en la primera columna sin nombre, si no existe no pasa nada
            d = _parse_date(row.get("Unnamed: 0"))
            out.append(Match(season="", division=None, j1=j1, j2=j2, sets=sets, date=d))
        return out

    def load_from_root_excels(self, root="."):
        # Busca pares RESULTADOS Tn.xlsx / JUGADORES Tn.xlsx
        patR = os.path.join(root, "RESULTADOS T*.xlsx")
        result_paths = sorted(glob.glob(patR))
        pairs = []
        for rp in result_paths:
            m = re.search(r"T(\d+)", os.path.basename(rp))
            if not m:
                continue
            n = int(m.group(1))
            pp = os.path.join(root, f"JUGADORES T{n}.xlsx")
            if os.path.exists(pp):
                pairs.append((n, pp, rp))
        # Reset
        self.seasons.clear(); self.matches.clear()
        self.divisions_by_season.clear(); self.membership_by_season.clear()
        self.standings_by_season_div.clear(); self.total_awards.clear(); self.total_tb.clear()
        # Cargar
        for n, players_path, results_path in sorted(pairs):
            season = f"Temporada {n}"
            self.seasons.append(season)
            divs, membership = self._load_players_file(players_path)
            self.divisions_by_season[season] = divs
            self.membership_by_season[season] = membership
            # partidos
            for m in self._load_results_file(results_path):
                m.season = season
                d1 = membership.get(m.j1); d2 = membership.get(m.j2)
                if d1 and d2 and d1 == d2:
                    m.division = d1
                    self.matches.append(m)
        # Cálculos
        self._build_standings()
        self._build_total()

    # --- Cálculos de clasificación ---

    def _build_standings(self):
        self.standings_by_season_div.clear()
        for season in self.seasons:
            for div in self.divisions_by_season.get(season, []):
                table: Dict[str, PlayerStats] = defaultdict(PlayerStats)
                for m in self.matches:
                    if m.season!=season or m.division!=div:
                        continue
                    s1 = sum(1 for (a,b) in m.sets if a>b)
                    s2 = sum(1 for (a,b) in m.sets if b>a)
                    if max(s1,s2) < 3:
                        continue
                    t1, t2 = table[m.j1], table[m.j2]
                    t1.played+=1; t2.played+=1
                    if s1>s2: t1.wins+=1; t2.losses+=1
                    elif s2>s1: t2.wins+=1; t1.losses+=1
                    for (a,b) in m.sets:
                        t1.points_for+=a; t1.points_against+=b
                        t2.points_for+=b; t2.points_against+=a
                        if a>b: t1.sets_for+=1; t2.sets_against+=1
                        elif b>a: t2.sets_for+=1; t1.sets_against+=1
                self.standings_by_season_div[(season,div)] = table

    def _build_total(self):
        self.total_awards.clear(); self.total_tb.clear()
        # puntos por ranking de cada división/temporada
        for season in self.seasons:
            divs = self.divisions_by_season.get(season, [])
            for idx, div in enumerate(divs, start=1):  # 1..N
                table = self.standings_by_season_div.get((season,div), {})
                ordered = sorted(
                    table.items(),
                    key=lambda kv: (-kv[1].wins, -kv[1].sets_for, -kv[1].points_for, kv[0])
                )
                for rank, (player, st) in enumerate(ordered, start=1):
                    self.total_awards[player] = self.total_awards.get(player, 0) + award_points_for_rank(idx, rank)
        # agregados para desempate en la general
        for (_, _), table in self.standings_by_season_div.items():
            for player, st in table.items():
                w,sf,pf = self.total_tb.get(player, (0,0,0))
                self.total_tb[player] = (w+st.wins, sf+st.sets_for, pf+st.points_for)

    # --- API para la web ---

    def seasons_sorted(self) -> List[str]:
        def key(s):
            try: return int(s.split()[-1])
            except: return 999
        return sorted(self.seasons, key=key)

    def standings_for(self, season: Optional[str], division: Optional[str]):
        """Si season es falsy (None o ''), devuelve la GENERAL histórica."""
        if not season:
            rows=[]
            for player, pts in self.total_awards.items():
                w,sf,pf = self.total_tb.get(player, (0,0,0))
                rows.append((player, pts, w, sf, pf))
            rows.sort(key=lambda r: (-r[1], -r[2], -r[3], -r[4], r[0]))
            return [
                {"player": p, "played": "", "wins": w, "sets_for": sf, "sets_against": "",
                 "points_for": pf, "points_against": "", "award_points": pts}
                for (p,pts,w,sf,pf) in rows
            ]

        # temporada (con o sin división)
        merged: Dict[str, PlayerStats] = defaultdict(PlayerStats)
        divs = self.divisions_by_season.get(season, [])
        if division:
            divs = [division]
        for div in divs:
            table = self.standings_by_season_div.get((season, div), {})
            for player, st in table.items():
                m=merged[player]
                m.played+=st.played; m.wins+=st.wins; m.losses+=st.losses
                m.sets_for+=st.sets_for; m.sets_against+=st.sets_against
                m.points_for+=st.points_for; m.points_against+=st.points_against
        rows = sorted(merged.items(), key=lambda kv: (-kv[1].wins, -kv[1].sets_for, -kv[1].points_for, kv[0]))
        return [
            {"player": p, "played": st.played, "wins": st.wins,
             "sets_for": st.sets_for, "sets_against": st.sets_against,
             "points_for": st.points_for, "points_against": st.points_against}
            for (p,st) in rows
        ]

    def results_for(self, season: Optional[str], division: Optional[str] = None):
        """
        Devuelve la lista de partidos de una temporada (y, si se indica, de una división).
        Cada fila incluye: fecha, división, J1, J2, S1..S5, marcador de sets y ganador.
        """
        if not season:
            return []  # se requiere temporada para ver resultados

        out = []
        for m in self.matches:
            if m.season != season:
                continue
            if division and m.division != division:
                continue

            # normaliza a 5 sets
            s = m.sets + [(None, None)] * (5 - len(m.sets))

            # cómputo sets ganados
            s1 = sum(1 for (a, b) in m.sets if a is not None and b is not None and a > b)
            s2 = sum(1 for (a, b) in m.sets if a is not None and b is not None and b > a)
            winner = m.j1 if s1 > s2 else (m.j2 if s2 > s1 else "")

            out.append({
                "date": m.date or "",
                "division": m.division or "",
                "j1": m.j1,
                "j2": m.j2,
                "s1j1": s[0][0], "s1j2": s[0][1],
                "s2j1": s[1][0], "s2j2": s[1][1],
                "s3j1": s[2][0], "s3j2": s[2][1],
                "s4j1": s[3][0], "s4j2": s[3][1],
                "s5j1": s[4][0], "s5j2": s[4][1],
                "sets_score": f"{s1}–{s2}",
                "winner": winner,
            })

        # ordena por fecha, luego división, luego jugadores
        out.sort(key=lambda r: (r["date"] or "", r["division"], r["j1"], r["j2"]))
        return out


