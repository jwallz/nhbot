"""NHbot web app — FastAPI + Jinja2 server-side rendering, HTMX for interaction.

Run:
    export NHBOT_DSN="dbname=nhbot"
    uvicorn nhbot.web.app:app --reload
"""
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nhbot.web import repo, mapsvg

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

app = FastAPI(title="NHbot")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

RATE_META = {
    "advertised": {"label": "Advertised rate", "blurb": "the rate on your tax bill"},
    "equalized":  {"label": "Equalized rate",  "blurb": "restated at full market value, for comparing towns"},
}

def _clean_rate(rate):
    return rate if rate in repo.METRICS else "advertised"

def _map_context(rate, year):
    values = repo.map_values(year, rate)
    svg, legend = mapsvg.choropleth(values)
    return {"svg": svg, "legend": legend, "rate": rate, "year": year,
            "rate_meta": RATE_META}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, rate: str = Query("advertised")):
    rate = _clean_rate(rate)
    year = repo.latest_year()
    return templates.TemplateResponse(request=request, name="index.html",
                                      context=_map_context(rate, year))


@app.get("/fragments/map", response_class=HTMLResponse)
def map_fragment(request: Request, rate: str = Query("advertised")):
    rate = _clean_rate(rate)
    year = repo.latest_year()
    return templates.TemplateResponse(request=request, name="partials/_map.html",
                                      context=_map_context(rate, year))


@app.get("/town/{geoid}", response_class=HTMLResponse)
def town(request: Request, geoid: str):
    m = repo.get_municipality(geoid)
    if not m:
        raise HTTPException(404, "Municipality not found")
    history = repo.rate_history(geoid)
    current = history[0] if history else None
    split = repo.tax_split(geoid, current["tax_year"]) if current else None
    return templates.TemplateResponse(request=request, name="town.html", context={
        "m": m, "history": history, "current": current, "split": split,
    })


@app.get("/compare", response_class=HTMLResponse)
def compare(request: Request,
            year: int = Query(None), county: str = Query(None),
            entity: str = Query(None), sort: str = Query("advertised"),
            dir: str = Query("desc")):
    year = year or repo.latest_year()
    ctx = {
        "years": repo.available_years(),
        "counties": repo.counties(),
        "rows": repo.compare_rows(year, county, entity, sort, dir),
        "year": year, "county": county, "entity": entity,
        "sort": sort, "dir": dir,
    }
    tmpl = "partials/_compare_rows.html" if request.headers.get("HX-Request") else "compare.html"
    return templates.TemplateResponse(request=request, name=tmpl, context=ctx)


@app.get("/about/equalized-rates", response_class=HTMLResponse)
def about_equalized(request: Request):
    return templates.TemplateResponse(request=request, name="about_equalized.html",
                                      context={})


@app.get("/healthz")
def healthz():
    return {"ok": True, "years": repo.available_years()}
