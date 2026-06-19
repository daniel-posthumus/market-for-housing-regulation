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
python scrape_minutes.py --repair --dry-run       # list corrupt local PDFs + their source
python scrape_minutes.py --repair                 # re-download only the corrupt PDFs
```

## Self-healing (`--repair`) and corruption guard

Every fetched `.pdf` is now validated against the `%PDF` magic before it's saved, so a
truncated download or an HTML error page served with a `.pdf` name is rejected instead of
silently entering the corpus (44 such garbage files were found and fixed on 2026-06-16).
`--repair` scans the modern raw PDFs already on disk, finds any that aren't real PDFs, and
re-downloads each from the archive — matching by filename and validating the replacement —
without touching the good files. It's idempotent and the recommended fix after a flaky run.

## Coverage (verified live 2026-06-05)

| Era | Years | Source | Status | Method |
|---|---|---|---|---|
| HTML | 1998–2014 | `sfplanning.s3.amazonaws.com/.../index.aspx-page=NNNN.html` (one index page per year, IDs in `YEAR_INDEX`) | ✅ all 17 index pages return HTTP 200 | fetch index → links from `div#ctl00_content_Screen` → save each meeting as raw HTML |
| PDF | 2015–present | `sfplanning.org/cpc-hearing-archives` → minutes links across **three** hosts (`citypln-m-extnl.sfgov.org/Commissions/Agenda_or_Minutes/…`, `commissions.sfplanning.org/cpcpackets/…`, `sfplanning.org/sites/default/files/agendas/…`) | ✅ archive page lists **295** minutes PDFs (1998–2026) | identify minutes by anchor text "Minutes" / `_min.pdf` (host-agnostic), download bucketed by the date in the filename |

Validation performed: `--year 2010 --dry-run` correctly enumerated all 46 meeting
pages (matching the 46 files on disk); a real `--year 2026 --era modern` run fetched 8
new PDFs, a re-run skipped all 8, and the files extract as genuine minutes.

## Notes / caveats
- The S3 page-IDs (`YEAR_INDEX`) are opaque and were verified by hand; if S3 ever
  retires them, the HTML era would need re-discovery (the only manual-fallback risk).
- The modern archive page is the single point of truth for 2015+. The harvester keys on
  the **anchor text** ("Minutes" vs "Agenda") rather than a host/path pattern, so it is
  robust to the site serving older years from different hosts (the earlier host-specific
  regex saw only 129 of 295 minutes, which is how 2019–2021 went missing/corrupt).
- Politeness: shared `requests.Session`, ret/backoff on 429/5xx, 0.5 s delay per file.
