# AI radar

A weekly page at https://vlrmzz.github.io/radar/ on what moved in AI: models, assistants, creative
tools and the developer tools people build with. A scheduled Claude Code routine builds it every Monday with no
manual step; `CLAUDE.md` is the routine's full instructions.

- `scripts/fetch.py`: collects the numbers (Wikipedia page views, pypistats.org, npm, GitHub, Hugging Face, HN Algolia) into `data/<week>.json`
- `picks/<week>.json`: the agent's picks and notes, checked by a reviewer agent
- `scripts/check.py`: the gate; every number in the text must match the data, links must work
- `scripts/render.py`: builds `index.html` and `weeks/<week>/`
- `scripts/finish.py`: records the run in `runs.json` and applies watchlist changes

The page uses the main site's `/assets/site.css`, so it only looks right served under vlrmzz.github.io.
