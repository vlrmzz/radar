"""The gate before publishing. Exits non-zero, with one line per problem, if anything is wrong.

  python3 scripts/check.py picks <week>   the agent's picks: schema, numbers, links, wording, watchlist changes
  python3 scripts/check.py page           the built index.html

The numbers rule is the important one: every percentage in the text must be the trend of a tool
the same pick names, and every other figure must match the data for the tools it names (within 5%)
or an exact count from the feeds.
"""
import json, re, sys, urllib.error, urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch import BLOCK, UA, get, gh, wiki  # noqa: E402
from render import Week, week_label  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PCT = re.compile(r"(?<![\w.])[+−-]?(\d+(?:\.\d+)?)(?:\s*(?:–|-|to)\s*[+−-]?(\d+(?:\.\d+)?))?\s*%")
FIG = re.compile(r"(?<![\w.#/-])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|K|M|million|thousand)?(?![\w.%])")
SUM_WORDS = re.compile(r"\b(together|combined|between them|in total)\b", re.I)
RULE_NUMBERS = {5_000, 10_000}  # the page's own thresholds, which notes may mention
MONTHS = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
problems, warnings = [], []


def fail(msg):
    problems.append(msg)


def figures(text):
    """Numbers of 100 or more in text, skipping years, dates and percentages."""
    out = []
    for m in FIG.finditer(text):
        raw, unit = m.group(1), m.group(2)
        v = float(raw.replace(",", ""))
        v *= {"k": 1e3, "K": 1e3, "thousand": 1e3, "M": 1e6, "million": 1e6}.get(unit, 1)
        near = text[max(0, m.start() - 12):m.end() + 12]
        if v < 100 or (2000 <= v <= 2100 and not unit) or v in RULE_NUMBERS or MONTHS.search(near):
            continue
        out.append((m.group(0), v))
    return out


def known_values(w, named, sums=False):
    """(value, tolerance) pairs a figure may match: the named tools' installs and stars (and the sum of
    two named tools' installs) within 5%, and exact counts from the feeds (points, likes, stars) within 1%."""
    d = w.d
    tools = [w.tools[n] for n in named if n in w.tools]
    vals = [(t[k], 0.05) for t in tools for k in ("last_week", "stars", "stars_7d") if t.get(k)]
    lw = [t["last_week"] for t in tools if t["last_week"]]
    if sums:
        vals += [(a + b, 0.05) for i, a in enumerate(lw) for b in lw[i + 1:]]
    exact = [h["points"] for h in d["hn"]] + [h.get("comments") or 0 for h in d["hn"]]
    exact += [r[k] for r in d["new_repos"] for k in ("stars", "per_day")]
    exact += [m["likes"] or 0 for m in d["hf"]["models"]] + [p["upvotes"] or 0 for p in d["hf"]["papers"]]
    return [(v, tol) for v, tol in vals + [(x, 0.01) for x in exact] if v]


def check_numbers(where, text, allowed_pct, w, named):
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for m in PCT.finditer(text):
        for g in (m.group(1), m.group(2)):
            if g is not None and round(float(g)) not in allowed_pct:
                fail(f"{where}: '{m.group(0).strip()}' is not the trend of any tool named here "
                     f"(allowed: {sorted(allowed_pct)})")
    for sentence in sentences:
        values = known_values(w, named, sums=bool(SUM_WORDS.search(sentence)))
        for raw, v in figures(PCT.sub("", sentence)):
            if not any(abs(v - k) <= tol * k for k, tol in values):
                fail(f"{where}: the figure '{raw}' doesn't match any value in this week's data")


def check_link(where, url):
    try:
        req = urllib.request.Request(url, headers={**UA, "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status >= 400:
                fail(f"{where}: {url} returned {r.status}")
    except urllib.error.HTTPError as ex:
        (warnings if ex.code in (401, 403, 429) else problems).append(f"{where}: {url} returned {ex.code}")
    except Exception as ex:
        fail(f"{where}: {url} unreachable ({ex.__class__.__name__})")


def installs_last_week(tool):
    if "wiki" in tool:
        r = wiki(tool["wiki"])
        return r and r["weeks"][-1]
    if "pypi" in tool:
        r = get(f"https://pypistats.org/api/packages/{tool['pypi']}/recent")
        return r and r["data"]["last_week"]
    r = get(f"https://api.npmjs.org/downloads/point/last-week/{tool['npm']}")
    return r and r.get("downloads")


def check_watchlist(changes, w):
    wl = json.load(open(ROOT / "watchlist.json"))
    names = {t["name"] for ts in wl.values() for t in ts}
    adds = [c for c in changes if c["action"] == "add"]
    drops = [c for c in changes if c["action"] == "drop"]
    if len(adds) > 2 or len(drops) > 2:
        fail("watchlist: at most 2 additions and 2 removals a week")
    for c in adds:
        t = c.get("tool", {})
        where = f"watchlist add {t.get('name')}"
        if c.get("category") not in wl:
            fail(f"{where}: unknown category {c.get('category')}")
        if not t.get("name") or t["name"] in names or sum(k in t for k in ("pypi", "npm", "wiki")) != 1:
            fail(f"{where}: needs a new name and exactly one of pypi, npm or wiki")
            continue
        n, bar = installs_last_week(t), 5_000 if "wiki" in t else 10_000
        if not n or n < bar:
            fail(f"{where}: {n or 0} last week, the bar is {bar:,}")
        if t.get("repo") and not gh(f"repos/{t['repo']}"):
            (problems if w.d.get("github", True) else warnings).append(f"{where}: repo {t['repo']} not found")
    added = {c.get("tool", {}).get("name") for c in adds}
    for c in drops:
        t = w.tools.get(c.get("name"))
        if not t:
            fail(f"watchlist drop {c.get('name')}: not on the watchlist")
            continue
        stale = bool(t["released"]) and date.fromisoformat(t["released"]) < date.today() - timedelta(days=180)
        stale = stale and (t["change_4w"] or 0) <= 5
        first = next((x for x in t["weeks"][t.get("start", 0):] if x), 0)
        dying = (t["change_4w"] or 0) <= -25 and first >= 10_000 and (t["last_week"] or 0) < 10_000
        swap = c.get("successor") in added
        if not (stale or dying or swap):
            fail(f"watchlist drop {t['name']}: needs no release in 180 days and no growth, a steep fall below "
                 "10,000 installs, or a successor added the same week")


def check_picks(week):
    pf = ROOT / "picks" / f"{week}.json"
    if not pf.exists():
        return fail(f"{pf.relative_to(ROOT)} missing")
    w = Week(week)
    p, d = w.p, w.d
    all_pct = {abs(t["change_4w"]) for t in w.tools.values() if t["change_4w"] is not None}
    median = round(__import__("statistics").median(w.changes))
    if p.get("week") != week:
        fail(f"picks week is {p.get('week')}, expected {week}")
    if "review" not in p or not isinstance(p["review"].get("dropped"), list):
        fail("review missing: the reviewer agent's result must be recorded under 'review'")
    if not 0 <= len(p["picks"]) <= 5:
        fail("between 0 and 5 picks")
    if p["picks"] and not (p.get("summary") and len(p["summary"]) <= 220):
        fail("summary missing or longer than 220 characters")

    texts = [("summary", p.get("summary") or "", all_pct | {abs(w.median_for([])), abs(w.median_for(
        [n for n in w.tools if w.tools[n]["registry"] == "Wikipedia"][:1]))}, set(w.tools))]
    for i, k in enumerate(p["picks"], 1):
        where = f"pick {i} ({k.get('title', '?')[:40]})"
        for f in ("title", "note", "signal", "sources"):
            if not k.get(f):
                fail(f"{where}: '{f}' missing")
        if len(k.get("title", "")) > 70 or len(k.get("note", "")) > 480 or len(k.get("signal", "")) > 60:
            fail(f"{where}: title ≤ 70, note ≤ 480, signal ≤ 60 characters")
        text = " ".join(str(k.get(f, "")) for f in ("title", "note", "signal"))
        c = k.get("chart") or {}
        named = set(c.get("highlight", [])) | set(c.get("context", [])) | {n for n in w.tools if n in text}
        allowed = {abs(w.tools[n]["change_4w"]) for n in named if n in w.tools and w.tools[n]["change_4w"] is not None}
        if "@median" in named:
            allowed.add(abs(w.median_for(c.get("highlight", []))))
        texts.append((where, text, allowed, named))
        for n in c.get("highlight", []) + [x for x in c.get("context", []) if x != "@median"]:
            if n not in w.tools or w.tools[n]["change_4w"] is None:
                fail(f"{where}: chart tool '{n}' has no trend this week")
            elif n in w.unreliable:
                fail(f"{where}: chart uses '{n}', which is marked unreliable")
        for label, ref in c.get("stats", []):
            try:
                w.stat_value(ref)
            except Exception:
                fail(f"{where}: stat '{label}' -> '{ref}' doesn't resolve in the data")
        for t, u in k.get("sources", []):
            check_link(f"{where} source '{t}'", u)
    for i, (a, b) in enumerate(p.get("skipped", []), 1):
        texts.append((f"skipped {i}", f"{a} {b}", all_pct, {n for n in w.tools if n in f"{a} {b}"}))

    for where, text, allowed, named in texts:
        check_numbers(where, text, allowed, w, named)
        if BLOCK.search(text):
            fail(f"{where}: contains a blocked word ({BLOCK.search(text).group(0)})")

    repos = {r["repo"] for r in d["new_repos"]}
    for r in p.get("also_repos", []):
        if r not in repos:
            fail(f"also_repos: {r} is not in this week's data")
    for n in p.get("unreliable", []):
        if n not in w.tools:
            fail(f"unreliable: {n} is not on the watchlist")
    check_watchlist(p.get("watchlist_changes", []), w)


def check_page():
    f = ROOT / "index.html"
    if not f.exists():
        return fail("index.html missing")
    html = f.read_text()
    weeks = sorted(x.stem for x in (ROOT / "data").glob("*.json"))
    w = Week(weeks[-1])
    if week_label(weeks[-1]) not in html:
        fail("index.html is not the latest week")
    for bad in (">None<", " None ", "nan%", "Traceback", "{e(", "undefined"):
        if bad in html:
            fail(f"index.html contains '{bad}'")
    for k in w.p["picks"]:
        if k["title"].replace("&", "&amp;").replace("'", "&#x27;") not in html:
            fail(f"index.html is missing the pick '{k['title']}'")
    if len(html) < 10_000:
        fail(f"index.html is suspiciously small ({len(html)} bytes)")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "picks" and len(sys.argv) == 3:
        check_picks(sys.argv[2])
    elif mode == "page":
        check_page()
    else:
        sys.exit(__doc__)
    for x in warnings:
        print(f"warning: {x}")
    for x in problems:
        print(f"FAIL: {x}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
