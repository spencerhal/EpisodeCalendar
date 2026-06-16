import os
import requests
from icalendar import Calendar, Event
from datetime import datetime, timedelta
from letterboxdpy.watchlist import Watchlist

# Pull credentials securely from GitHub environment
USERNAME = os.environ.get("LB_USERNAME")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
REGION = "US" 

def get_watchlist_films():
    print(f"--- Fetching Letterboxd Watchlist for user: {USERNAME} ---")
    films = []
    
    try:
        # Utilize letterboxdpy to cleanly extract your watchlist dictionary object
        w = Watchlist(USERNAME)
        watchlist_data = w.movies
        
        if not watchlist_data:
            print("No movies found or watchlist is set to private.")
            return films
            
        for movie_id, details in watchlist_data.items():
            title = details.get("name")
            slug = details.get("slug", "")
            if title:
                films.append({"title": title, "slug": slug})
                
        print(f"Successfully collected {len(films)} films from Letterboxd.")
    except Exception as e:
        print(f"❌ Error extracting Letterboxd Watchlist: {e}")
        
    return films
    
def get_tmdb_release_date(title):
    search_url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title}
    try:
        data = requests.get(search_url, params=params).json()
        if not data.get("results"): 
            return None
            
        movie_id = data["results"][0]["id"]
        
        release_url = f"https://api.themoviedb.org/3/movie/{movie_id}/release_dates"
        release_data = requests.get(release_url, params={"api_key": TMDB_API_KEY}).json()
        
        for country in release_data.get("results", []):
            if country["iso_3166_1"] == REGION:
                for release in country["release_dates"]:
                    if release["type"] in [2, 3]:  # Limited or Wide Theatrical Release
                        date_str = release["release_date"].split("T")[0]
                        return movie_id, datetime.strptime(date_str, "%Y-%m-%d").date()
                        
        fallback_date = data["results"][0].get("release_date")
        if fallback_date:
            return movie_id, datetime.strptime(fallback_date, "%Y-%m-%d").date()
    except Exception:
        return None
    return None

def generate_ical(films):
    cal = Calendar()
    cal.add("prodid", "-//Letterboxd Tracker//EN")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", "Letterboxd Watchlist")

    print("\n--- Matching Films with TMDB Release Dates ---")
    added_events = 0

    for film in films:
        tmdb_info = get_tmdb_release_date(film["title"])
        if not tmdb_info: 
            continue
            
        tmdb_id, release_date = tmdb_info
        
        # Filter for upcoming releases or movies out within the last month
        if release_date < datetime.now().date() - timedelta(days=30): 
            continue

        slug = film['slug'].strip("/")
        full_url = f"https://letterboxd.com/film/{slug}/" if slug else "https://letterboxd.com"

        event = Event()
        event.add("summary", f"🎬 {film['title']}")
        event.add("dtstart", release_date)
        event.add("dtend", release_date + timedelta(days=1))
        event.add("description", full_url)
        event.add("url", full_url)
        # Stable UID means if a release date changes on TMDB, your calendar shifts it instead of duplicating
        event.add("uid", f"letterboxd-{tmdb_id}@script.local")
        cal.add_component(event)
        added_events += 1

    if os.path.exists("watchlist_releases.ics"):
        os.remove("watchlist_releases.ics")

    with open("watchlist_releases.ics", "wb") as f:
        f.write(cal.to_ical())
        
    print(f"\n--- Completed! Successfully wrote {added_events} upcoming events to watchlist_releases.ics ---")

if __name__ == "__main__":
    watchlist = get_watchlist_films()
    if watchlist:
        generate_ical(watchlist)
    else:
        print("Error: No movies parsed from Letterboxd. Skipping calendar compilation.")
