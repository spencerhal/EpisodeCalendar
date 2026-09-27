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
from datetime import datetime, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from icalendar import Calendar, Event

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
TMDB_BASE = "https://api.themoviedb.org/3"
SHOWS_FILE = "shows.json"
OUTPUT_FILE = "tv_episode_calendar.ics"

# Target timezone for evaluating air times and 12am-9am window (defaults to US Central)
CALENDAR_TZ_NAME = os.environ.get("CALENDAR_TIMEZONE", "America/Chicago")
try:
    CALENDAR_TZ = ZoneInfo(CALENDAR_TZ_NAME)
except Exception:
    CALENDAR_TZ = ZoneInfo("America/Chicago")

# Default drop times for streaming platforms (in CALENDAR_TZ, e.g. US Central Time).
# Apple TV+ drops shows globally at 9:00 PM ET / 8:00 PM CT (e.g. Ted Lasso, Shrinking, Pluribus).
STREAMING_PLATFORM_DROP_TIMES = {
    "apple": dt_time(20, 0),
    "apple tv": dt_time(20, 0),
    "apple tv+": dt_time(20, 0),
}

# TMDB's provider data comes from JustWatch, and that agreement lets TMDB name
# the service but not link into it - the only URL it exposes is its own watch
# page. Direct links come from this second API instead; without a key we fall
# back to the TMDB page.
STREAMING_API_KEY = os.environ.get("STREAMING_AVAILABILITY_API_KEY")
STREAMING_BASE = "https://api.movieofthenight.com/v4"
STREAMING_COUNTRY = "us"

# Tie-break order when a show is natively on more than one service, best first.
# Ids come from the streaming API's US service list. Reorder to match what you
# actually subscribe to; anything unlisted sorts last.
PREFERRED_SERVICES = [
    "apple",
    "hbo",
    "netflix",
    "disney",
    "hulu",
    "prime",
    "peacock",
    "paramount",
]

# TMDB names providers differently and does list YouTube TV, so the fallback
# path keeps its own preference.
TMDB_PREFERRED_PROVIDER = "youtube tv"

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
            shows.append({
                "id": entry["id"],
                "name": entry.get("name", ""),
                "drop_time": entry.get("drop_time"),
            })
        else:
            shows.append({"id": entry, "name": "", "drop_time": None})
    return shows


def _option_rank(option):
    """Sort key for streaming options, lower is better.

    An "addon" entry means watching one service through another's storefront -
    Ted Lasso shows up as The Roku Channel carrying the Apple TV+ addon - so
    the native service has to outrank it. The API tends to return the native
    option last, which is why order alone is a bad guide.
    """
    if option.get("addon") or option.get("type") == "addon":
        tier = 1
    elif option.get("type") in ("subscription", "free"):
        tier = 0
    else:
        tier = 2  # rent or buy

    service_id = option.get("service", {}).get("id", "")
    preference = (
        PREFERRED_SERVICES.index(service_id)
        if service_id in PREFERRED_SERVICES
        else len(PREFERRED_SERVICES)
    )
    return (tier, preference)


def _pick_streaming_option(options):
    """Best way to watch: native service first, then an addon, then pay-per-view."""
    if not options:
        return None
    # min() is stable, so the API's own order breaks any remaining tie.
    return min(options, key=_option_rank)


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
                if TMDB_PREFERRED_PROVIDER in provider.get("provider_name", "").lower():
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


def get_tvmaze_episode_mapping(show_id, show_name):
    """Fetch show metadata and episode mapping from TVmaze using external IDs or single search.

    Returns a dict of {(season_number, episode_number): metadata_dict}.
    """
    imdb_id = None
    tvdb_id = None
    try:
        ext_ids = tmdb_get(f"/tv/{show_id}/external_ids")
        imdb_id = ext_ids.get("imdb_id")
        tvdb_id = ext_ids.get("tvdb_id")
    except Exception as e:
        print(f"  Warning: failed to fetch TMDB external IDs for {show_name}: {e}", file=sys.stderr)

    tvmaze_show = None
    if imdb_id:
        try:
            resp = requests.get(f"https://api.tvmaze.com/lookup/shows?imdb={imdb_id}", timeout=10)
            if resp.status_code == 200:
                tvmaze_show = resp.json()
        except Exception:
            pass

    if not tvmaze_show and tvdb_id:
        try:
            resp = requests.get(f"https://api.tvmaze.com/lookup/shows?thetvdb={tvdb_id}", timeout=10)
            if resp.status_code == 200:
                tvmaze_show = resp.json()
        except Exception:
            pass

    if not tvmaze_show and show_name:
        try:
            resp = requests.get("https://api.tvmaze.com/singlesearch/shows", params={"q": show_name}, timeout=10)
            if resp.status_code == 200:
                tvmaze_show = resp.json()
        except Exception:
            pass

    ep_mapping = {}
    if tvmaze_show:
        is_network = tvmaze_show.get("network") is not None
        web_channel = tvmaze_show.get("webChannel", {}).get("name") if tvmaze_show.get("webChannel") else None
        network = tvmaze_show.get("network", {}).get("name") if tvmaze_show.get("network") else None
        tvmaze_id = tvmaze_show.get("id")
        if tvmaze_id:
            try:
                resp = requests.get(f"https://api.tvmaze.com/shows/{tvmaze_id}/episodes", timeout=10)
                if resp.status_code == 200:
                    for ep in resp.json():
                        s_num = ep.get("season")
                        e_num = ep.get("number")
                        if s_num is not None and e_num is not None:
                            ep_mapping[(s_num, e_num)] = {
                                "airstamp": ep.get("airstamp"),
                                "airtime": ep.get("airtime"),
                                "runtime": ep.get("runtime"),
                                "is_network": is_network,
                                "web_channel": web_channel,
                                "network": network,
                            }
            except Exception as e:
                print(f"  Warning: failed to fetch TVmaze episodes for {show_name}: {e}", file=sys.stderr)

    return ep_mapping


def fetch_show_episodes(show_id, show_name, drop_time=None):
    """Yield episode dicts for every aired/scheduled episode of a show."""
    details = tmdb_get(f"/tv/{show_id}")
    name = show_name or details.get("name", "Unknown Show")
    seasons = details.get("seasons", [])
    watch_info = get_show_watch_info(show_id)

    # Fetch TVmaze episode mapping to get specific air times
    tvmaze_eps = get_tvmaze_episode_mapping(show_id, name)

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

            ep_num = ep.get("episode_number")
            tv_info = tvmaze_eps.get((season_number, ep_num)) if tvmaze_eps else None

            yield {
                "show_id": show_id,
                "show_name": name,
                "season_number": season_number,
                "episode_number": ep_num,
                "episode_name": ep.get("name") or "TBA",
                "air_date": air_date,
                "overview": ep.get("overview") or "",
                "watch_link": watch_info["link"],
                "provider": watch_info["provider"],
                "drop_time": drop_time,
                "airstamp": tv_info.get("airstamp") if tv_info else None,
                "airtime": tv_info.get("airtime") if tv_info else None,
                "runtime": tv_info.get("runtime") if tv_info else None,
                "is_network": tv_info.get("is_network") if tv_info else False,
                "web_channel": tv_info.get("web_channel") if tv_info else None,
                "network": tv_info.get("network") if tv_info else None,
            }


def is_between_12am_and_9am(dt_local):
    """Check if the time in the target timezone falls between 12:00 AM and 9:00 AM (inclusive).

    12:00 AM is 00:00, 9:00 AM is 09:00.
    """
    t = dt_local.time()
    return dt_time(0, 0) <= t <= dt_time(9, 0)


def resolve_episode_time(ep, date_obj):
    """Attempt to find a start and end time for an episode.

    Returns:
        (dt_start_utc, dt_end_utc, is_timed_event)
        If is_timed_event is False, the episode should be treated as an all-day event.
    """
    code = f"s{ep['season_number']}e{ep['episode_number']}"

    # 1. Custom drop_time configured for the show in shows.json (e.g. "20:00")
    if ep.get("drop_time"):
        try:
            parts = str(ep["drop_time"]).strip().split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            custom_time = dt_time(h, m)
            dt_local = datetime.combine(date_obj, custom_time, tzinfo=CALENDAR_TZ)
            if is_between_12am_and_9am(dt_local):
                return None, None, False
            dt_start = dt_local.astimezone(timezone.utc)
            runtime_mins = ep.get("runtime") or 30
            return dt_start, dt_start + timedelta(minutes=runtime_mins), True
        except Exception as e:
            print(f"  Warning: failed to parse custom drop_time '{ep['drop_time']}' for {ep['show_name']}: {e}", file=sys.stderr)

    # 2. Explicit scheduled airtime from TVmaze (e.g. "20:30", "23:29")
    if ep.get("airtime") and ep.get("airstamp"):
        try:
            airstamp = ep["airstamp"]
            if airstamp.endswith("Z"):
                airstamp = airstamp[:-1] + "+00:00"
            dt_start = datetime.fromisoformat(airstamp).astimezone(timezone.utc)
            dt_local = dt_start.astimezone(CALENDAR_TZ)
            if is_between_12am_and_9am(dt_local):
                return None, None, False
            runtime_mins = ep.get("runtime") or 30
            return dt_start, dt_start + timedelta(minutes=runtime_mins), True
        except Exception as e:
            print(f"  Warning: failed to parse airstamp '{ep.get('airstamp')}' for {ep['show_name']} {code}: {e}", file=sys.stderr)

    # 3. Known streaming platform drop time (e.g. Apple TV+ drops at 8:00 PM Central)
    provider_str = (ep.get("provider") or "").lower()
    web_channel_str = (ep.get("web_channel") or "").lower()
    for plat_key, drop_t in STREAMING_PLATFORM_DROP_TIMES.items():
        if plat_key in provider_str or plat_key in web_channel_str:
            dt_local = datetime.combine(date_obj, drop_t, tzinfo=CALENDAR_TZ)
            if is_between_12am_and_9am(dt_local):
                return None, None, False
            dt_start = dt_local.astimezone(timezone.utc)
            runtime_mins = ep.get("runtime") or 30
            return dt_start, dt_start + timedelta(minutes=runtime_mins), True

    # 4. Fallback TVmaze airstamp (e.g. for network broadcast shows where airtime wasn't explicitly set)
    if ep.get("airstamp") and ep.get("is_network"):
        try:
            airstamp = ep["airstamp"]
            if airstamp.endswith("Z"):
                airstamp = airstamp[:-1] + "+00:00"
            dt_start = datetime.fromisoformat(airstamp).astimezone(timezone.utc)
            dt_local = dt_start.astimezone(CALENDAR_TZ)
            if is_between_12am_and_9am(dt_local):
                return None, None, False
            runtime_mins = ep.get("runtime") or 30
            return dt_start, dt_start + timedelta(minutes=runtime_mins), True
        except Exception as e:
            print(f"  Warning: failed to parse fallback airstamp '{ep.get('airstamp')}' for {ep['show_name']} {code}: {e}", file=sys.stderr)

    return None, None, False


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

        # Check if we should use a specific time block or an all-day event
        dt_start, dt_end, use_time_block = resolve_episode_time(ep, date_obj)

        if use_time_block:
            event.add("dtstart", dt_start)
            event.add("dtend", dt_end)
        else:
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
            all_episodes.extend(
                fetch_show_episodes(show["id"], show["name"], show.get("drop_time"))
            )
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