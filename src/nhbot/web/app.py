"""NHDataHub web app — FastAPI + Jinja2 server-side rendering, HTMX for interaction.

Run:
    export NHBOT_DSN="dbname=nhbot"
    uvicorn nhbot.web.app:app --reload

URLs are RESTful and town-first: the home map links to /{slug} (e.g. /amherst),
and the legacy /town/{geoid} 301-redirects there. All fixed routes are declared
before the catch-all /{slug} so they win.
"""
import os, smtplib, ssl
from email.message import EmailMessage
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nhbot.web import repo, mapsvg
from nhbot.web.db import cursor
from nhbot.web.slug import slugify

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["slugify"] = slugify   # /{{ name|slugify }} town links

def _usd(n):
    """Adaptive dollar formatting for headline/table figures."""
    try: n = float(n or 0)
    except (TypeError, ValueError): return "—"
    a = abs(n)
    if a >= 1e9: return f"${n/1e9:.2f}B"
    if a >= 1e6: return f"${n/1e6:,.0f}M"
    if a >= 1e3: return f"${n/1e3:,.0f}K"
    return f"${n:,.0f}"
templates.env.filters["usd"] = _usd

def _ordinal(n):
    try: n = int(n)
    except (TypeError, ValueError): return n
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"
templates.env.filters["ord"] = _ordinal

app = FastAPI(title="NHDataHub")
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

# advertised/equalized still power the /compare page
RATE_META = {
    "advertised": {"label": "Advertised rate", "blurb": "the rate on your tax bill"},
    "equalized":  {"label": "Equalized rate",  "blurb": "restated at full market value, for comparing towns"},
}

def _clean_rate(rate):
    return rate if rate in repo.METRICS else "advertised"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={
        "map_svg": mapsvg.town_map(),
        "towns": repo.all_towns(),          # powers the type-ahead search box
    })


@app.get("/towns", response_class=HTMLResponse)
def towns(request: Request):
    return templates.TemplateResponse(request=request, name="towns.html",
                                      context={"towns": repo.all_towns()})


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(request=request, name="about.html", context={})


@app.get("/contact", response_class=HTMLResponse)
def contact(request: Request):
    return templates.TemplateResponse(request=request, name="contact.html", context={"sent": None})


def _store_contact(name, email, category, message, ip):
    """Persist a contact message (durable record, even before SMTP is configured)."""
    with cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS nh.contact_message(
            id serial PRIMARY KEY, created_at timestamptz DEFAULT now(),
            name text, email text, category text, message text, ip text,
            emailed boolean DEFAULT false)""")
        cur.execute("""INSERT INTO nh.contact_message(name,email,category,message,ip)
                       VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                    (name, email, category, message, ip))
        return cur.fetchone()["id"]


def _email_contact(mid, name, email, category, message, ip):
    """Best-effort email via SMTP (e.g. Amazon SES). No-op if SMTP env isn't set.
    Env: SMTP_HOST, SMTP_PORT(587), SMTP_USER, SMTP_PASS, CONTACT_TO, CONTACT_FROM."""
    host, to = os.getenv("SMTP_HOST"), os.getenv("CONTACT_TO")
    if not (host and to):
        return None                      # not configured yet — the message is still stored
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[NHDataHub] {category or 'Contact'} — {name}"
        msg["From"] = os.getenv("CONTACT_FROM") or os.getenv("SMTP_USER") or to
        msg["To"] = to
        if email:
            msg["Reply-To"] = email
        msg.set_content(f"Name: {name}\nEmail: {email}\nTopic: {category}\nIP: {ip}\n\n{message}")
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls(context=ssl.create_default_context())
            u, p = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
            if u and p:
                s.login(u, p)
            s.send_message(msg)
        with cursor() as cur:
            cur.execute("UPDATE nh.contact_message SET emailed=true WHERE id=%s", (mid,))
        return True
    except Exception:                    # never surface SMTP errors to the visitor
        return False


@app.post("/contact", response_class=HTMLResponse)
def contact_submit(request: Request,
                   name: str = Form(""), email: str = Form(""),
                   category: str = Form("General comment"), message: str = Form(""),
                   website: str = Form("")):
    if website.strip():                  # honeypot filled → silently accept (drop the bot)
        return templates.TemplateResponse(request=request, name="contact.html", context={"sent": "ok"})
    missing = []
    if not name.strip():                         missing.append("your name")
    if "@" not in email or not email.strip():    missing.append("a valid email")
    if not message.strip():                      missing.append("a message")
    if missing:
        return templates.TemplateResponse(request=request, name="contact.html", context={
            "sent": "error", "errors": missing,
            "form": {"name": name, "email": email, "category": category, "message": message}})
    ip = request.client.host if request.client else None
    try:
        mid = _store_contact(name.strip()[:120], email.strip()[:200], category,
                             message.strip()[:4000], ip)
        _email_contact(mid, name.strip(), email.strip(), category, message.strip(), ip)
    except Exception:
        pass
    return templates.TemplateResponse(request=request, name="contact.html", context={"sent": "ok"})


@app.get("/compare", response_class=HTMLResponse)
def compare(request: Request,
            year: int = Query(None), county: str = Query(None),
            entity: str = Query(None), rate: str = Query("advertised"),
            sort: str = Query(None), dir: str = Query("desc")):
    rate = _clean_rate(rate)
    year = year or repo.latest_year()
    effective_sort = sort if sort in ("name", "county", "ratio", "cpp") else rate
    ctx = {
        "years": repo.available_years(),
        "counties": repo.counties(),
        "rows": repo.compare_rows(year, county, entity, effective_sort, dir),
        "year": year, "county": county, "entity": entity,
        "rate": rate, "sort": effective_sort, "dir": dir,
        "rate_meta": RATE_META,
    }
    tmpl = "partials/_compare_results.html" if request.headers.get("HX-Request") else "compare.html"
    return templates.TemplateResponse(request=request, name=tmpl, context=ctx)


@app.get("/schools", response_class=HTMLResponse)
def schools(request: Request, county: str = Query(None),
            sort: str = Query("cpp"), dir: str = Query("desc")):
    sort = sort if sort in repo.SCHOOL_SORTS else "cpp"
    ctx = {
        "counties": repo.counties(),
        "rows": repo.school_rows(county, sort, dir),
        "county": county, "sort": sort, "dir": dir,
        "state_cpp": repo.STATE_CPP_TOTAL,
    }
    tmpl = "partials/_schools_results.html" if request.headers.get("HX-Request") else "schools.html"
    return templates.TemplateResponse(request=request, name=tmpl, context=ctx)


@app.get("/state", response_class=HTMLResponse)
def state(request: Request):
    byears = repo.state_budget_years()
    rvyears = repo.state_revenue_years()
    by = min(byears) if byears else None            # primary budget FY (current year of biennium)
    by2 = max(byears) if len(byears) > 1 else None
    ry = max(rvyears) if rvyears else None           # latest revenue FY (actuals)
    # federal-funds drill-down: which agencies receive the money, top few + an "other" rollup
    fed_rows = repo.state_federal_funds(by) if by else []
    federal = None
    if fed_rows:
        ftot = sum(float(r["amount"] or 0) for r in fed_rows)
        TOP = 6
        top = fed_rows[:TOP]
        rest = fed_rows[TOP:]
        agencies = [{"department": r["department"], "category": r["category"],
                  "amount": float(r["amount"] or 0),
                  "pct": round(100 * float(r["amount"] or 0) / ftot) if ftot else 0}
                 for r in top]
        if rest:
            ramt = sum(float(r["amount"] or 0) for r in rest)
            agencies.append({"department": "Other agencies", "category": None,
                          "amount": ramt, "n": len(rest),
                          "pct": round(100 * ramt / ftot) if ftot else 0})
        federal = {"total": ftot, "agencies": agencies, "max": agencies[0]["amount"] if agencies else 1}
    ctx = {
        "has_data": bool(byears or rvyears),
        "budget_years": byears, "budget_year": by, "budget_year2": by2,
        "budget_total": repo.state_budget_total(by) if by else 0,
        "budget_total2": repo.state_budget_total(by2) if by2 else 0,
        "by_cat": repo.state_budget_by_category(by) if by else [],
        "by_dept": repo.state_budget(by) if by else [],
        "funding": repo.state_funding(by) if by else [],
        "federal": federal,
        "revenue_year": ry, "revenue_prior": (ry - 1) if ry else None,
        "revenue_total": repo.state_revenue_total(ry) if ry else 0,
        "revenue": repo.state_revenue(ry) if ry else [],
    }
    return templates.TemplateResponse(request=request, name="state.html", context=ctx)


@app.get("/national", response_class=HTMLResponse)
def national(request: Request):
    m = repo.state_comparison_map()
    nh, us = m.get("New Hampshire"), m.get("United States")
    n_states = sum(1 for r in m.values() if r.get("hh_burden_rank"))
    # states ranked by HOUSEHOLD tax burden, lowest first; U.S. average woven in for the chart
    hh_list = sorted([r for r in m.values()
                      if r.get("hh_burden_pct") is not None and r["state"] != "District of Columbia"],
                     key=lambda r: r["hh_burden_pct"])
    hh_max = max((r["hh_burden_pct"] for r in hh_list), default=1)
    # property's share of a NH household's tax basket
    prop_share = None
    if nh and nh.get("hh_property_pc"):
        basket = sum(nh.get(k) or 0 for k in
                     ("hh_property_pc", "hh_income_pc", "hh_sales_pc", "hh_excise_pc"))
        prop_share = round(100 * nh["hh_property_pc"] / basket) if basket else None
    neighbors = [m[s] for s in ("Massachusetts", "Vermont", "Maine", "Connecticut") if s in m]
    def per_household(r):
        """Per-capita component dollars scaled to per-household by avg household size."""
        pph = float(r.get("hh_persons_per_household") or 0)
        c = {k: round((r.get(k) or 0) * pph) for k in
             ("hh_property_pc", "hh_income_pc", "hh_sales_pc", "hh_excise_pc")}
        return {"state": r["state"], "prop": c["hh_property_pc"], "income": c["hh_income_pc"],
                "sales": c["hh_sales_pc"], "excise": c["hh_excise_pc"],
                "total": sum(c.values()), "pph": pph, "burden_pct": r.get("hh_burden_pct")}
    breakdown = [per_household(r) for r in ([nh, us] + neighbors) if r]
    breakdown_max = max((b["total"] for b in breakdown), default=1)
    ctx = {
        "nh": nh, "us": us, "hh_list": hh_list, "hh_max": hh_max, "n_states": n_states,
        "prop_share": prop_share, "breakdown_rows": breakdown, "breakdown_max": breakdown_max,
    }
    return templates.TemplateResponse(request=request, name="national.html", context=ctx)


@app.get("/political", response_class=HTMLResponse)
def political(request: Request):
    makeup = repo.political_makeup()
    vals = list(makeup.values())
    summary = {
        "all_r": sum(1 for m in vals if m["has_major"] and m["d"] == 0 and m["r"] > 0),
        "all_d": sum(1 for m in vals if m["has_major"] and m["r"] == 0 and m["d"] > 0),
        "mixed": sum(1 for m in vals if m["d"] > 0 and m["r"] > 0),
        "total": len(makeup),
    }
    return templates.TemplateResponse(request=request, name="political.html", context={
        "pol_map": mapsvg.political_map(makeup), "summary": summary})


@app.get("/about/equalized-rates", response_class=HTMLResponse)
def about_equalized(request: Request):
    return templates.TemplateResponse(request=request, name="about_equalized.html",
                                      context={})


@app.get("/healthz")
def healthz():
    return {"ok": True, "years": repo.available_years()}


@app.get("/town/{geoid}")
def town_legacy(geoid: str):
    """Back-compat: old GEOID URL -> canonical /{slug}."""
    slug = repo.slug_for(geoid)
    if not slug:
        raise HTTPException(404, "Municipality not found")
    return RedirectResponse(f"/{slug}", status_code=301)


def _render_town(request: Request, geoid: str):
    m = repo.get_municipality(geoid)
    if not m:
        raise HTTPException(404, "Municipality not found")
    history = repo.rate_history(geoid)
    current = history[0] if history else None
    split = repo.tax_split(geoid, current["tax_year"]) if current else None
    tax_dollar = repo.tax_dollar(geoid, current["tax_year"]) if current else None
    return templates.TemplateResponse(request=request, name="town.html", context={
        "m": m, "history": history, "current": current, "split": split,
        "schools": repo.get_schools(geoid), "state_cpp": repo.STATE_CPP_TOTAL,
        "finance": repo.get_finance(geoid), "trend": repo.get_finance_trend(geoid),
        "tax_dollar": tax_dollar, "municipal": repo.get_municipal(geoid),
        "total_budget": repo.total_budget(geoid),
        "legislators": repo.get_legislators(geoid),
        "select_board": repo.get_select_board(geoid),
        "locator": mapsvg.locator_map(geoid),
        "valuation": repo.get_valuation(geoid),
    })


# Catch-all town slug — MUST be declared last so the fixed routes above win.
@app.get("/{slug}", response_class=HTMLResponse)
def town(request: Request, slug: str):
    geoid = repo.geoid_for_slug(slug)
    if not geoid:
        raise HTTPException(404, "Page not found")
    return _render_town(request, geoid)
