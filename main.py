"""
Builds an .ics calendar of upcoming/past episode air dates for a list of
TV shows selected via the local portal (shows.json).

Reads:  shows.json           -> [{"id": 1399, "name": "Game of Thrones"}, ...]
Writes: tv_episode_calendar.ics

Requires env var TMDB_API_KEY (TMDB v3 API key).
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from icalendar import Calendar, Event

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
TMDB_BASE = "https://api.themoviedb.org/3"
SHOWS_FILE = "shows.json"
OUTPUT_FILE = "tv_episode_calendar.ics"

# Set to True if you want season 0 (specials) included.
INCLUDE_SPECIALS = False

# Only keep episodes from the last 550 days
EPISODE_DAYS_WINDOW = 550

# Cache for watch providers per show to avoid redundant API calls
WATCH_PROVIDERS_CACHE = {}


def tmdb_get(path, params=None):
    """GET from TMDB, with basic retry on rate limiting."""
    params = dict(params or {})
    params["api_key"] = TMDB_API_KEY

    for attempt in range(3):
        resp = requests.get(f"{TMDB_BASE}{path}", params=params, timeout=30)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "2"))
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()


def load_shows():
    if not os.path.exists(SHOWS_FILE):
        print(f"No {SHOWS_FILE} found - nothing to do.")
        return []
    with open(SHOWS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    shows = []
    for entry in data:
        if isinstance(entry, dict):
            shows.append({"id": entry["id"], "name": entry.get("name", "")})
        else:
            shows.append({"id": entry, "name": ""})
    return shows


def get_show_watch_info(show_id):
    """Fetches streaming availability, prioritizing YouTube TV."""
    if show_id in WATCH_PROVIDERS_CACHE:
        return WATCH_PROVIDERS_CACHE[show_id]

    link = None
    provider_name = None

    try:
        data = tmdb_get(f"/tv/{show_id}/watch/providers")
        us_providers = data.get("results", {}).get("US", {})
        tmdb_link = us_providers.get("link")

        providers = us_providers.get("flatrate", []) + us_providers.get("free", [])
        
        # Check for YouTube TV first
        for provider in providers:
            if "youtube tv" in provider.get("provider_name", "").lower():
                provider_name = provider.get("provider_name")
                link = tmdb_link
                break

        # Fallback to the first available streaming provider if YouTube TV isn't listed
        if not provider_name and providers:
            provider_name = providers[0].get("provider_name")
            link = tmdb_link
        elif not provider_name and tmdb_link:
            link = tmdb_link

    except requests.HTTPError:
        pass

    res = {"link": link, "provider": provider_name}
    WATCH_PROVIDERS_CACHE[show_id] = res
    return res


def fetch_show_episodes(show_id, show_name):
    """Yield episode dicts for every aired/scheduled episode of a show."""
    details = tmdb_get(f"/tv/{show_id}")
    name = show_name or details.get("name", "Unknown Show")
    seasons = details.get("seasons", [])
    watch_info = get_show_watch_info(show_id)

    for season in seasons:
        season_number = season.get("season_number")
        if season_number is None:
            continue
        if season_number == 0 and not INCLUDE_SPECIALS:
            continue

        try:
            season_data = tmdb_get(f"/tv/{show_id}/season/{season_number}")
        except requests.HTTPError:
            continue

        for ep in season_data.get("episodes", []):
            air_date = ep.get("air_date")
            if not air_date:
                continue
            yield {
                "show_id": show_id,
                "show_name": name,
                "season_number": season_number,
                "episode_number": ep.get("episode_number"),
                "episode_name": ep.get("name") or "TBA",
                "air_date": air_date,
                "overview": ep.get("overview") or "",
                "watch_link": watch_info["link"],
                "provider": watch_info["provider"],
            }


def build_calendar(all_episodes):
    cal = Calendar()
    cal.add("prodid", "-//TV Episode Calendar//tv-calendar//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "TV Episode Calendar")
    cal.add("x-wr-timezone", "UTC")

    cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=EPISODE_DAYS_WINDOW)
    kept = 0

    for ep in all_episodes:
        try:
            date_obj = datetime.strptime(ep["air_date"], "%Y-%m-%d").date()
        except ValueError:
            continue

        if date_obj < cutoff_date:
            continue

        event = Event()
        code = f"s{ep['season_number']}e{ep['episode_number']}"
        event.add("summary", f"{ep['show_name']} - {code}")
        event.add("dtstart", date_obj)
        event.add("dtend", date_obj + timedelta(days=1))
        event.add(
            "uid",
            f"tvcal-{ep['show_id']}-{ep['season_number']}-{ep['episode_number']}@tv-calendar",
        )
        event.add("dtstamp", datetime.now(timezone.utc))

        description_parts = []
        if ep["overview"]:
            description_parts.append(ep["overview"])

        if ep["watch_link"]:
            event.add("url", ep["watch_link"])
            provider_str = f" ({ep['provider']})" if ep["provider"] else ""
            description_parts.append(f"\nWatch here{provider_str}: {ep['watch_link']}")

        if description_parts:
            event.add("description", "\n".join(description_parts))

        cal.add_component(event)
        kept += 1

    return cal, kept


def main():
    if not TMDB_API_KEY:
        print("ERROR: TMDB_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    shows = load_shows()
    if not shows:
        cal, _ = build_calendar([])
        with open(OUTPUT_FILE, "wb") as f:
            f.write(cal.to_ical())
        return

    all_episodes = []
    for show in shows:
        print(f"Fetching episodes for {show['name'] or show['id']}...")
        try:
            all_episodes.extend(fetch_show_episodes(show["id"], show["name"]))
        except requests.HTTPError as e:
            print(f"  Skipping show {show['id']}: {e}", file=sys.stderr)

    cal, kept = build_calendar(all_episodes)
    with open(OUTPUT_FILE, "wb") as f:
        f.write(cal.to_ical())

    print(
        f"Fetched {len(all_episodes)} episodes across {len(shows)} shows; "
        f"wrote {kept} within the last {EPISODE_DAYS_WINDOW} days to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()