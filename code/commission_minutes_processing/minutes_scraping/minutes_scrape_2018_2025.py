# =============================================================================
# DEPRECATED — superseded by scrape_minutes.py. The guessed cpc-hearing-archives
# URL patterns here were unreliable; scrape_minutes.py instead parses the live
# archive page for the stable citypln-m-extnl.sfgov.org minutes-PDF links.
# =============================================================================
import requests
from bs4 import BeautifulSoup
from pathlib import Path
import re
import time
import os
import logging
import zlib
from urllib.parse import urljoin, urlparse

# Set up logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# let's create a set of locals referring to our directory and working directory 
home = Path.home()
work_dir = (home / 'housing_project')
data = (work_dir / 'data')
minutes = (data / 'meeting_minutes')
minutes_raw = (minutes / 'raw')
minutes_clean = (minutes / 'processed')
code = Path.cwd() 

# Define the base URL and year-specific archive pages
BASE_URL = "https://sfplanning.org"
MAIN_ARCHIVE_URL = f"{BASE_URL}/cpc-hearing-archives"

# The archive is organized by years, with each year having its own page
# We'll need to check all year pages from 2003 to present
YEAR_RANGE = range(2003, 2026)  # Adjust end year as needed

# Define the local directory to save PDFs
OUTPUT_DIR = minutes_raw
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

# Set up headers to mimic a browser visit
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

def fetch_page(url):
    """Fetches the content of a given URL with error handling."""
    logger.info(f"Fetching: {url}")
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        
        # Check content type
        content_type = response.headers.get('Content-Type', '').lower()
        if 'application/pdf' in content_type:
            logger.warning(f"URL returned PDF content instead of HTML: {url}")
            return None
            
        # Return the content
        return response.text
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error with {url}: {e}")
        return None

def parse_html_safely(html_content, parser="html.parser"):
    """Safely parse HTML content with error handling."""
    if not html_content:
        return None
        
    try:
        return BeautifulSoup(html_content, parser)
    except Exception as e:
        logger.error(f"Error parsing HTML with {parser}: {e}")
        # Try alternative parsers
        alternative_parsers = ["lxml", "html5lib", "html.parser"]
        for alt_parser in alternative_parsers:
            if alt_parser != parser:
                try:
                    logger.info(f"Trying alternative parser: {alt_parser}")
                    return BeautifulSoup(html_content, alt_parser)
                except Exception as e2:
                    logger.error(f"Error with parser {alt_parser}: {e2}")
        return None

def is_valid_pdf_url(url):
    """Check if a URL is likely a valid PDF link."""
    # Must end with .pdf
    if not url.lower().endswith('.pdf'):
        return False
        
    # Check for common invalid patterns
    invalid_patterns = [
        r'example\.pdf$',
        r'template\.pdf$',
        r'sample\.pdf$'
    ]
    
    for pattern in invalid_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return False
            
    return True

def construct_year_urls():
    """Construct URLs for each year's archive based on common patterns."""
    urls = []
    
    # Try several URL patterns for each year
    for year in YEAR_RANGE:
        # Pattern 1: Year as a path segment
        urls.append(f"{MAIN_ARCHIVE_URL}/{year}")
        
        # Pattern 2: Year as a query parameter
        urls.append(f"{MAIN_ARCHIVE_URL}?year={year}")
        
        # Pattern 3: Year in another common format
        urls.append(f"{BASE_URL}/meetings/planning-commission/{year}")

    return urls

def find_minutes_links_on_page(html_content, base_url):
    """Find all potential minutes PDF links on a page."""
    soup = parse_html_safely(html_content)
    if not soup:
        return []
        
    minutes_links = []
    
    # Look for all links on the page
    for a_tag in soup.find_all("a"):
        href = a_tag.get("href")
        text = a_tag.text.strip() if a_tag.text else ""
        
        if not href:
            continue
            
        # Normalize the URL
        if not href.startswith(('http://', 'https://')):
            href = urljoin(base_url, href)
        
        # Check if it's a PDF link
        if href.lower().endswith('.pdf'):
            # Check if it might be minutes - either in the link text or URL
            if (re.search(r"minute", text, re.IGNORECASE) or 
                re.search(r"minute", href, re.IGNORECASE)):
                
                if is_valid_pdf_url(href):
                    minutes_links.append(href)
    
    return minutes_links

def download_pdfs(links, output_dir):
    """Downloads each PDF from the list of links into year-based subfolders."""
    logger.info(f"Preparing to download {len(links)} files...")

    downloaded = 0
    already_exists = 0
    errors = 0

    for idx, link in enumerate(links, start=1):
        try:
            # Extract the filename and year
            parsed_url = urlparse(link)
            filename = os.path.basename(parsed_url.path)
            filename = requests.utils.unquote(filename)
            filename = re.sub(r'[\\/*?:"<>|]', "_", filename)

            # Try to extract year from the link
            year_match = re.search(r'(\d{4})', link)
            year = year_match.group(1) if year_match else "unknown"

            # Create year subfolder
            year_folder = output_dir / year
            year_folder.mkdir(parents=True, exist_ok=True)
            file_path = year_folder / filename

            if file_path.exists():
                logger.info(f"[{idx}/{len(links)}] Already exists: {filename}")
                already_exists += 1
                continue

            logger.info(f"[{idx}/{len(links)}] Downloading: {link}")
            response = requests.get(link, headers=HEADERS, stream=True)
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', '').lower()
            if 'application/pdf' not in content_type:
                logger.warning(f"Not a PDF (content-type: {content_type}): {link}")
                continue

            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            logger.info(f"Downloaded: {file_path}")
            downloaded += 1
            time.sleep(1)

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download {link}: {e}")
            errors += 1
        except Exception as e:
            logger.error(f"Error processing {link}: {e}")
            errors += 1

    return {
        "downloaded": downloaded,
        "already_exists": already_exists,
        "errors": errors
    }

def group_links_by_year(links):
    """Group links by year for reporting."""
    year_groups = {}
    for link in links:
        # Try to extract year from link
        year_match = re.search(r'(\d{4})', link)
        if year_match:
            year = year_match.group(1)
            if year not in year_groups:
                year_groups[year] = []
            year_groups[year].append(link)
    return year_groups

def main():
    all_minutes_links = []
    
    # First approach: Try the main archive page
    logger.info("Trying main archive page...")
    html_content = fetch_page(MAIN_ARCHIVE_URL)
    if html_content:
        links = find_minutes_links_on_page(html_content, MAIN_ARCHIVE_URL)
        logger.info(f"Found {len(links)} minutes links on main page")
        all_minutes_links.extend(links)
    
    # Second approach: Try year-specific URLs
    year_urls = construct_year_urls()
    
    logger.info(f"Trying {len(year_urls)} year-specific URLs...")
    for url in year_urls:
        html_content = fetch_page(url)
        if html_content:
            links = find_minutes_links_on_page(html_content, url)
            logger.info(f"Found {len(links)} minutes links from {url}")
            all_minutes_links.extend(links)
            time.sleep(1)  # Be polite between requests
    
    # Remove duplicates
    unique_links = []
    for link in all_minutes_links:
        if link not in unique_links:
            unique_links.append(link)
    
    logger.info(f"Total unique minutes PDF links found: {len(unique_links)}")
    
    # Group by year for reporting
    year_groups = group_links_by_year(unique_links)
    logger.info("\nSummary of minutes found by year:")
    for year in sorted(year_groups.keys()):
        logger.info(f"Year {year}: {len(year_groups[year])} files")
    
    if not unique_links:
        logger.error("No minutes PDF links found. Please check the website structure.")
        return
        
    # Ask for confirmation before downloading
    confirm = input(f"Found {len(unique_links)} 'Minutes' PDFs. Continue with download? (y/n): ")
    if confirm.lower() != 'y':
        logger.info("Download cancelled.")
        return

    results = download_pdfs(unique_links, OUTPUT_DIR)
    logger.info(f"Download completed. Statistics:")
    logger.info(f"  - New files downloaded: {results['downloaded']}")
    logger.info(f"  - Files already existed: {results['already_exists']}")
    logger.info(f"  - Errors encountered: {results['errors']}")

if __name__ == "__main__":
    main()