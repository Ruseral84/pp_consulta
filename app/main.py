from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .parser import LeagueData, project_root_from_this_file
from .submissions import router as submissions_router


# --- Rutas/paths base ---
BASE_DIR = project_root_from_this_file(__file__)
APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

# --- App & static/templates ---
app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Carga de datos de liga ---
LEAGUE = LeagueData(BASE_DIR)

# --- Router para formularios de resultados (nuevas rutas) ---
app.include_router(submissions_router)


# --- Rutas existentes ---
@app.get("/")
def root():
    # Mantengo el redirect que ya tenías
    return RedirectResponse(url="/standings")


@app.get("/standings")
def standings(request: Request, season: str | None = None, division: str | None = None):
    """
    - Sin 'season' o con '(General)': muestra la clasificación histórica general.
    - Con 'season': lista divisiones para esa temporada y muestra la tabla de la división seleccionada.
    """
    seasons = LEAGUE.seasons_list()

    # General histórica
    if not season or season == "(General)":
        rows = LEAGUE.standings_general()
        ctx = {
            "request": request,
            "season": None,
            "division": None,
            "divisions": [],
            "seasons": seasons,
            "rows": rows,
        }
        return templates.TemplateResponse("standings.html", ctx)

    # Con temporada concreta
    divs = LEAGUE.divisions_for(season)
    # Si no llega división, cogemos la primera disponible
    division = division or (divs[0] if divs else None)
    rows = LEAGUE.standings_division(season, division) if division else []

    ctx = {
        "request": request,
        "season": season,
        "division": division,
        "divisions": divs,
        "seasons": seasons,
        "rows": rows,
    }
    return templates.TemplateResponse("standings.html", ctx)


@app.get("/results")
def results(request: Request, season: str | None = None):
    """
    - Muestra resultados de la temporada seleccionada (por defecto, la primera de la lista).
    """
    seasons = LEAGUE.seasons_list()
    season = season or (seasons[0] if seasons else None)
    rows = LEAGUE.results_for(season) if season else []

    ctx = {
        "request": request,
        "season": season,
        "seasons": seasons,
        "rows": rows,
    }
    return templates.TemplateResponse("results.html", ctx)
