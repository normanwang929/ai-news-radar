# AI News Radar Agent Notes

## Scope

This repo powers the **public AI News Radar static site**.
Data comes from the trendscan 15-cluster pipeline via `scripts/bridge_trendscan.py`,
which reads trendscan's SQLite and outputs radar-compatible JSON to `data/`.

**This is a data consumer, not a data collector.** The feed is the bridge script.

## Data Pipeline

```
trendscan (15 clusters + LLM analysis)
    → bridge_trendscan.py (reads SQLite, outputs radar JSON)
    → data/*.json
    → GitHub Pages (serves static files)
    → ai-radar skill ("今天AI圈有什么")
```

## Working Rules

- **Do NOT modify `scripts/update_news.py`** — that's the original collector, not used here.
- To refresh data, run: `python scripts/bridge_trendscan.py --output-dir data --window-hours 24`
- Do not commit private feeds, secrets, tokens, cookies, or `.env` values.
- The bridge script auto-generates all required JSON files.

## Common Commands

```bash
# Refresh data
python scripts/bridge_trendscan.py --db <trendscan_db_path> --output-dir data

# Serve locally
python -m http.server 8080

# Bridge + push
python scripts/cron_push_bridge.py
```

For agent workflows, read `skills/ai-news-radar/SKILL.md`.
