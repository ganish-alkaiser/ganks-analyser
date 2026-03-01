from __future__ import annotations

import json
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Iterable

import requests
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)


DEFAULT_REGIONS = [
    {"name": "The Forge", "id": 10000002},
]

BASE_URL = "https://zkillboard.com"
ESI_BASE_URL = "https://esi.evetech.net/latest"
HEADERS = {
    "User-Agent": "ganked-analyzer/1.0 (+https://zkillboard.com)",
    "Accept": "application/json",
}
CACHE_FILE = Path("cache.json")
REGIONS_FILE = Path("regions.json")
CACHE_LOCK = Lock()
MAX_AUTO_PAGES = 200


def _new_cache() -> dict:
    return {"region_pages": {}, "kill_times": {}}


def _load_cache() -> dict:
    with CACHE_LOCK:
        if not CACHE_FILE.exists():
            return _new_cache()

        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _new_cache()

    if not isinstance(data, dict):
        return _new_cache()

    data.setdefault("region_pages", {})
    data.setdefault("kill_times", {})
    return data


def _save_cache(cache: dict) -> None:
    with CACHE_LOCK:
        CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _page_cache_key(region_id: int, page: int) -> str:
    return f"{region_id}:{page}"


def _normalize_region_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None

    name = item.get("name")
    region_id = item.get("id")
    if not isinstance(name, str) or not name.strip():
        return None

    try:
        normalized_id = int(region_id)
    except (TypeError, ValueError):
        return None

    return {"name": name.strip(), "id": normalized_id}


def _load_regions() -> list[dict]:
    if not REGIONS_FILE.exists():
        return list(DEFAULT_REGIONS)

    try:
        raw = json.loads(REGIONS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return list(DEFAULT_REGIONS)

    if not isinstance(raw, list):
        return list(DEFAULT_REGIONS)

    regions: list[dict] = []
    seen_ids: set[int] = set()
    for item in raw:
        normalized = _normalize_region_item(item)
        if normalized is None:
            continue
        region_id = normalized["id"]
        if region_id in seen_ids:
            continue
        seen_ids.add(region_id)
        regions.append(normalized)

    return regions or list(DEFAULT_REGIONS)


def _build_region_url(region_id: int, page: int) -> str:
    if page <= 1:
        return f"{BASE_URL}/api/kills/regionID/{region_id}/"
    return f"{BASE_URL}/api/kills/regionID/{region_id}/page/{page}/"


def _is_ganked(labels: list[str]) -> bool:
    return any(label.lower() == "ganked" for label in labels)


def _cached_page_to_ganked_refs(cached_page: dict) -> dict[int, str | None]:
    refs: dict[int, str | None] = {}

    cached_refs = cached_page.get("ganked_kill_refs", [])
    if isinstance(cached_refs, list):
        for item in cached_refs:
            if not isinstance(item, dict):
                continue
            killmail_id = item.get("killmail_id")
            kill_hash = item.get("hash")
            if killmail_id is None:
                continue
            refs[int(killmail_id)] = str(kill_hash) if kill_hash else None

    if refs:
        return refs

    cached_ids = cached_page.get("ganked_kill_ids", [])
    if isinstance(cached_ids, list):
        for kill_id in cached_ids:
            refs[int(kill_id)] = None

    return refs


def _collect_ganked_kill_ids(
    region_id: int,
    session: requests.Session,
    cache: dict,
    force_refresh: bool,
    start_date: date | None,
) -> tuple[dict[int, str | None], int, bool, dict, int]:
    ganked_refs: dict[int, str | None] = {}
    kills_seen = 0
    cache_changed = False
    page_cache = cache.get("region_pages", {})
    source_stats = {
        "pages_from_api": 0,
        "pages_from_cache": 0,
    }
    pages_scanned = 0

    for page in range(1, MAX_AUTO_PAGES + 1):
        cache_key = _page_cache_key(region_id, page)
        cached_page = None
        page_payload = None

        if not force_refresh and cache_key in page_cache:
            cached_page = page_cache[cache_key]

        if cached_page is not None:
            source_stats["pages_from_cache"] += 1
        else:
            url = _build_region_url(region_id, page)
            response = session.get(url, timeout=20)
            response.raise_for_status()
            page_payload = response.json()
            source_stats["pages_from_api"] += 1

        if page_payload is not None and not isinstance(page_payload, list):
            break

        if page_payload is not None and not page_payload:
            break

        if start_date is not None:
            page_top_ref: dict | None = None
            if cached_page is not None:
                page_top_ref = cached_page.get("page_top_ref")
            elif isinstance(page_payload, list) and page_payload:
                top_kill_id = page_payload[0].get("killmail_id")
                top_hash = page_payload[0].get("zkb", {}).get("hash")
                if top_kill_id and top_hash:
                    page_top_ref = {
                        "killmail_id": int(top_kill_id),
                        "hash": str(top_hash),
                    }

            if page_top_ref:
                try:
                    top_time, top_changed, _ = _extract_kill_time(
                        int(page_top_ref["killmail_id"]),
                        str(page_top_ref["hash"]),
                        session,
                        cache,
                        force_refresh,
                    )
                except (requests.RequestException, ValueError, KeyError):
                    top_time = None
                    top_changed = False

                cache_changed = cache_changed or top_changed
                if (
                    top_time is not None
                    and top_time.date() < start_date
                ):
                    break

        pages_scanned += 1
        if cached_page is not None:
            kills_seen += int(cached_page.get("kills_seen", 0))
            ganked_refs.update(_cached_page_to_ganked_refs(cached_page))
            continue

        payload = page_payload if isinstance(page_payload, list) else []
        page_kills_seen = 0
        page_ganked: dict[int, str | None] = {}
        page_top_ref: dict | None = None

        for entry in payload:
            kill_id = entry.get("killmail_id")
            if not kill_id:
                continue

            if page_top_ref is None:
                top_hash = entry.get("zkb", {}).get("hash")
                if top_hash:
                    page_top_ref = {
                        "killmail_id": int(kill_id),
                        "hash": str(top_hash),
                    }

            kills_seen += 1
            page_kills_seen += 1
            labels = entry.get("zkb", {}).get("labels", [])
            if isinstance(labels, list) and _is_ganked(labels):
                parsed_id = int(kill_id)
                kill_hash = entry.get("zkb", {}).get("hash")
                parsed_hash = str(kill_hash) if kill_hash else None
                ganked_refs[parsed_id] = parsed_hash
                page_ganked[parsed_id] = parsed_hash

        page_cache[cache_key] = {
            "kills_seen": page_kills_seen,
            "page_top_ref": page_top_ref,
            "ganked_kill_refs": [
                {"killmail_id": kill_id, "hash": kill_hash}
                for kill_id, kill_hash in sorted(page_ganked.items())
            ],
        }
        cache_changed = True

    return (
        ganked_refs,
        kills_seen,
        cache_changed,
        source_stats,
        pages_scanned,
    )


def _extract_kill_time(
    kill_id: int,
    kill_hash: str,
    session: requests.Session,
    cache: dict,
    force_refresh: bool,
) -> tuple[datetime | None, bool, str]:
    kill_times = cache.get("kill_times", {})
    cache_key = f"{kill_id}:{kill_hash}"

    if not force_refresh and cache_key in kill_times:
        cached_value = kill_times[cache_key]
        if cached_value is None:
            return None, False, "cache"
        try:
            return datetime.fromisoformat(cached_value), False, "cache"
        except ValueError:
            pass

    url = (
        f"{ESI_BASE_URL}/killmails/{kill_id}/{kill_hash}/"
        "?datasource=tranquility"
    )
    response = session.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, dict):
        kill_times[cache_key] = None
        return None, True, "api"

    killmail_time = data.get("killmail_time")
    if not killmail_time:
        kill_times[cache_key] = None
        return None, True, "api"

    parsed_time = datetime.fromisoformat(killmail_time.replace("Z", "+00:00"))
    kill_times[cache_key] = parsed_time.isoformat()
    return parsed_time, True, "api"


def _aggregate_datetimes(items: Iterable[datetime]) -> dict:
    by_hour = Counter()
    by_weekday = Counter()

    for dt in items:
        by_hour[dt.hour] += 1
        by_weekday[dt.weekday()] += 1

    hour_data = [by_hour.get(hour, 0) for hour in range(24)]
    weekday_labels = [
        "Segunda",
        "Terça",
        "Quarta",
        "Quinta",
        "Sexta",
        "Sábado",
        "Domingo",
    ]
    weekday_data = [by_weekday.get(idx, 0) for idx in range(7)]

    top_hour = (
        max(range(24), key=lambda hour: hour_data[hour])
        if any(hour_data)
        else None
    )
    top_weekday = (
        max(range(7), key=lambda day: weekday_data[day])
        if any(weekday_data)
        else None
    )

    return {
        "hours": {
            "labels": [f"{hour:02d}:00" for hour in range(24)],
            "data": hour_data,
            "top": f"{top_hour:02d}:00" if top_hour is not None else None,
        },
        "weekdays": {
            "labels": weekday_labels,
            "data": weekday_data,
            "top": (
                weekday_labels[top_weekday]
                if top_weekday is not None
                else None
            ),
        },
    }


def _parse_iso_date(value: str | None) -> date | None:
    if value is None or value.strip() == "":
        return None

    return date.fromisoformat(value)


def _is_in_period(
    kill_time: datetime,
    start_date: date | None,
    end_date: date | None,
) -> bool:
    kill_day = kill_time.date()
    if start_date is not None and kill_day < start_date:
        return False
    if end_date is not None and kill_day > end_date:
        return False
    return True


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/regions")
def regions():
    return jsonify({"regions": _load_regions()})


@app.get("/api/analysis")
def analysis():
    try:
        region_id = int(request.args.get("region_id", "10000002"))
    except ValueError:
        return jsonify({"error": "region_id inválido"}), 400

    try:
        start_date = _parse_iso_date(request.args.get("start_date"))
        end_date = _parse_iso_date(request.args.get("end_date"))
    except ValueError:
        return (
            jsonify(
                {
                    "error": "start_date/end_date inválido (use YYYY-MM-DD)",
                }
            ),
            400,
        )

    if (
        start_date is not None
        and end_date is not None
        and start_date > end_date
    ):
        return (
            jsonify(
                {
                    "error": "start_date não pode ser maior que end_date",
                }
            ),
            400,
        )

    refresh = request.args.get("refresh", "false").lower() in {
        "1",
        "true",
        "yes",
    }

    cache = _load_cache()
    cache_changed = False

    with requests.Session() as session:
        session.headers.update(HEADERS)

        (
            ganked_kill_refs,
            kills_seen,
            collect_changed,
            collect_stats,
            pages_scanned,
        ) = _collect_ganked_kill_ids(
            region_id,
            session,
            cache,
            refresh,
            start_date,
        )
        cache_changed = cache_changed or collect_changed
        timestamps: list[datetime] = []
        timestamps_in_period: list[datetime] = []
        kill_times_from_api = 0
        kill_times_from_cache = 0

        for kill_id, kill_hash in sorted(ganked_kill_refs.items()):
            if not kill_hash:
                continue
            try:
                kill_time, time_changed, source = _extract_kill_time(
                    kill_id,
                    kill_hash,
                    session,
                    cache,
                    refresh,
                )
            except (requests.RequestException, ValueError, KeyError):
                continue

            if source == "api":
                kill_times_from_api += 1
            else:
                kill_times_from_cache += 1

            cache_changed = cache_changed or time_changed
            if kill_time is not None:
                timestamps.append(kill_time)
                if _is_in_period(kill_time, start_date, end_date):
                    timestamps_in_period.append(kill_time)
            time.sleep(0.15)

    if cache_changed:
        _save_cache(cache)

    aggregated = _aggregate_datetimes(timestamps_in_period)

    return jsonify(
        {
            "region_id": region_id,
            "pages_scanned": pages_scanned,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "cache_file": str(CACHE_FILE),
            "refresh": refresh,
            "total_kills_seen": kills_seen,
            "ganked_kills_found": len(ganked_kill_refs),
            "ganked_in_period": len(timestamps_in_period),
            "ganked_with_timestamp": len(timestamps),
            "source": {
                "pages_from_api": collect_stats["pages_from_api"],
                "pages_from_cache": collect_stats["pages_from_cache"],
                "kill_times_from_api": kill_times_from_api,
                "kill_times_from_cache": kill_times_from_cache,
            },
            "hours": aggregated["hours"],
            "weekdays": aggregated["weekdays"],
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
