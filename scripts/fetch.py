"""Collect this week's numbers into data/<week>.json.

No LLM is involved here: every figure on the page comes from this script. Sources are public
APIs that need no key: pypistats.org, the npm registry, Wikipedia page views (Wikimedia),
GitHub, Hugging Face and HN Algolia.
"""
import json, os, re, shutil, statistics, subprocess, sys, time, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today()
UA = {"User-Agent": "vlrmzz-radar/1.0 (weekly; https://vlrmzz.github.io/radar/)"}
BLOCK = re.compile(r"nsfw|porn|hentai|nude|onlyfans|jailbreak|crack|cheat|casino|airdrop|crypto|token sale|gambl", re.I)
AI_WORDS = ("ai", "llm", "agent", "model", "gpt", "claude", "gemini", "rag", "mcp", "inference", "transformer",
            "fine-tun", "rl", "coding", "openai", "anthropic", "deepseek", "qwen", "diffusion", "embedding")


def week_id(d=TODAY):
    y, w, _ = d.isocalendar()
    return f"{y}-w{w:02d}"


def get(url, headers=None, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                print(f"  ! {url}: {e}", file=sys.stderr)
                return None
            time.sleep(4 * (i + 1))


def gh(path):
    """GitHub API: a token from the environment if there is one, else the gh CLI, else anonymous.
    Returns None on any failure. Cloud routines put a placeholder in GH_TOKEN, so that one is ignored."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token == "proxy-injected":
        token = None
    if not token and shutil.which("gh"):
        out = subprocess.run(["gh", "api", path], capture_output=True, text=True)
        if out.returncode == 0:
            return json.loads(out.stdout)
    hdr = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    return get(f"https://api.github.com/{path}", hdr, tries=1)


def github_ok():
    """Can we read public repos at all? In a cloud routine the GitHub proxy may only serve the
    routine's own repo. Without GitHub the page still builds: no star counts, no new-repos list."""
    return bool(gh("repos/python/cpython"))


# ---- weekly series -------------------------------------------------------------------------

def weekly(daily):
    """daily: {date: n}. Zero days count as missing (npm has outages). Returns the last 12 weeks,
    oldest first, each as 7 x the mean of the days that have data, or None under 4 such days."""
    daily = {d: n for d, n in daily.items() if n}
    if not daily:
        return [None] * 12
    last = max(date.fromisoformat(d) for d in daily)
    weeks = []
    for w in range(12):
        end = last - timedelta(days=7 * w)
        days = [daily[k] for k in ((end - timedelta(days=i)).isoformat() for i in range(7)) if k in daily]
        weeks.append(round(7 * sum(days) / len(days)) if len(days) >= 4 else None)
    return weeks[::-1]


def change(weeks, start=0):
    """Median of the last 4 weeks against the 4 before, using only weeks after a source break
    (fewer weeks if needed). Medians, so one bot-driven spike week doesn't swing it."""
    clean = weeks[start:]
    k = min(4, len(clean) // 2)
    a = [w for w in clean[-2 * k:-k] if w] if k else []
    b = [w for w in clean[-k:] if w] if k else []
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.median(a), statistics.median(b)
    return round(100 * (mb - ma) / ma)


def spikes(weeks):
    """Weeks above 4x the median: almost always CI or mirror traffic, not people."""
    vals = [w for w in weeks if w]
    if len(vals) < 3:
        return []
    med = statistics.median(vals)
    return [i for i, w in enumerate(weeks) if w and w > 4 * med]


def mark_breaks(tracked):
    """A registry sometimes changes how it counts (PyPI did in August 2026): every package jumps
    or drops in the same week. Find such weeks per registry and start each series after the last."""
    breaks = {}
    for reg in ("PyPI", "npm", "Wikipedia"):
        series = [t for ts in tracked.values() for t in ts if t["registry"] == reg]
        full = [t["weeks"] for t in series if all(t["weeks"])]
        start = 0
        # Wikipedia views jump together in a big news week; that's real, not a counting change.
        if len(full) >= 5 and reg != "Wikipedia":
            for i in range(1, 12):
                r = statistics.median(w[i] / w[i - 1] for w in full)
                if r < 0.85 or r > 1.2:
                    start = i + 1
        breaks[reg] = start
        for t in series:
            t["start"] = start
            t["change_4w"] = change(t["weeks"], start)
            t["spikes"] = spikes(t["weeks"][start:])
    return breaks


# ---- sources -------------------------------------------------------------------------------

def pypi(pkg):
    stats = get(f"https://pypistats.org/api/packages/{pkg}/overall?mirrors=false")
    time.sleep(1.5)  # pypistats asks clients to be gentle
    meta = get(f"https://pypi.org/pypi/{pkg}/json")
    if not stats or not meta:
        return None
    daily = {r["date"]: r["downloads"] for r in stats["data"] if r["category"] == "without_mirrors"}
    ver = meta["info"]["version"]
    released = min((f["upload_time"][:10] for f in meta["releases"].get(ver) or []), default=None)
    urls = list((meta["info"].get("project_urls") or {}).values()) + [meta["info"].get("home_page") or ""]
    return {"weeks": weekly(daily), "version": ver, "released": released, "found_repo": github_repo(urls),
            "url": f"https://pypi.org/project/{pkg}/"}


def github_repo(urls):
    """owner/name of the first GitHub repo link in a package's metadata."""
    for u in urls:
        m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?]|$)", u or "")
        if m and m.group(1) not in ("sponsors", "orgs"):
            return f"{m.group(1)}/{m.group(2)}"
    return None


def npm(pkg):
    start, end = (TODAY - timedelta(days=90)).isoformat(), (TODAY - timedelta(days=1)).isoformat()
    rng = get(f"https://api.npmjs.org/downloads/range/{start}:{end}/{pkg}")
    doc = get(f"https://registry.npmjs.org/{urllib.parse.quote(pkg, safe='@')}")
    if not rng or not doc:
        return None
    ver = doc["dist-tags"]["latest"]
    daily = {r["day"]: r["downloads"] for r in rng["downloads"]}
    repo = doc.get("repository")
    repo = repo.get("url") if isinstance(repo, dict) else repo
    return {"weeks": weekly(daily), "version": ver, "released": doc["time"].get(ver, "")[:10],
            "found_repo": github_repo([repo, doc.get("homepage")]), "url": f"https://www.npmjs.com/package/{pkg}"}


def wiki(title):
    """Weekly English Wikipedia page views by people (not bots), a rough measure of public attention.
    Titles move (NotebookLM became Gemini Notebook), so redirects are resolved first."""
    q = urllib.parse.urlencode({"action": "query", "titles": title, "redirects": 1, "format": "json"})
    r = get(f"https://en.wikipedia.org/w/api.php?{q}")
    pages = list((r or {}).get("query", {}).get("pages", {}).values())
    if not pages or "missing" in pages[0]:
        return None
    canon = pages[0]["title"]
    start, end = (TODAY - timedelta(days=90)).strftime("%Y%m%d"), (TODAY - timedelta(days=1)).strftime("%Y%m%d")
    art = urllib.parse.quote(canon.replace(" ", "_"), safe="")
    pv = get(f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/{art}/daily/{start}/{end}")
    if not pv:
        return None
    daily = {f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}-{i['timestamp'][6:8]}": i["views"] for i in pv["items"]}
    return {"weeks": weekly(daily), "version": None, "released": None, "article": canon,
            "url": f"https://en.wikipedia.org/wiki/{art}"}


def previous():
    files = sorted(p for p in (ROOT / "data").glob("*.json") if p.stem < week_id())
    return json.load(open(files[-1])) if files else None


def tracked(with_github=True):
    wl = json.load(open(ROOT / "watchlist.json"))
    prev = previous()
    prev_stars = {t["name"]: t.get("stars") for ts in (prev or {}).get("tracked", {}).values() for t in ts}
    out = {}
    for cat, tools in wl.items():
        out[cat] = []
        for t in tools:
            print(f"  {cat}: {t['name']}", file=sys.stderr)
            reg = "PyPI" if "pypi" in t else "npm" if "npm" in t else "Wikipedia"
            d = pypi(t["pypi"]) if reg == "PyPI" else npm(t["npm"]) if reg == "npm" else wiki(t["wiki"])
            if not d:
                print(f"  ! skipped {t['name']}: no data", file=sys.stderr)
                continue
            slug = t.get("repo") or d.pop("found_repo", None)
            d.pop("found_repo", None)
            repo = gh(f"repos/{slug}") if with_github and slug else None
            stars = repo and repo.get("stargazers_count")
            ps = prev_stars.get(t["name"])
            out[cat].append({**t, **d, "repo": slug, "registry": reg,
                             "last_week": d["weeks"][-1], "stars": stars,
                             "stars_7d": stars - ps if stars is not None and ps is not None else None})
    return out


def is_ai(text):
    t = (text or "").lower()
    return not BLOCK.search(t) and any(re.search(rf"\b{re.escape(w)}", t) for w in AI_WORDS)


def new_repos():
    since = (TODAY - timedelta(days=21)).isoformat()
    q = urllib.parse.quote(f"created:>{since} stars:>300")
    res = gh(f"search/repositories?q={q}&sort=stars&order=desc&per_page=60") or {"items": []}
    rows = []
    for r in res["items"]:
        if not is_ai(f"{r['description']} {' '.join(r.get('topics', []))} {r['name']}"):
            continue
        age = max(1, (datetime.now(timezone.utc) - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).days)
        rows.append({"repo": r["full_name"], "desc": r["description"], "stars": r["stargazers_count"],
                     "created": r["created_at"][:10], "age_days": age, "per_day": round(r["stargazers_count"] / age),
                     "lang": r["language"], "url": r["html_url"]})
    return sorted(rows, key=lambda r: -r["per_day"])[:15]


def hn():
    since = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
    nf = urllib.parse.quote(f"created_at_i>{since},points>150")
    res = get(f"https://hn.algolia.com/api/v1/search?tags=story&numericFilters={nf}&hitsPerPage=100") or {"hits": []}
    rows = [{"title": h["title"], "points": h["points"], "comments": h.get("num_comments"),
             "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
             "hn": f"https://news.ycombinator.com/item?id={h['objectID']}"}
            for h in res["hits"] if is_ai(h["title"])]
    return sorted(rows, key=lambda r: -r["points"])[:15]


def hf():
    models = get("https://huggingface.co/api/models?sort=trendingScore&limit=10") or []
    papers = get("https://huggingface.co/api/daily_papers?limit=100") or []
    week_ago = (TODAY - timedelta(days=7)).isoformat()
    papers = sorted((p for p in papers if p.get("publishedAt", "")[:10] >= week_ago),
                    key=lambda p: -p["paper"].get("upvotes", 0))[:8]
    return {
        "models": [{"id": m["id"], "likes": m.get("likes"), "task": m.get("pipeline_tag"),
                    "tags": m.get("tags", []), "created": m.get("createdAt", "")[:10]}
                   for m in models if not BLOCK.search(m["id"])],
        "papers": [{"title": p["paper"]["title"], "upvotes": p["paper"].get("upvotes"),
                    "summary": (p["paper"].get("summary") or "")[:600],
                    "url": f"https://huggingface.co/papers/{p['paper']['id']}"} for p in papers],
    }


if __name__ == "__main__":
    has_gh = github_ok()
    if not has_gh:
        print("  ! GitHub API unreachable: no stars or new repos this week", file=sys.stderr)
    t = tracked(has_gh)
    data = {"week": week_id(), "fetched": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "github": has_gh, "breaks": mark_breaks(t), "tracked": t,
            "new_repos": new_repos() if has_gh else [], "hn": hn(), "hf": hf()}
    n = sum(len(v) for v in t.values())
    if n < 20:
        sys.exit(f"only {n} tools returned data; not writing a broken week")
    out = ROOT / "data" / f"{data['week']}.json"
    json.dump(data, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"wrote {out.relative_to(ROOT)}: {n} tools, {len(data['new_repos'])} repos, {len(data['hn'])} HN stories")
