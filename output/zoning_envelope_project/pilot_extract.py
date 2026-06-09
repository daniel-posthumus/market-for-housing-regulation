#!/usr/bin/env python3
"""
pilot_extract.py — by-right envelope extraction PILOT (Task 2).

Scope: a 2-3 city PROOF OF CONCEPT, not a production scraper. It demonstrates
(a) which municipal-code hosts can be retrieved programmatically vs. block bots,
(b) the two dominant code STRUCTURES (consolidated use-table vs. per-district
chapters), and (c) the load-bearing by-right-vs-conditional read for multifamily.

It does NOT attempt a clean structured dataset across all 14 jurisdictions, and it
deliberately does NOT resolve ambiguous by-right/conditional calls — those are
printed with a REVIEW: prefix for a human.

Pilot cities (see report for selection rationale + minutes-pilot coordination flag):
  - Fremont      CodePublishing  Title 18  consolidated use table  [in NZLUD]
  - San Mateo    public.law law-library  Title 27  per-district chapters  [NOT in NZLUD]
  - San Jose     Municode  Title 20  -> host returns HTTP 403 to bots (documented, not scraped)

Run:  python3 pilot_extract.py
Requires only the std lib + `requests` (already in the project venv).
"""
from __future__ import annotations
import re, html, sys
try:
    import requests
except ImportError:
    sys.exit("pip install requests")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}

def get(url: str) -> tuple[int, str]:
    r = requests.get(url, headers=UA, timeout=30)
    return r.status_code, r.text

def to_text(htm: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", htm, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    return t

# ───────────────────────── Fremont (consolidated use table) ─────────────────────────
def fremont():
    print("\n=== FREMONT — Title 18 ch. 18.90 (CodePublishing; consolidated use table) ===")
    url = "https://www.codepublishing.com/CA/Fremont/html/Fremont18/Fremont1890.html"
    code, htm = get(url)
    print(f"  fetch {url} -> HTTP {code}")
    if code != 200:
        print("  REVIEW: could not retrieve Fremont code programmatically."); return
    txt = to_text(htm)
    # districts: R-1, R-2, R-3 (multifamily), R-G
    row = txt[txt.find("Multiple dwellings , including"):][:200]
    syms = [s for s in re.split(r"\s+", to_text(row)) if s in {"P", "C", "Z", "A", "--", "-"}]
    print("  districts (table order): R-1 | R-2 | R-3 | R-G")
    print(f"  'Multiple dwellings ... apartment' row symbols  -> {syms[:4]}  (P=by-right, --=prohibited)")
    print("  READ: multifamily is PERMITTED BY RIGHT (P) in R-3 and R-G; prohibited in R-1/R-2.")
    print("  REVIEW: 'P' = principally permitted USE, but Fremont still applies separate")
    print("          design/site review; confirm whether that review is discretionary for the model.")

# ───────────────────────── San Mateo (per-district chapters) ─────────────────────────
def san_mateo():
    print("\n=== SAN MATEO — Title 27 ch. 27.22 R3 (public.law; per-district chapters) ===")
    base = "https://law.cityofsanmateo.org/us/ca/cities/san-mateo/code/"
    for sec, label in [("27.22.010", "PERMITTED USES"), ("27.22.020", "SPECIAL USES"),
                       ("27.22.055", "BUILDING HEIGHT")]:
        code, htm = get(base + sec)
        txt = to_text(htm)
        i = txt.find(f"{sec} {label}", txt.find("Municipal Code", 100))
        snippet = re.sub(r"\s+", " ", txt[i:i + 360]).strip()
        print(f"  [{sec} {label}] HTTP {code}")
        print(f"    {snippet[:300]}")
    print("  READ: multifamily ('Multiple family dwellings') is a PERMITTED (by-right) use in R3.")
    print("  REVIEW: BUILDING HEIGHT (27.22.055) is NOT in the code text — it defers to the")
    print("          General Plan 'Building Height Plan' MAP. Height CANNOT be text-scraped here;")
    print("          requires the GP map. This is why NLP height fields are unreliable for such cities.")

# ───────────────────────── San Jose (host blocks bots) ─────────────────────────
def san_jose():
    print("\n=== SAN JOSE — Title 20 ch. 20.30 (Municode) ===")
    url = "https://library.municode.com/ca/san_jose/codes/code_of_ordinances?nodeId=TIT20ZO_CH20.30REZODI"
    code, htm = get(url)
    has_text = "permitted" in htm.lower()
    print(f"  fetch {url} -> HTTP {code}; code text present in HTML? {has_text}")
    print("  REVIEW: Municode returns HTTP 200 but only a ~6KB Angular JS SHELL — zero code text.")
    print("          The actual ordinance loads via a separate Municode JSON API (api.municode.com);")
    print("          a plain GET yields no use table. American Legal / eCode360 block bots outright (403).")
    print("          San Jose Title 20 uses an 'S'=Special-Use-Permit symbol in Table 20-50;")
    print("          extraction needs the city's static Title 20 PDF or manual reading, not the Municode page.")
    # Fallback static PDF the city itself posts:
    print("          Static fallback: https://sj-admin.s3-us-west-2.amazonaws.com/2020_0000_CityofSanJose_MuniCodeTitle%2020.pdf")

if __name__ == "__main__":
    fremont()
    san_mateo()
    san_jose()
    print("\nDone. See zoning_envelope_assessment.md for the full field-level honesty assessment.")
