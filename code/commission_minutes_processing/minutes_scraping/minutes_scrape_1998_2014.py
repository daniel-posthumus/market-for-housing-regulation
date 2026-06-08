# =============================================================================
# DEPRECATED — superseded by scrape_minutes.py (consolidated, idempotent scraper).
# Kept only because it records the verified S3 archive page-IDs for 1998–2014.
# scrape_minutes.py covers this era (and saves full HTML, not just <p> text).
# =============================================================================
import requests
from bs4 import BeautifulSoup
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

# let's create a set of locals referring to our directory and working directory 
home = Path.home()
work_dir = (home / 'housing_project')
data = (work_dir / 'data')
minutes = (data / 'meeting_minutes')
minutes_raw = (minutes / 'raw')
minutes_clean = (minutes / 'processed')
code = Path.cwd() 
import os
import time
import requests
from bs4 import BeautifulSoup

## Year -> Index Page URL (manually verified list)
year_urls = {
    "2014": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=3713.html",
    "2013": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=3359.html",
    "2012": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=3057.html",
    "2011": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=2588.html",
    "2010": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=2293.html",
    "2009": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1417.html",
    "2008": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1358.html",
    "2007": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1291.html",
    "2006": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1241.html",
    "2005": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1188.html",
    "2004": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1140.html",
    "2003": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1005.html",
    "2002": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1055.html",
    "2001": "https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1097.html",
    '2000': 'https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1003.html',
    '1999': 'https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1002.html',
    '1998': 'https://sfplanning.s3.amazonaws.com/default/files/meetingarchive/planning_dept/sf-planning.org/index.aspx-page=1001.html',
}

# Output directory root
base_output_dir = minutes_raw
os.makedirs(base_output_dir, exist_ok=True)

def extract_minutes_links_from_div(year_url):
    """Extract correct absolute URLs for minutes pages from a year index page."""
    response = requests.get(year_url)
    soup = BeautifulSoup(response.text, 'html.parser')

    content_div = soup.find('div', id='ctl00_content_Screen')
    if not content_div:
        print("❌ Couldn't find content div.")
        return []

    links = []
    for a in content_div.find_all('a', href=True):
        full_url = urljoin(year_url, a['href'])  # ✅ Proper URL joining
        links.append(full_url)

    return list(set(links))

def scrape_minutes_text(url):
    """Scrape text content from a meeting minutes page."""
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    paragraphs = soup.find_all('p')
    return '\n'.join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

def run_scraper():
    for year, index_url in year_urls.items():
        print(f"\n📅 Processing year {year}")
        year_dir = os.path.join(base_output_dir, year)
        os.makedirs(year_dir, exist_ok=True)

        try:
            meeting_links = extract_minutes_links_from_div(index_url)
            print(f"  🔗 Found {len(meeting_links)} meeting links.")

            for i, meeting_url in enumerate(meeting_links):
                try:
                    print(f"    [{i+1}/{len(meeting_links)}] Scraping {meeting_url}")
                    text = scrape_minutes_text(meeting_url)

                    filename = os.path.join(year_dir, f"minutes_{i+1}.txt")
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write(text)

                    time.sleep(0.5)
                except Exception as e:
                    print(f"    ❌ Failed to scrape {meeting_url}: {e}")
        except Exception as e:
            print(f"❌ Failed to process year {year}: {e}")

    print("\n✅ Done scraping.")

if __name__ == "__main__":
    run_scraper()