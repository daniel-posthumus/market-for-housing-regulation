# Minutes scraping

`scrape_minutes.py` is the single, maintained scraper. The other three files are
**deprecated** (kept only for their hardcoded URL records). It is idempotent: a
content-hash manifest at `data/meeting_minutes/raw/_manifest.json` lets re-runs skip
already-downloaded files, so the existing ~18 GB corpus is never re-fetched or clobbered.

## Usage
```bash
python scrape_minutes.py --list                  # show coverage, fetch nothing
python scrape_minutes.py --year 2010 --dry-run   # preview a year
python scrape_minutes.py --year 2003-2008        # fetch a range
python scrape_minutes.py --era modern            # fetch all 2015+ PDFs
python scrape_minutes.py                          # fetch everything (idempotent)
python scrape_minutes.py --year 2010 --refresh    # force re-download
```

## Coverage (verified live 2026-06-05)

| Era | Years | Source | Status | Method |
|---|---|---|---|---|
| HTML | 1998–2014 | `sfplanning.s3.amazonaws.com/.../index.aspx-page=NNNN.html` (one index page per year, IDs in `YEAR_INDEX`) | ✅ all 17 index pages return HTTP 200 | fetch index → links from `div#ctl00_content_Screen` → save each meeting as raw HTML |
| PDF | 2015–present | `sfplanning.org/cpc-hearing-archives` → links to `citypln-m-extnl.sfgov.org/Commissions/Agenda_or_Minutes/YYYYMMDD_{cal,cpc}_min.pdf` | ✅ archive page lists 129 minutes PDFs (incl. 2026) | parse archive page once → download each PDF, bucketed by year |

Validation performed: `--year 2010 --dry-run` correctly enumerated all 46 meeting
pages (matching the 46 files on disk); a real `--year 2026 --era modern` run fetched 8
new PDFs, a re-run skipped all 8, and the files extract as genuine minutes.

## Notes / caveats
- The S3 page-IDs (`YEAR_INDEX`) are opaque and were verified by hand; if S3 ever
  retires them, the HTML era would need re-discovery (the only manual-fallback risk).
- The modern archive page is the single point of truth for 2015+; if it stops listing
  older years, those PDFs are already in the corpus (and on the same stable host, so a
  filename-pattern fallback `…/Agenda_or_Minutes/YYYYMMDD_cpc_min.pdf` would still work).
- Politeness: shared `requests.Session`, ret/backoff on 429/5xx, 0.5 s delay per file.
