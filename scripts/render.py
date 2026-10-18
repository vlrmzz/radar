"""Build the site from data/<week>.json (numbers) and picks/<week>.json (the agent's notes).

Writes index.html for the latest week and weeks/<week>/index.html for every week, so old
weeks always share the current template. No LLM is involved here.
"""
import json, statistics, sys
from datetime import datetime
from html import escape as e
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "/radar"


def num(n):
    if n is None:
        return "–"
    for div, suf in ((1e6, "M"), (1e3, "k")):
        if n >= div:
            v = n / div
            return f"{v:.1f}{suf}" if v < 10 else f"{v:.0f}{suf}"
    return str(n)


def signed(v):
    return f'{"+" if v > 0 else "−" if v < 0 else ""}{abs(v)}%'


def short_date(s):
    return datetime.fromisoformat(s).strftime("%-d %b") if s else "–"


def week_label(w):
    y, n = w.split("-w")
    return f"Week {int(n)}, {y}"


def spark(weeks, spiky):
    vals = [w for w in weeks if w]
    if len(weeks) < 2 or len(vals) < 2:
        return ""
    top = 3 * statistics.median(vals) if spiky else max(vals)
    lo = min(min(vals), top)
    pts = [f"{i * 72 / (len(weeks) - 1):.1f},{15 - 13 * (min(w, top) - lo) / ((top - lo) or 1):.1f}"
           for i, w in enumerate(weeks) if w is not None]
    cls = "up" if vals[-1] >= vals[0] else "down"
    dash = ' stroke-dasharray="2 2"' if spiky else ""
    return (f'<svg class="spark {cls}" viewBox="0 0 72 16" width="72" height="16" aria-hidden="true">'
            f'<polyline points="{" ".join(pts)}"{dash}/></svg>')


def pct_cell(c, unreliable=False):
    if unreliable:
        return '<td class="num" title="Not a reliable trend this week, see below">–</td>'
    if c is None:
        return '<td class="num">–</td>'
    cls = "up" if c >= 10 else "down" if c <= -10 else ""
    return f'<td class="num {cls}">{signed(c)}</td>'


def bars(rows, label, fmt=signed):
    """Horizontal bars in HTML, so text stays readable at any width. rows: (name, value, kind),
    kind up/down/ctx/one. Colour says direction (gray for context); the side of zero and the
    signed value say it too, so colour is never the only channel."""
    if not rows:
        return ""
    lo, hi = min(0, *(v for _, v, _ in rows)), max(0, *(v for _, v, _ in rows))
    span = (hi - lo) or 1
    zero = 100 * -lo / span
    out = []
    for name, v, kind in rows:
        w = 100 * abs(v) / span
        left = zero if v >= 0 else zero - w
        out.append(f'<div class="bar-row" tabindex="0" title="{e(name)}: {e(fmt(v))}">'
                   f'<span class="bl">{e(name)}</span>'
                   f'<span class="bt"><span class="bz" style="left:{zero:.1f}%"></span>'
                   f'<span class="b {kind} {"pos" if v >= 0 else "neg"}" style="left:{left:.1f}%;width:{max(w, 0.8):.1f}%"></span></span>'
                   f'<span class="bv">{e(fmt(v))}</span></div>')
    return f'<div class="bars" role="img" aria-label="{e(label)}">{"".join(out)}</div>'


class Week:
    def __init__(self, week):
        self.d = json.load(open(ROOT / "data" / f"{week}.json"))
        pf = ROOT / "picks" / f"{week}.json"
        self.p = json.load(open(pf)) if pf.exists() else {"picks": []}
        self.tools = {t["name"]: t for ts in self.d["tracked"].values() for t in ts}
        self.unreliable = set(self.p.get("unreliable", []))
        self.changes = [t["change_4w"] for t in self.tools.values() if t["change_4w"] is not None]

    def median_for(self, names):
        """Median trend of the same kind as the named items: page views or installs, never both."""
        wiki = any(self.tools[n]["registry"] == "Wikipedia" for n in names if n in self.tools)
        return round(statistics.median(t["change_4w"] for t in self.tools.values()
                                       if t["change_4w"] is not None and (t["registry"] == "Wikipedia") == wiki))

    def stat_value(self, ref):
        kind, key, *field = ref.split("|")
        d = self.d
        if kind == "hn":
            return f'{next(h for h in d["hn"] if h["title"].startswith(key))["points"]:,}'
        if kind == "hf":
            return f'{next(m for m in d["hf"]["models"] if m["id"] == key)[field[0]]:,}'
        if kind == "hfrank":
            return f'#{[m["id"] for m in d["hf"]["models"]].index(key) + 1}'
        if kind == "repo":
            r = next(r for r in d["new_repos"] if r["repo"] == key)
            return f'{r[field[0]]:,}'
        if kind == "tool":
            v = self.tools[key][field[0]]
            return signed(v) if field[0] == "change_4w" else num(v)
        raise ValueError(f"unknown stat ref {ref}")

    def pick_chart(self, k):
        c = k.get("chart")
        if not c:
            return ""
        if "stats" in c:
            tiles = "".join(f'<div class="stat"><span class="sl">{e(l)}</span><span class="sv">{self.stat_value(r)}</span></div>'
                            for l, r in c["stats"])
            return f'<div class="stats">{tiles}</div>'
        rows = [(n, self.tools[n]["change_4w"], "up" if self.tools[n]["change_4w"] >= 0 else "down") for n in c["highlight"]]
        for n in c.get("context", []):
            if n == "@median":
                wiki = any(self.tools[h]["registry"] == "Wikipedia" for h in c["highlight"])
                rows.append(("Median, all topics" if wiki else "Median, all tools", self.median_for(c["highlight"]), "ctx"))
            else:
                rows.append((n, self.tools[n]["change_4w"], "ctx"))
        named = "".join(f'<span class="k {kd}"></span>' for kd in ("up", "down") if any(x[2] == kd for x in rows))
        key = f'<p class="key">{named}named in the note' + (' <span class="k ctx"></span>for comparison' if c.get("context") else "") + "</p>"
        return bars(rows, "Install trend: " + ", ".join(f"{n} {signed(v)}" for n, v, _ in rows)) + key

    def picks_html(self):
        if not self.p["picks"]:
            return '  <p class="meta">No picks this week: nothing passed the checks. The numbers below are still current.</p>'
        items = "\n".join(f"""    <li>
      <h3>{e(k["title"])}</h3>
      <p>{e(k["note"])}</p>
      {self.pick_chart(k)}
      <span class="meta">{e(k["signal"])}</span>
      <span class="srcs">{" · ".join(f'<a href="{e(u)}">{e(t)}</a>' for t, u in k["sources"])}</span>
    </li>""" for k in self.p["picks"])
        summary = f'  <p class="summary">{e(self.p["summary"])}</p>\n' if self.p.get("summary") else ""
        return f'{summary}  <ul class="entries picks">\n{items}\n  </ul>'

    def movers(self, wiki, label, ups=6, downs=4, floor=10_000):
        big = [t for t in self.tools.values() if t["change_4w"] is not None and (t["registry"] == "Wikipedia") == wiki
               and (t["last_week"] or 0) >= floor and t["name"] not in self.unreliable]
        sel = sorted(big, key=lambda t: -t["change_4w"])[:ups] + sorted(big, key=lambda t: t["change_4w"])[:downs]
        sel = sorted({t["name"]: t for t in sel}.values(), key=lambda t: -t["change_4w"])
        return bars([(t["name"], t["change_4w"], "up" if t["change_4w"] >= 0 else "down") for t in sel], label)

    def tables(self, wiki):
        out = []
        for cat, ts in self.d["tracked"].items():
            if not ts or (ts[0]["registry"] == "Wikipedia") != wiki:
                continue
            link = lambda t: t["url"] if wiki or not t.get("repo") else f"https://github.com/{t['repo']}"
            rows = "\n".join(f"""      <tr>
        <td><a href="{e(link(t))}">{e(t["name"])}</a>{"" if wiki else f'<span class="reg">{t["registry"]}</span>'}</td>
        <td class="num">{num(t["last_week"])}</td>
        <td class="sp">{spark(t["weeks"][t.get("start", 0):], bool(t["spikes"]))}</td>
        {pct_cell(t["change_4w"], t["name"] in self.unreliable)}
        {"" if wiki else f'<td class="rel">{short_date(t["released"])}</td>'}
      </tr>""" for t in sorted(ts, key=lambda t: -(t["last_week"] or 0)))
            head = ('<th>topic</th><th class="num">views / wk</th><th class="sp">recent weeks</th><th class="num">trend</th>' if wiki else
                    '<th>tool</th><th class="num">installs / wk</th><th class="sp">recent weeks</th><th class="num">trend</th><th class="rel">released</th>')
            out.append(f"""  <h3 class="cat">{e(cat)}</h3>
  <table class="watch{" wiki" if wiki else ""}">
    <thead><tr>{head}</tr></thead>
    <tbody>
{rows}
    </tbody>
  </table>""")
        return "\n".join(out)

    def breaks_note(self):
        b = {k: v for k, v in self.d.get("breaks", {}).items() if v}
        if not b:
            return ""
        b.pop("Wikipedia", None)
        if not b:
            return ""
        return " This week, " + " and ".join(f"{reg} trends use only the last {12 - s} weeks" for reg, s in b.items()) + \
            ", because the registry changed how it counts."

    def also(self):
        d, p = self.d, self.p
        excl = p.get("also_hn_exclude", [])
        hn = [h for h in d["hn"] if not any(h["title"].startswith(x) for x in excl)][:6]
        repos = [r for r in d["new_repos"] if r["repo"] in p.get("also_repos", [])]
        li = lambda items: "\n".join(f"      <li>{x}</li>" for x in items)
        blocks = []
        if hn:
            blocks.append(f"""      <h3 class="cat">On Hacker News</h3>
      <ul class="mini">
{li(f'<a href="{e(h["url"])}">{e(h["title"])}</a> <span class="n"><a href="{e(h["hn"])}">{h["points"]} pts</a></span>' for h in hn)}
      </ul>""")
        if repos:
            blocks.append(f"""      <h3 class="cat">New on GitHub</h3>
      <p class="meta cap">Stars a day since each repo was created</p>
      {bars([(r["repo"].split("/")[1], r["per_day"], "one") for r in repos], "Stars a day for new repos", lambda v: f"{v:,}")}
      <ul class="mini">
{li(f'<a href="{e(r["url"])}">{e(r["repo"])}</a> <span class="n">{num(r["stars"])} stars in {r["age_days"]} days</span><br><span class="d">{e(r["desc"] or "")}</span>' for r in repos)}
      </ul>""")
        if d["hf"]["models"]:
            blocks.append(f"""      <h3 class="cat">Trending on Hugging Face</h3>
      <ul class="mini">
{li(f'<a href="https://huggingface.co/{e(m["id"])}">{e(m["id"])}</a> <span class="n">{e((m["task"] or "").replace("-", " "))}</span>' for m in d["hf"]["models"][:6])}
      </ul>""")
        if d["hf"]["papers"]:
            blocks.append(f"""      <h3 class="cat">Papers people upvoted</h3>
      <ul class="mini">
{li(f'<a href="{e(x["url"])}">{e(x["title"])}</a> <span class="n">{x["upvotes"]} votes</span>' for x in d["hf"]["papers"][:6])}
      </ul>""")
        return "\n".join(blocks)

    def skipped(self):
        items = self.p.get("skipped", [])
        if not items:
            return ""
        lis = "\n".join(f"    <li><strong>{e(a)}</strong>: {e(b)}</li>" for a, b in items)
        return f'  <h2 class="sec-label">left out, and why</h2>\n  <ul class="plain skipped">\n{lis}\n  </ul>\n'


def runs_note(runs):
    if not runs:
        return ""
    ok = sum(r["status"] == "published" for r in runs)
    last = runs[-1]
    if len(runs) == 1:
        tally = "It has run once so far"
    else:
        tally = f"It has run {len(runs)} times so far: {ok} published"
        if ok < len(runs):
            tally += f" and {len(runs) - ok} stopped at a check, which leaves the previous week up"
    return (f" {tally}. The last run, on {short_date(last['date'])}, "
            f"{'published' if last['status'] == 'published' else 'stopped at a check'}.")


def page(w, weeks, runs, latest):
    d = w.d
    fetched = datetime.fromisoformat(d["fetched"])
    past = [x for x in reversed(weeks) if x != d["week"]][:12]
    past_links = " · ".join(f'<a href="{BASE}/weeks/{x}/">{week_label(x)}</a>' for x in past) or "this is the first week"
    notice = "" if latest else f'  <p class="todo">An archived week. <a href="{BASE}/">See the latest</a>.</p>\n'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap">
<link rel="stylesheet" href="/assets/site.css">
<link rel="stylesheet" href="{BASE}/assets/radar.css">
<link rel="icon" href="/favicon.ico">
<link rel="canonical" href="https://vlrmzz.github.io{BASE}/{"" if latest else f"weeks/{d['week']}/"}">
<title>AI radar{"" if latest else f", {week_label(d['week']).lower()}"} · Vlr Mzz</title>
<meta name="description" content="A weekly, automatically generated view of what moved in AI: models, apps and the tools people build with.">
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="site-name" href="/">Vlr Mzz</a>
    <nav class="site-nav" aria-label="Main">
      <a href="/work/">Work</a>
      <a href="/projects/">Projects</a>
      <a href="/writing/">Writing</a>
      <a href="{BASE}/" aria-current="page">Radar</a>
      <a href="/about/">About</a>
    </nav>
  </div>
</header>
<main class="wrap">
  <h1 class="page-title">AI radar</h1>
{notice}  <p class="lede">What moved in AI this week: models, apps and the tools people build with. It's built automatically every Monday from public numbers: what people read about on Wikipedia, what developers install, and what gets attention on Hacker News, GitHub and Hugging Face.</p>
  <p class="post-meta">{week_label(d["week"])} · data from {fetched.strftime("%-d %B %Y")} · <a href="#method">generated by an agent, not reviewed</a></p>

  <h2 class="sec-label">this week</h2>
{w.picks_html()}

  <h2 class="sec-label">public attention</h2>
  <h3 class="cat">Biggest moves</h3>
  <p class="meta cap">Change in weekly English Wikipedia views, for pages with at least 5,000 views a week.</p>
  {w.movers(True, "Biggest changes in Wikipedia views", ups=5, downs=3, floor=5_000)}
  <p class="meta">Views by people (not bots) of each English Wikipedia article last week: a rough measure of how many people looked something up. The trend compares the median of recent weeks with the weeks before, and a dashed line means a spike, usually a news story.</p>
{w.tables(True)}

  <h2 class="sec-label">developer tools</h2>
  <h3 class="cat">Biggest moves</h3>
  <p class="meta cap">Install trend for the six biggest risers and four biggest fallers with at least 10,000 installs a week.</p>
  {w.movers(False, "Biggest install trends on the watchlist")}
  <p class="meta">Installs last week from PyPI and npm. The trend compares the median of recent weeks with the weeks before. A dashed line means some weeks had bot-sized spikes.{w.breaks_note()}</p>
{w.tables(False)}

  <h2 class="sec-label">also this week</h2>
{w.also()}

{w.skipped()}
  <h2 class="sec-label" id="method">how this page is made</h2>
  <p>A Claude Code job runs every Monday. A script collects the numbers: Wikipedia page views from Wikimedia, weekly installs from pypistats.org and the npm registry, stars and releases from GitHub, trending models and papers from Hugging Face, and the top AI stories on Hacker News. The agent never writes a number. It picks what mattered this week and writes the notes. A second agent checks each note against its sources and drops anything it can't confirm. A last script checks that every number in the notes matches the data and that every link works, and the page is published only if all of that passes.{runs_note(runs)}</p>
  <p class="meta">Page views measure curiosity, not use, and install counts include CI and mirrors, so read both as trends, not user counts. <a href="{BASE}/data/{d["week"]}.json">Raw data for this week</a></p>
  <p class="meta">Past weeks: {past_links}</p>
</main>
<footer class="site-footer">
  <div class="wrap"><span>Vlr Mzz · Zurich</span><span><a href="/">Home</a></span></div>
</footer>
</body>
</html>
"""


if __name__ == "__main__":
    weeks = sorted(p.stem for p in (ROOT / "data").glob("*.json"))
    if not weeks:
        sys.exit("no data yet")
    rf = ROOT / "runs.json"
    runs = json.load(open(rf)) if rf.exists() else []
    for wk in weeks:
        out = ROOT / "weeks" / wk
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(page(Week(wk), weeks, runs, latest=False))
    (ROOT / "index.html").write_text(page(Week(weeks[-1]), weeks, runs, latest=True))
    print(f"rendered index.html and {len(weeks)} week page(s); latest {weeks[-1]}")
