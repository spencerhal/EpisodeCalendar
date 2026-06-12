import os
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event
from datetime import datetime, timedelta

# Pull credentials securely from GitHub environment
USERNAME = os.environ.get("LB_USERNAME")
TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
REGION = "US" 

def get_watchlist_films():
    url = f"https://letterboxd.com/{USERNAME}/watchlist/"
    films = []
    while url:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(response.text, "html.parser")
        for poster in soup.find_all("div", class_="poster"):
            title = poster.find("img")["alt"]
            slug = poster['data-film-slug']
            films.append({"title": title, "slug": slug})
        next_link = soup.find("a", class_="next")
        url = f"https://letterboxd.com{next_link['href']}" if next_link else None
    return films

def get_tmdb_release_date(title):
    search_url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title}
    try:
        data = requests.get(search_url, params=params).json()
        if not data.get("results"): return None
        movie_id = data["results"][0]["id"]
        
        release_url = f"https://api.themoviedb.org/3/movie/{movie_id}/release_dates"
        release_data = requests.get(release_url, params={"api_key": TMDB_API_KEY}).json()
        
        for country in release_data.get("results", []):
            if country["iso_3166_1"] == REGION:
                for release in country["release_dates"]:
                    if release["type"] in [2, 3]:
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

    for film in films:
        tmdb_info = get_tmdb_release_date(film["title"])
        if not tmdb_info: continue
        tmdb_id, release_date = tmdb_info
        
        if release_date < datetime.now().date() - timedelta(days=30): continue

        event = Event()
        event.add("summary", f"🎬 {film['title']}")
        event.add("dtstart", release_date)
        event.add("dtend", release_date + timedelta(days=1))
        event.add("description", f"https://letterboxd.com/film/{film['slug']}/")
        event.add("uid", f"letterboxd-{tmdb_id}@script.local")
        cal.add_component(event)

    with open("watchlist_releases.ics", "wb") as f:
        f.write(cal.to_ical())

if __name__ == "__main__":
    watchlist = get_watchlist_films()
    generate_ical(watchlist)
