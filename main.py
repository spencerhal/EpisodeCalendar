"""
Builds an .ics calendar of upcoming/past episode air dates for a list of
TV shows selected via the local portal (shows.json).

Reads:  shows.json           -> [{"id": 1399, "name": "Game of Thrones"}, ...]
Writes: tv_episode_calendar.ics

Requires env var TMDB_API_KEY (TMDB v3 API key).

Optionally set STREAMING_AVAILABILITY_API_KEY (free key from
https://developers.movieofthenight.com) to link events straight to the show on
its streaming service instead of to TMDB's watch page.
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

# TMDB's provider data comes from JustWatch, and that agreement lets TMDB name
# the service but not link into it - the only URL it exposes is its own watch
# page. Direct links come from this second API instead; without a key we fall
# back to the TMDB page.
STREAMING_API_KEY = os.environ.get("STREAMING_AVAILABILITY_API_KEY")
STREAMING_BASE = "https://api.movieofthenight.com/v4"
STREAMING_COUNTRY = "us"

# Preferred service when a show is on more than one, matched case-insensitively
# against the service name.
PREFERRED_SERVICE = "youtube tv"

# Set to True if you want season 0 (specials) included.
INCLUDE_SPECIALS = False

# Only keep episodes from the last 550 days
EPISODE_DAYS_WINDOW = 550

# Cache for watch providers per show to avoid redundant API calls
WATCH_PROVIDERS_CACHE = {}

# Flipped once the streaming API reports we are out of quota, so one 429 does
# not turn into one failed request per remaining show.
_streaming_quota_exhausted = False


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


def _pick_streaming_option(options):
    """Choose the best option: watchable on a subscription beats pay-per-view."""
    included = [o for o in options if o.get("type") in ("subscription", "free", "addon")]
    candidates = included or options
    if not candidates:
        return None

    for option in candidates:
        if PREFERRED_SERVICE in option.get("service", {}).get("name", "").lower():
            return option
    return candidates[0]


def get_direct_streaming_link(show_id):
    """Deep link straight to the show on its streaming service, or None.

    Returns None whenever the lookup cannot be trusted - no key, show not
    indexed, quota gone, API down - so the caller can fall back to TMDB.
    """
    global _streaming_quota_exhausted

    if not STREAMING_API_KEY or _streaming_quota_exhausted:
        return None

    try:
        resp = requests.get(
            f"{STREAMING_BASE}/shows/tv/{show_id}",
            params={"country": STREAMING_COUNTRY, "series_granularity": "show"},
            headers={"X-API-Key": STREAMING_API_KEY},
            timeout=30,
        )
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            _streaming_quota_exhausted = True
            print(
                "  Streaming API quota exhausted - using TMDB links from here on.",
                file=sys.stderr,
            )
            return None
        resp.raise_for_status()
        options = resp.json().get("streamingOptions", {}).get(STREAMING_COUNTRY, [])
    except (requests.RequestException, ValueError) as e:
        print(f"  Streaming lookup failed for show {show_id}: {e}", file=sys.stderr)
        return None

    option = _pick_streaming_option(options)
    if not option or not option.get("link"):
        return None

    return {
        "link": option["link"],
        "provider": option.get("service", {}).get("name"),
    }


def get_show_watch_info(show_id):
    """Where to watch a show: a direct service link if we can get one, else TMDB."""
    if show_id in WATCH_PROVIDERS_CACHE:
        return WATCH_PROVIDERS_CACHE[show_id]

    res = get_direct_streaming_link(show_id)

    if res is None:
        link = None
        provider_name = None

        try:
            data = tmdb_get(f"/tv/{show_id}/watch/providers")
            us_providers = data.get("results", {}).get("US", {})
            tmdb_link = us_providers.get("link")

            providers = us_providers.get("flatrate", []) + us_providers.get("free", [])

            # Check for YouTube TV first
            for provider in providers:
                if PREFERRED_SERVICE in provider.get("provider_name", "").lower():
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

    if not STREAMING_API_KEY:
        print(
            "Note: STREAMING_AVAILABILITY_API_KEY is not set - "
            "events will link to TMDB instead of the streaming service."
        )

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