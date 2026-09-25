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
from datetime import datetime

import requests
from icalendar import Calendar, Event

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
TMDB_BASE = "https://api.themoviedb.org/3"
SHOWS_FILE = "shows.json"
OUTPUT_FILE = "tv_episode_calendar.ics"

# Set to True if you want season 0 (specials) included.
INCLUDE_SPECIALS = False


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
    # Support either a flat list of ids or a list of {id, name} objects.
    shows = []
    for entry in data:
        if isinstance(entry, dict):
            shows.append({"id": entry["id"], "name": entry.get("name", "")})
        else:
            shows.append({"id": entry, "name": ""})
    return shows


def fetch_show_episodes(show_id, show_name):
    """Yield episode dicts for every aired/scheduled episode of a show."""
    details = tmdb_get(f"/tv/{show_id}")
    name = show_name or details.get("name", "Unknown Show")
    seasons = details.get("seasons", [])

    for season in seasons:
        season_number = season.get("season_number")
        if season_number is None:
            continue
        if season_number == 0 and not INCLUDE_SPECIALS:
            continue

        try:
            season_data = tmdb_get(f"/tv/{show_id}/season/{season_number}")
        except requests.HTTPError:
            # Some seasons (e.g. unreleased) can 404 - skip gracefully.
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
            }


def build_calendar(all_episodes):
    cal = Calendar()
    cal.add("prodid", "-//TV Episode Calendar//tv-calendar//")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "TV Episode Calendar")
    cal.add("x-wr-timezone", "UTC")

    for ep in all_episodes:
        try:
            date_obj = datetime.strptime(ep["air_date"], "%Y-%m-%d").date()
        except ValueError:
            continue

        event = Event()
        code = f"S{ep['season_number']:02d}E{ep['episode_number']:02d}"
        event.add("summary", f"{ep['show_name']} - {code} - {ep['episode_name']}")
        event.add("dtstart", date_obj)
        event.add("dtend", date_obj)
        event.add(
            "uid",
            f"tvcal-{ep['show_id']}-{ep['season_number']}-{ep['episode_number']}@tv-calendar",
        )
        event.add("dtstamp", datetime.utcnow())
        if ep["overview"]:
            event.add("description", ep["overview"])
        cal.add_component(event)

    return cal


def main():
    if not TMDB_API_KEY:
        print("ERROR: TMDB_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    shows = load_shows()
    if not shows:
        # Still write an (empty) valid calendar so subscribers don't 404/break.
        cal = build_calendar([])
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

    cal = build_calendar(all_episodes)
    with open(OUTPUT_FILE, "wb") as f:
        f.write(cal.to_ical())

    print(f"Wrote {len(all_episodes)} episodes across {len(shows)} shows to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
