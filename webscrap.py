"""
sacnilk_scraper.py  (v2 — fixed URL structure)
------------------------------------------------
Scrapes pan-India box office data from sacnilk.com using the REAL URL patterns:

  Individual movie page:
    sacnilk.com/news/{Title_With_Underscores}_{Year}_Box_Office_Collection_Day_Wise_Worldwide

  Example:
    sacnilk.com/news/Pushpa_The_Rule_Part_2_2024_Box_Office_Collection_Day_Wise_Worldwide
    sacnilk.com/news/Jawan_2023_Box_Office_Collection_Day_Wise_Worldwide

  Listing pages (used to discover more slugs):
    sacnilk.com/box-office-collections
    sacnilk.com/entertainmenttopbar/Box_Office
    sacnilk.com/entertainmenttopbar2/RecentMoviesCollection

Strategy:
  Stage 1 – Use hardcoded seed list of known 50+ major releases
  Stage 2 – Crawl listing pages to discover more movie URLs
  Stage 3 – Fetch each movie page and parse the Day 1–7 collection table

Usage:
    pip install requests beautifulsoup4 pandas tqdm
    python sacnilk_scraper.py                        # 500 movies, 2022-2025
    python sacnilk_scraper.py --target 600 --years 2023 2024 2025
    python sacnilk_scraper.py --validate-only
"""

import re
import time
import logging
import random
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional

import requests
import pandas as pd
from bs4 import BeautifulSoup
from tqdm import tqdm

# ─── Config ──────────────────────────────────────────────────────────────────

BASE_URL   = "https://www.sacnilk.com"
HEADERS    = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.sacnilk.com/",
}
DELAY_MIN  = 1.2
DELAY_MAX  = 2.8
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(OUTPUT_DIR / "scrape_log.txt"),
    ],
)
log = logging.getLogger(__name__)

# ─── Data models ─────────────────────────────────────────────────────────────

@dataclass
class MovieMeta:
    movie_id:      str
    title:         str
    language:      str
    genre:         str
    release_date:  str
    screens_india: int
    budget_cr:     float
    cast_tier:     str
    director:      str
    lifetime_cr:   float
    week1_net_cr:  float
    india_gross_cr: float
    worldwide_cr:  float
    verdict:       str
    data_complete: bool

@dataclass
class DailyRow:
    movie_id:     str
    title:        str
    day:          int
    date:         str
    india_net_cr: float

# ─── Cast tier ────────────────────────────────────────────────────────────────

A_LIST = {
    "shah rukh khan","salman khan","aamir khan","akshay kumar","hrithik roshan",
    "ranveer singh","ranbir kapoor","ajay devgn","vijay","thalapathy vijay",
    "allu arjun","ram charan","prabhas","mahesh babu","yash","suriya",
    "rajinikanth","kamal haasan","vijay sethupathi","dhanush","fahadh faasil",
    "mohanlal","mammootty","tovino thomas","dulquer salmaan","shahid kapoor",
    "jr ntr","jr. ntr","chiranjeevi","pawan kalyan",
}
B_LIST = {
    "tiger shroff","varun dhawan","john abraham","kartik aaryan",
    "sidharth malhotra","vicky kaushal","ayushmann khurrana","vikrant massey",
    "silambarasan","vikram","jayam ravi","vishal","nani","adivi sesh",
    "sundeep kishan","rana daggubati","gopichand",
}

def get_cast_tier(text: str) -> str:
    low = text.lower()
    if any(n in low for n in A_LIST): return "A"
    if any(n in low for n in B_LIST): return "B"
    return "C"

# ─── HTTP ─────────────────────────────────────────────────────────────────────

session = requests.Session()
session.headers.update(HEADERS)

def get_soup(url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=20)
            if resp.status_code == 200:
                return BeautifulSoup(resp.text, "html.parser")
            if resp.status_code in (404, 410):
                log.debug(f"HTTP {resp.status_code}: {url}")
                return None
            log.warning(f"HTTP {resp.status_code} attempt {attempt+1}: {url}")
        except Exception as e:
            log.warning(f"Request error attempt {attempt+1}: {e}")
        time.sleep(2 ** attempt + random.uniform(0, 1))
    return None

def polite_sleep():
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

# ─── Helpers ──────────────────────────────────────────────────────────────────

def parse_cr(text: str) -> float:
    if not text: return 0.0
    text = re.sub(r"[₹\u20b9,]", "", text).strip()
    m = re.search(r"([\d]+\.?[\d]*)", text)
    return float(m.group(1)) if m else 0.0

def extract_date(text: str) -> str:
    months = {
        "january":"01","february":"02","march":"03","april":"04","may":"05",
        "june":"06","july":"07","august":"08","september":"09","october":"10",
        "november":"11","december":"12","jan":"01","feb":"02","mar":"03",
        "apr":"04","jun":"06","jul":"07","aug":"08","sep":"09","oct":"10",
        "nov":"11","dec":"12",
    }
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", text)
    if m:
        d, mon, y = m.group(1), m.group(2).lower(), m.group(3)
        if mon in months:
            return f"{y}-{months[mon]}-{int(d):02d}"
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""

# ─── Known slugs seed list ────────────────────────────────────────────────────
# These were confirmed from Google search results — they definitely exist on sacnilk.

KNOWN_SLUGS = [
    # 2025
    "Chhaava_2025_Box_Office_Collection_Day_Wise_Worldwide",
    "Sky_Force_2025_Box_Office_Collection_Day_Wise_Worldwide",
    "Thandel_2025_Box_Office_Collection_Day_Wise_Worldwide",
    "Deva_2024_Box_Office_Collection_Day_Wise_Worldwide",
    # 2024
    "Pushpa_The_Rule_Part_2_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Stree_2_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Kalki_2898_AD_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Singham_Again_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Bhool_Bhulaiyaa_3_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Kanguva_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Vettaiyan_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Devara_Part_1_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Amaran_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "The_Greatest_of_All_Time_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "HanuMan_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "UI_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Marco_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Game_Changer_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Baby_John_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Mufasa_The_Lion_King_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Raayan_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Kill_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Maidaan_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Bade_Miyan_Chote_Miyan_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Crew_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Shaitaan_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Article_370_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Fighter_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Guntur_Kaaram_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "KA_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "SWAG_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "I_Want_To_Talk_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Khel_Khel_Mein_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Vedaa_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Sarfira_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Vaazhai_2024_Box_Office_Collection_Day_Wise_Worldwide",
    "Shraddha_2024_Box_Office_Collection_Day_Wise_Worldwide",
    # 2023
    "Jawan_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Pathaan_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Gadar_2_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Animal_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Dunki_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Tiger_3_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Sam_Bahadur_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Leo_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Salaar_Cease_Fire_Part_1_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Jailer_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Ponniyin_Selvan_2_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Adipurush_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Tu_Jhoothi_Main_Makkar_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Kisi_Ka_Bhai_Kisi_Ki_Jaan_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Varisu_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Thunivu_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Waltair_Veerayya_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Veera_Simha_Reddy_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Dasara_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "OMG_2_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Mission_Majnu_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Bholaa_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Shehzada_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Thank_You_For_Coming_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "The_Vaccine_War_2023_Box_Office_Collection_Day_Wise_Worldwide",
    "Kuttey_2023_Box_Office_Collection_Day_Wise_Worldwide",
    # 2022
    "KGF_Chapter_2_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "RRR_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Brahmastra_Part_One_Shiva_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Drishyam_2_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Vikram_Vedha_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "The_Kashmir_Files_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Bhool_Bhulaiyaa_2_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Gangubai_Kathiawadi_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Jugjugg_Jeeyo_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Kantara_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Laal_Singh_Chaddha_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Ponniyin_Selvan_Part_1_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Karthikeya_2_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Sarkaru_Vaari_Paata_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Bheemla_Nayak_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "777_Charlie_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Vikrant_Rona_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Liger_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Shamshera_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Raksha_Bandhan_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "F3_Fun_and_Frustration_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Acharya_2022_Box_Office_Collection_Day_Wise_Worldwide",
    "Ramarao_On_Duty_2022_Box_Office_Collection_Day_Wise_Worldwide",
]

# ─── Stage 2: Listing page crawler ───────────────────────────────────────────

MOVIE_PAGE_PATTERN = re.compile(
    r"/news/([A-Za-z0-9_]+_\d{4}_Box_Office_Collection_Day_Wise_Worldwide)",
    re.I,
)

def crawl_listing_page(url: str) -> list[dict]:
    soup = get_soup(url)
    if not soup:
        return []
    stubs = []
    seen = set()
    for a in soup.find_all("a", href=MOVIE_PAGE_PATTERN):
        m = MOVIE_PAGE_PATTERN.search(a["href"])
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        year_m = re.search(r"_(\d{4})_", slug)
        year = int(year_m.group(1)) if year_m else 0
        title = a.get_text(strip=True) or slug.replace("_", " ").split(" Box Office")[0]
        stubs.append({
            "slug":  slug,
            "url":   f"{BASE_URL}/news/{slug}",
            "title": title,
            "year":  year,
        })
    log.info(f"  {len(stubs)} stubs from {url}")
    return stubs

def discover_stubs_from_listings() -> list[dict]:
    listing_urls = [
        f"{BASE_URL}/box-office-collections",
        f"{BASE_URL}/entertainmenttopbar/Box_Office",
        f"{BASE_URL}/entertainmenttopbar2/RecentMoviesCollection",
        f"{BASE_URL}/collections",
    ]
    # Also try paginated
    for p in range(2, 15):
        listing_urls.append(f"{BASE_URL}/box-office-collections?page={p}")

    all_stubs = []
    for url in listing_urls:
        batch = crawl_listing_page(url)
        all_stubs.extend(batch)
        polite_sleep()
        if not batch and "page=" in url:
            break   # stop paginating when empty
    return all_stubs

# ─── Stage 3: Parse individual movie page ────────────────────────────────────

LANG_KEYWORDS = {
    "Bollywood": "Hindi",  "Hindi": "Hindi",
    "Kollywood": "Tamil",  "Tamil": "Tamil",
    "Tollywood": "Telugu", "Telugu": "Telugu",
    "Sandalwood": "Kannada", "Kannada": "Kannada",
    "Mollywood": "Malayalam", "Malayalam": "Malayalam",
    "Marathi": "Marathi",  "Punjabi": "Punjabi",
}

def parse_movie_page(stub: dict) -> tuple[Optional[MovieMeta], list[DailyRow]]:
    soup = get_soup(stub["url"])
    if not soup:
        return None, []

    slug      = stub["slug"]
    full_text = soup.get_text(" ", strip=True)

    # ── Title ──
    h1    = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else stub.get("title", "")
    title = re.sub(r"\s*box office.*", "", title, flags=re.I).strip() or stub.get("title", "")

    # ── Language ──
    language = "Unknown"
    for kw, lang in LANG_KEYWORDS.items():
        if re.search(rf"\b{kw}\b", full_text, re.I):
            language = lang
            break

    # ── Release date ──
    release_date = ""
    dm = re.search(r"Release Date\s*[:\-]?\s*(.{5,30}?)(?:\s+For more|\s+##|\n|$)", full_text, re.I)
    if dm:
        release_date = extract_date(dm.group(1))

    # ── Screens ──
    screens = 0
    sm = re.search(r"India[^0-9]*(\d[\d,]+)\s*(?:\*\s*)?(?:screens?|Screens?)", full_text)
    if sm:
        try: screens = int(sm.group(1).replace(",", ""))
        except: pass

    # ── Budget ──
    budget_cr = 0.0
    bm = re.search(r"Budget\s*[:\-]?\s*[\u20b9₹]?\s*([\d.]+)\s*Cr", full_text, re.I)
    if bm:
        try: budget_cr = float(bm.group(1))
        except: pass

    # ── Genre ──
    genre = "Drama"
    for g in ["Action","Comedy","Thriller","Horror","Romance","SciFi","Historical","Sports","Crime"]:
        if re.search(rf"\b{g}\b", full_text, re.I):
            genre = g
            break

    # ── Director ──
    director = ""
    dm2 = re.search(r"directed by ([A-Z][a-zA-Z ]{2,30}?)(?:\s+and|\s+produced|\.|,)", full_text)
    if dm2:
        director = dm2.group(1).strip()

    # ── Cast tier ──
    cm = re.search(r"stars\s+([A-Z][a-zA-Z ,\.]+?)(?:\s+in key roles|\s+as\s+)", full_text)
    cast_text = cm.group(1) if cm else full_text[:600]
    tier = get_cast_tier(cast_text)

    # ── Totals ──
    def find_cr(pattern: str) -> float:
        m = re.search(pattern, full_text, re.I)
        if m:
            try: return float(m.group(1))
            except: pass
        return 0.0

    india_net_cr   = find_cr(r"India Net Collection\s*[\u20b9₹]?\s*([\d.]+)\s*Cr")
    worldwide_cr   = find_cr(r"Worldwide Collection\s*[\u20b9₹]?\s*([\d.]+)\s*Cr")
    india_gross_cr = find_cr(r"India Gross Collection\s*[\u20b9₹]?\s*([\d.]+)\s*Cr")

    verdict = ""
    vm = re.search(r"Verdict\s*[:\-]?\s*([A-Za-z ]{3,25}?)(?:\s+##|\n|$)", full_text, re.I)
    if vm:
        verdict = vm.group(1).strip()

    # ── Day 1–7 collection table ──
    # Actual sacnilk format (confirmed from search results):
    # "Day 1 [1st Friday] ₹ 20.5 Cr" or "Day 1 [1st Friday]₹ 20.5 Cr -"
    day_pattern = re.compile(
        r"Day\s+(\d+)\s*\[[^\]]*\]\s*[\u20b9₹]?\s*([\d]+\.?[\d]*)\s*Cr",
        re.I,
    )

    daily_rows: list[DailyRow] = []
    seen_days:  set[int] = set()
    week1_net = 0.0

    for m in day_pattern.finditer(full_text):
        day_num = int(m.group(1))
        cr_val  = float(m.group(2))
        if day_num < 1 or day_num > 7 or cr_val <= 0:
            continue
        if day_num in seen_days:
            continue
        seen_days.add(day_num)

        day_date = ""
        if release_date:
            try:
                base = datetime.strptime(release_date, "%Y-%m-%d")
                day_date = (base + timedelta(days=day_num - 1)).strftime("%Y-%m-%d")
            except ValueError:
                pass

        daily_rows.append(DailyRow(
            movie_id=slug, title=title, day=day_num,
            date=day_date, india_net_cr=cr_val,
        ))
        week1_net += cr_val

    # Fallback: HTML table rows
    if not daily_rows:
        for tr in soup.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            c0 = cells[0].get_text(strip=True)
            dm3 = re.search(r"Day\s+(\d+)", c0, re.I)
            if not dm3:
                continue
            day_num = int(dm3.group(1))
            if not (1 <= day_num <= 7) or day_num in seen_days:
                continue
            for cell in cells[1:]:
                ct = cell.get_text(strip=True)
                cv = re.search(r"([\d]+\.?[\d]*)", ct)
                if cv and float(cv.group(1)) > 0:
                    seen_days.add(day_num)
                    daily_rows.append(DailyRow(
                        movie_id=slug, title=title, day=day_num,
                        date="", india_net_cr=float(cv.group(1)),
                    ))
                    week1_net += float(cv.group(1))
                    break

    daily_rows.sort(key=lambda r: r.day)
    data_complete = len(seen_days) >= 7

    if len(seen_days) < 2:
        log.debug(f"Insufficient days for: {title} ({len(seen_days)} days)")
        return None, []

    lifetime_cr = max(india_net_cr, week1_net)

    meta = MovieMeta(
        movie_id=slug, title=title, language=language,
        genre=genre, release_date=release_date,
        screens_india=screens, budget_cr=budget_cr,
        cast_tier=tier, director=director,
        lifetime_cr=lifetime_cr, week1_net_cr=round(week1_net, 2),
        india_gross_cr=india_gross_cr, worldwide_cr=worldwide_cr,
        verdict=verdict, data_complete=data_complete,
    )
    return meta, daily_rows

# ─── Main ─────────────────────────────────────────────────────────────────────

def run_scraper(target_movies: int = 500, years: list[int] | None = None):
    if years is None:
        years = [2022, 2023, 2024, 2025]

    log.info(f"Sacnilk scraper v2 — target: {target_movies}, years: {years}")

    # Build stub list
    stubs: list[dict] = []
    seen:  set[str]   = set()

    def add_stub(s: dict):
        if s["slug"] not in seen:
            seen.add(s["slug"])
            stubs.append(s)

    # Seed from known slugs
    for slug in KNOWN_SLUGS:
        ym = re.search(r"_(\d{4})_", slug)
        year = int(ym.group(1)) if ym else 0
        if year not in years:
            continue
        title = slug.split(f"_{year}_")[0].replace("_", " ")
        add_stub({"slug": slug, "url": f"{BASE_URL}/news/{slug}", "title": title, "year": year})

    log.info(f"Seed stubs: {len(stubs)}")

    # Discover more from listing pages
    log.info("Discovering more from listing pages …")
    for s in discover_stubs_from_listings():
        if s.get("year", 0) in years:
            add_stub(s)

    log.info(f"Total candidate stubs: {len(stubs)}")

    # Scrape each
    all_metas:   list[dict] = []
    all_dailies: list[dict] = []
    skipped = 0

    for stub in tqdm(stubs, desc="Scraping movies"):
        meta, dailies = parse_movie_page(stub)
        if meta is None:
            skipped += 1
            log.debug(f"Skipped: {stub['title']}")
        else:
            all_metas.append(asdict(meta))
            all_dailies.extend([asdict(d) for d in dailies])
            log.debug(f"OK: {meta.title} ({len(dailies)} days, ₹{meta.week1_net_cr} Cr week1)")

        polite_sleep()

        if len(all_metas) >= target_movies:
            log.info(f"Reached target {target_movies} — stopping.")
            break

    # Save
    pd.DataFrame(all_metas).to_csv(OUTPUT_DIR / "raw_movies.csv",         index=False)
    pd.DataFrame(all_dailies).to_csv(OUTPUT_DIR / "daily_collections.csv", index=False)

    log.info(
        f"\n{'='*50}\n"
        f"Done! Movies: {len(all_metas)} | Dailies: {len(all_dailies)} | Skipped: {skipped}\n"
        f"Output: {OUTPUT_DIR.resolve()}\n"
        f"{'='*50}"
    )

def validate_dataset():
    meta  = pd.read_csv(OUTPUT_DIR / "raw_movies.csv")
    daily = pd.read_csv(OUTPUT_DIR / "daily_collections.csv")
    print(f"\n{'='*45}")
    print("DATASET VALIDATION")
    print(f"{'='*45}")
    print(f"Total movies        : {len(meta)}")
    print(f"Complete (≥7 days)  : {meta['data_complete'].sum()}")
    print(f"Has lifetime data   : {(meta['lifetime_cr'] > 0).sum()}")
    print(f"\nLanguage breakdown  :")
    print(meta["language"].value_counts().to_string())
    print(f"\nLifetime (Cr) stats:")
    valid = meta["lifetime_cr"][meta["lifetime_cr"] > 0]
    if len(valid):
        print(valid.describe().round(2).to_string())
    print(f"\nTotal daily rows    : {len(daily)}")
    print(f"{'='*45}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target",        type=int,   default=500)
    parser.add_argument("--years",         nargs="+",  type=int, default=[2022,2023,2024,2025])
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        validate_dataset()
    else:
        run_scraper(target_movies=args.target, years=args.years)
        validate_dataset()