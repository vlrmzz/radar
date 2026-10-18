"""Record a run in runs.json and, when it published, apply the week's watchlist changes.

  python3 scripts/finish.py <week> published
  python3 scripts/finish.py <week> stopped "<why>"

A stopped run puts back everything a run may have built (index.html, weeks/, watchlist.json) as it
was in the last commit, and moves this week's picks to picks/stopped/ so they are kept but not shown.
"""
import json, shutil, subprocess, sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def apply_watchlist(changes):
    wl = json.load(open(ROOT / "watchlist.json"))
    for c in changes:
        if c["action"] == "add":
            wl[c["category"]].append(c["tool"])
        elif c["action"] == "drop":
            for cat in wl:
                wl[cat] = [t for t in wl[cat] if t["name"] != c["name"]]
    cats = ",\n".join(f'  {json.dumps(c)}: [\n' + ",\n".join(f"    {json.dumps(t, ensure_ascii=False)}" for t in ts) + "\n  ]"
                      for c, ts in wl.items())
    (ROOT / "watchlist.json").write_text("{\n" + cats + "\n}\n")


def restore(week, pf):
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True)
    for path in ("index.html", "weeks", "watchlist.json"):
        if git("ls-files", path).stdout.strip():
            git("checkout", "HEAD", "--", path)
    git("clean", "-fdq", "--", "weeks", "index.html")
    if pf.exists():
        (ROOT / "picks" / "stopped").mkdir(exist_ok=True)
        shutil.move(pf, ROOT / "picks" / "stopped" / pf.name)


if __name__ == "__main__":
    week, status = sys.argv[1], sys.argv[2]
    if status not in ("published", "stopped"):
        sys.exit(__doc__)
    pf = ROOT / "picks" / f"{week}.json"
    p = json.load(open(pf)) if pf.exists() else {}
    if status == "stopped":
        restore(week, pf)
    rf = ROOT / "runs.json"
    runs = [r for r in (json.load(open(rf)) if rf.exists() else []) if r["week"] != week]
    review = p.get("review", {})
    runs.append({"week": week, "date": date.today().isoformat(), "status": status,
                 "picks": len(p.get("picks", [])), "reviewer_dropped": len(review.get("dropped", [])),
                 "reviewer_fixed": len(review.get("fixed", [])),
                 "watchlist_changes": p.get("watchlist_changes", []) if status == "published" else [],
                 "why": sys.argv[3] if len(sys.argv) > 3 else ""})
    json.dump(runs, open(rf, "w"), indent=1, ensure_ascii=False)
    if status == "published":
        apply_watchlist(p.get("watchlist_changes", []))
    print(f"{week}: {status}; {len(runs)} run(s) recorded")
