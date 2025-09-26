from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .parser import LeagueData

app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

LEAGUE = LeagueData()

@app.on_event("startup")
def _startup():
    # Lee todos los pares JUGADORES/RESULTADOS de la carpeta raíz
    LEAGUE.load_from_root_excels(".")

@app.get("/", response_class=HTMLResponse)
def index():
    return RedirectResponse("/standings")

@app.get("/reload", response_class=HTMLResponse)
def reload_data():
    LEAGUE.load_from_root_excels(".")
    return RedirectResponse("/standings")

@app.get("/standings", response_class=HTMLResponse)
def standings(request: Request, season: str | None = None, division: str | None = None):
    # Normaliza: si season es "", trátalo como None y borra división residual
    season = season or None
    if season is None:
        division = None

    seasons = LEAGUE.seasons_sorted()
    divisions = LEAGUE.divisions_by_season.get(season, []) if season else []

    # Si hay temporada pero no división, por defecto División 1
    if season and not division and divisions:
        division = divisions[0]

    rows = LEAGUE.standings_for(season, division)

    return templates.TemplateResponse("standings.html", {
        "request": request,
        "season": season,
        "division": division,
        "seasons": seasons,
        "divisions": divisions,
        "rows": rows
    })

@app.get("/results", response_class=HTMLResponse)
def results(request: Request, season: str | None = None, division: str | None = None):
    seasons = LEAGUE.seasons_sorted()

    # Si no se pasa temporada, toma la más reciente (última)
    if not season and seasons:
        season = seasons[-1]

    divisions = LEAGUE.divisions_by_season.get(season, []) if season else []
    rows = LEAGUE.results_for(season, division)

    return templates.TemplateResponse("results.html", {
        "request": request,
        "season": season,
        "division": division,
        "seasons": seasons,
        "divisions": divisions,
        "rows": rows
    })



