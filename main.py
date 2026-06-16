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
    # Use the clean, standard URL pattern with Letterboxd's explicit sorting query parameter
    url = f"https://letterboxd.com/{USERNAME}/watchlist/by/added-newest/"
    films = []
    page = 1
    
    print(f"--- Starting Letterboxd Scrape for user: {USERNAME} ---")
    
    while url:
        print(f"Scraping Page {page}: {url}")
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
        if response.status_code != 200:
            print(f"Error: Received status code {response.status_code} from Letterboxd.")
            break
            
        soup = BeautifulSoup(response.text, "html.parser")
        page_films_count = 0
        
        # Modern Letterboxd markup uses li elements with 'poster-container'
        for item in soup.find_all("li", class_="poster-container"):
            # Target the modern React component attributes inside the internal div
            poster_div = item.find("div", class_=lambda x: x and 'poster' in x)
            if not poster_div:
                continue
            
            # Letterboxd updated standard properties to data-item-* tags
            title = poster_div.get("data-item-name") or poster_div.get("data-film-name")
            slug = poster_div.get("data-item-slug") or poster_div.get("data-film-slug") or ""
            
            # Fallback check if data attributes are obscured but an img fallback block exists
            if not title:
                img_tag = poster_div.find("img")
                if img_tag and img_tag.get("alt"):
                    title = img_tag["alt"]

            if title:
                films.append({"title": title, "slug": slug})
                page_films_count += 1
        
        print(f"Found {page_films_count} films on page {page}.")
        
        # Traverse standard pagination footer
        next_link = soup.find("a", class_="next")
        if next_link:
            page += 1
            next_path = next_link['href']
            url = f"https://letterboxd.com{next_path}"
        else:
            url = None
            
    print(f"Total films collected from Letterboxd: {len(films)}\n")
    return films
    
def get_tmdb_release_date(title):
    search_url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title}
    try:
        data = requests.get(search_url, params=params).json()
        if not data.get("results"): 
            print(f"  ⚠️ TMDB: No match found for '{title}'")
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
    except Exception as e:
        print(f"  ❌ TMDB Error parsing '{title}': {e}")
        return None
    return None

def generate_ical(films):
    cal = Calendar()
    cal.add("prodid", "-//Letterboxd Tracker//EN")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", "Letterboxd Watchlist")

    print("--- Matching Films with TMDB Release Dates ---")
    added_events = 0

    for film in films:
        tmdb_info = get_tmdb_release_date(film["title"])
        if not tmdb_info: 
            continue
            
        tmdb_id, release_date = tmdb_info
        
        # Filter for future/recent releases
        if release_date < datetime.now().date() - timedelta(days=30): 
            continue

        slug = film['slug'].strip("/")
        full_url = f"https://letterboxd.com/film/{slug}/" if slug else "https://letterboxd.com"

        event = Event()
        event.add("summary", f"{film['title']}")
        event.add("dtstart", release_date)
        event.add("dtend", release_date + timedelta(days=1))
        event.add("description", full_url)
        event.add("url", full_url)
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
        print("Error: No movies parsed. Skipping calendar compilation.")
