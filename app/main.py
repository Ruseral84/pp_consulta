from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .parser import LeagueData, project_root_from_this_file

BASE_DIR = project_root_from_this_file(__file__)
APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Carga todo al arrancar
LEAGUE = LeagueData(BASE_DIR)


@app.get("/")
def root():
    return RedirectResponse(url="/standings")


@app.get("/standings")
def standings(request: Request, season: str | None = None, division: str | None = None):
    seasons = LEAGUE.seasons_list()

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

    # con temporada seleccionada
    divs = LEAGUE.divisions_for(season)
    # si no nos pasan división, cojo la primera disponible
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
