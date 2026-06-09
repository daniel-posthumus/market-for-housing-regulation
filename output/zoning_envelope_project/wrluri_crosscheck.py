#!/usr/bin/env python3
"""
wrluri_crosscheck.py — benchmark NZLUD against WRLURI for the 14-city set.

Resolves REVIEW #11 of zoning_envelope_assessment.md, which was blocked because the
Dropbox WRLURI .dta files were git-LFS pointer stubs. They are now materialized.

WRLURI (Wharton Residential Land Use Regulatory Index) is a SURVEY-based restrictiveness
index; higher = more restrictive. Two waves: 2006 (`WRLURI` in the 2008 .dta) and 2018
(`WRLURI18` in the 2020 .dta). NZLUD's `zri` is a CODE-derived restrictiveness index
(higher = more restrictive); `mf_per` is the share of residential districts permitting
multifamily BY RIGHT (higher = LESS restrictive). NZLUD covers only 4 of the 14 cities.

Join keys: WRLURI 2006 `ufips` and WRLURI18 `GEOID`/`fipsplacecode18` are Census place
FIPS; NZLUD GEOID for CA = 600000 + place FIPS. Name matching handles the "<City>, CA" /
"City of <X>" / trailing-" City" forms (so "Redwood City" is not truncated to "Redwood").

Run:  .venv/bin/python output/zoning_envelope_project/wrluri_crosscheck.py
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path

RAW = Path("/Users/danpost/Library/CloudStorage/Dropbox/market-for-housing-regulation/data/raw")
HERE = Path(__file__).resolve().parent

CITIES = ["Daly City", "South San Francisco", "San Mateo", "Redwood City", "San Bruno",
          "Burlingame", "San Jose", "Palo Alto", "Mountain View", "Sunnyvale",
          "Oakland", "Berkeley", "Fremont", "Richmond"]


def _norm_variants(raw_name: str) -> set[str]:
    """All plausible plain-city forms of a WRLURI name string."""
    s = str(raw_name).strip()
    for suf in (", CA", ", California"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    s = s.strip()
    if s.lower().startswith("city of "):
        s = s[len("city of "):]
    variants = {s}
    if s.endswith(" City"):
        variants.add(s[: -len(" City")])  # "Fremont City" -> "Fremont"
    return {v.strip().lower() for v in variants}


def load_wrluri06() -> pd.DataFrame:
    df = pd.read_stata(RAW / "WHARTON LAND REGULATION DATA_1_24_2008.dta", convert_categoricals=False)
    return df[df["state"].astype(str).str.strip().str.lower() == "california"].copy()


def load_wrluri18() -> pd.DataFrame:
    df = pd.read_stata(RAW / "WRLURI_01_15_2020.dta", convert_categoricals=False)
    return df[df["state"].astype(str).str.upper() == "CA"].copy()


def match_city(city: str, df: pd.DataFrame, name_col: str, idx_col: str):
    """Return (idx_value, fips) for an exact city match, else (None, None)."""
    target = city.lower()
    for _, r in df.iterrows():
        if target in _norm_variants(r[name_col]):
            fips = r.get("ufips") if "ufips" in df.columns else r.get("fipsplacecode18")
            try:
                fips = int(fips)
            except (TypeError, ValueError):
                fips = None
            val = r[idx_col]
            return (None if pd.isna(val) else float(val), fips)
    return (None, None)


def main():
    w06, w18 = load_wrluri06(), load_wrluri18()
    nz = pd.read_csv(HERE / "nzlud_14city_subset.csv")
    nz_by_city = {r["city"]: r for _, r in nz.iterrows()}

    rows = []
    for c in CITIES:
        v06, _ = match_city(c, w06, "name", "WRLURI")
        v18, _ = match_city(c, w18, "communityname18", "WRLURI18")
        nzr = nz_by_city.get(c)
        rows.append({
            "city": c,
            "in_nzlud": nzr is not None,
            "nzlud_zri": round(float(nzr["zri"]), 3) if nzr is not None else None,
            "nzlud_mf_per": round(float(nzr["mf_per"]), 3) if nzr is not None else None,
            "in_wrluri06": v06 is not None,
            "wrluri06": round(v06, 3) if v06 is not None else None,
            "in_wrluri18": v18 is not None,
            "wrluri18": round(v18, 3) if v18 is not None else None,
        })
    out = pd.DataFrame(rows)
    out.to_csv(HERE / "wrluri_crosscheck.csv", index=False)

    print("=== Coverage across the 14 cities ===")
    print(f"  in NZLUD     : {out.in_nzlud.sum()}/14")
    print(f"  in WRLURI06  : {out.in_wrluri06.sum()}/14")
    print(f"  in WRLURI18  : {out.in_wrluri18.sum()}/14")
    print(f"  in ALL THREE : {(out.in_nzlud & out.in_wrluri06 & out.in_wrluri18).sum()}/14")
    print(f"  NZLUD ∩ WRLURI06 (the benchmarkable overlap): {(out.in_nzlud & out.in_wrluri06).sum()}/14")
    print("\n=== Full table ===")
    print(out.to_string(index=False))

    bench = out[out.in_nzlud & out.in_wrluri06].copy()
    print("\n=== Benchmark: NZLUD vs WRLURI 2006 (overlap only) ===")
    print(bench[["city", "wrluri06", "nzlud_zri", "nzlud_mf_per"]].to_string(index=False))
    if len(bench) >= 2:
        sp_zri = bench["wrluri06"].corr(bench["nzlud_zri"], method="spearman")
        sp_mf = bench["wrluri06"].corr(bench["nzlud_mf_per"], method="spearman")
        print(f"\nSpearman rank corr (n={len(bench)}):")
        print(f"  WRLURI06 vs NZLUD zri    : {sp_zri:+.2f}  (expect POSITIVE; both 'more restrictive' = higher)")
        print(f"  WRLURI06 vs NZLUD mf_per : {sp_mf:+.2f}  (expect NEGATIVE; mf_per is inverse restrictiveness)")
        print("  NOTE: n is tiny and vintages differ (WRLURI 2006 vs NZLUD 2019-21); not a validation.")


if __name__ == "__main__":
    main()
