# Ganked Analyzer (EVE Online)

Web dashboard to analyze gank frequency by region using the zKillboard API.

## What it does

- Reads region kills from the API, for example:
  `https://zkillboard.com/api/kills/regionID/10000002/`
- Identifies ganks using the `ganked` label in `zkb.labels`.
- Fetches kill timestamps through ESI using `killmail_id + zkb.hash`.
- Displays:
  - total kills scanned;
  - total `GANKED` kills;
  - distribution by hour (UTC);
  - distribution by weekday.

## Initial region

- The Forge (`10000002`) is preconfigured in `regions.json`.
- To add more regions, edit `regions.json` using this format:

```json
[
  {"name": "The Forge", "id": 10000002},
  {"name": "Domain", "id": 10000043}
]
```

## Run locally

1. Install `uv` (Astral), if needed:

```bash
pip install uv
```

2. Sync environment and dependencies:

```bash
uv sync
```

3. Run the app:

```bash
uv run python app.py
```

4. Open in browser:

`http://127.0.0.1:5000`

## Notes

- zKillboard may apply request limits.
- Times are shown in UTC.
- Classification depends on `zkb.labels`; if API output changes, parser updates may be required.
- Retrieved data is stored in `cache.json` to avoid repeated requests.
- To force refresh and ignore cache, use `?refresh=true` in `/api/analysis`.

## Date range filter

- `/api/analysis` accepts optional filters:
  - `start_date=YYYY-MM-DD`
  - `end_date=YYYY-MM-DD`
- Page scanning is automatic and no longer requires a `pages` parameter.
- Example:

`/api/analysis?region_id=10000002&start_date=2026-01-01&end_date=2026-03-01`
