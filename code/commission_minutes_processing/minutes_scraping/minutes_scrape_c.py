# =============================================================================
# DEPRECATED — superseded by scrape_minutes.py. This date-string -> URL approach
# (www.sfgov.org/sfplanningarchive/...) no longer reflects the live site.
# =============================================================================
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
from pathlib import Path
import time

# === Set up paths ===
home = Path.home()
work_dir = home / "housing_project"
data = work_dir / "data"
minutes = data / "meeting_minutes"
minutes_raw = minutes / "raw"
minutes_clean = minutes / "processed"
text_dir = minutes / "text"
text_dir.mkdir(parents=True, exist_ok=True)

# === Constants ===
ARCHIVE_URL = "https://sfplanning.org/cpc-hearing-archives"
BASE_URL = "https://www.sfgov.org/sfplanningarchive/meeting/planning-commission-{}-minutes"
HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

# === Helper Functions ===
def fetch_meeting_dates():
    """Scrape all meeting dates from the archive page."""
    response = requests.get(ARCHIVE_URL, headers=HEADERS)
    soup = BeautifulSoup(response.text, 'html.parser')
    text = soup.get_text()

    # Match dates like July 20, 2017 or October 5, 2006
    date_pattern = r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})"
    date_strings = re.findall(date_pattern, text)

    unique_dates = list(set(date_strings))
    print(f"🔎 Found {len(unique_dates)} unique date strings.")
    return unique_dates

def format_date_for_url(date_str):
    """Convert 'Month Day, Year' to 'month-day-year' lowercase."""
    try:
        dt = datetime.strptime(date_str, "%B %d, %Y")
        return dt.strftime("%B-%d-%Y").lower()
    except Exception as e:
        print(f"⚠️ Failed to parse date: {date_str} — {e}")
        return None

def scrape_minutes(url):
    """Scrape and return text content from the meeting minutes page."""
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    paragraphs = soup.find_all('p')
    text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    return text

def run():
    dates = fetch_meeting_dates()

    for date_str in dates:
        formatted = format_date_for_url(date_str)
        if not formatted:
            continue

        try:
            # Extract year from date string
            year = datetime.strptime(date_str, "%B %d, %Y").year
        except Exception as e:
            print(f"⚠️ Could not parse year from date: {date_str} — {e}")
            continue

        url = BASE_URL.format(formatted)
        print(f"🌐 Checking: {url}")

        try:
            text = scrape_minutes(url)
            if text:
                # Create year subdirectory
                year_dir = minutes_raw / str(year)
                year_dir.mkdir(parents=True, exist_ok=True)

                # Save the file
                filename = year_dir / f"{formatted}.txt"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(text)

                print(f"✅ Saved: {filename}")
            else:
                print(f"❌ No content found at: {url}")
        except Exception as e:
            print(f"❌ Failed to process {url}: {e}")

        time.sleep(0.5)  # Politeness delay

    print("\n🎉 Done.")

if __name__ == "__main__":
    run()