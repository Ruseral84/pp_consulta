from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os

from .parser import LeagueData
from .submissions import router as submissions_router


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(BASE_DIR, "app")

app = FastAPI()
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))

app.include_router(submissions_router)

# Instancia única del parser (no tocar lo demás)
LEAGUE = LeagueData(BASE_DIR)


@app.get("/")
def index():
    # Siempre arrancar en la **General**
    return RedirectResponse(url="/standings?season=(General)")


@app.get("/standings")
def standings(request: Request, season: str = "(General)", division: str | None = None):
    seasons = LEAGUE.seasons()

    rows = []
    show_general = season == "(General)"
    divisions = []

    if show_general:
        rows = LEAGUE.general_rows()
        division = ""
    else:
        divisions = LEAGUE.divisions_for(season)
        if not division and divisions:
            division = divisions[0]
        rows = LEAGUE.season_division_table(season, division) if division else []

    ctx = {
        "request": request,
        "seasons": seasons,
        "season": season,
        "divisions": divisions,
        "division": division or "",
        "rows": rows,
        "show_general": show_general,
    }
    return templates.TemplateResponse("standings.html", ctx)


@app.get("/results")
def results(request: Request, season: str | None = None):
    # Igual que antes, pero pasando 'rows' que espera la plantilla
    seasons = LEAGUE.seasons()
    if not season:
        season = seasons[-1] if len(seasons) > 1 else "(General)"

    rows = []
    # Solo hay resultados por temporada concreta
    if season != "(General)":
        rows = LEAGUE.results_rows(season)

    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "seasons": seasons,
            "season": season,
            "rows": rows,
        },
    )
