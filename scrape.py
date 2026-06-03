"""
RBS Rake Route Scraper
Target: rbs.indianrail.gov.in

Usage:
  python scrape.py shortest                          # all 43 OD pairs, shortest path
  python scrape.py rational                          # all 43 OD pairs, rational routes
  python scrape.py shortest OCIG PBJT               # single test pair
  python scrape.py rational OCIG PBJT               # single test pair
  python scrape.py shortest --headless              # headless Chrome
  python scrape.py shortest --headless --workers 4  # 4 parallel Chrome instances
  python scrape.py rational OCIG PBJT --debug       # form dump + page source
"""

import json
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    UnexpectedAlertPresentException,
)

# ── Config ─────────────────────────────────────────────────────────────────────

OD_PAIRS_FILE = Path(__file__).parent / "od_pairs.json"

URLS = {
    "shortest": "https://rbs.indianrail.gov.in/ShortPath/ShortPath.jsp",
    "rational": "https://rbs.indianrail.gov.in/ShortPath/CommodityRationalRt.jsp",
}
OUTPUT_FILES = {
    "shortest": Path(__file__).parent / "routes_output.json",
    "rational": Path(__file__).parent / "rational_routes_output.json",
}

COMMODITY_MAP = {
    "Clinker":        "CLINKER",
    "Steel Products": "IRON OR STEEL",
    "Coal":           "COAL",
    "Cement":         "CEMENT",
    "Iron Ore":       "IRON ORE",
    "Fertilizer":     "FERTILIZER",
    "Food Grains":    "FOOD GRAINS",
    "Container":      "CONTAINER",
    "Other Dalmia":   "ALL",
    "Goods":          "ALL",
    "Unknown":        "ALL",
}

RESULTS_HEADER  = "STATION CODE STATION NAME"
PAGE_TIMEOUT    = 15
RESULTS_TIMEOUT = 20

# ── Parsing ────────────────────────────────────────────────────────────────────

_CODE_RE      = re.compile(r'^([A-Z][A-Z0-9]{1,5})$')
_INLINE_RE    = re.compile(
    r'^([A-Z][A-Z0-9]{1,5})\s+'
    r'(.+?)\s+'
    r'(\d+\.?\d*)\s+'
    r'\d+\.?\d*'
)
_NAME_DATA_RE = re.compile(r'^(.+?)\s+(\d+\.?\d*)\s+\d+\.?\d*')


def parse_route(raw_text):
    """
    Parse the station table from RBS body text.
    Handles two row formats:
      A) station code alone on one line, name+distances on the next
      B) code + name + distances all on one line
    Stops when cumulative distance resets to 0 after non-zero (reverse route appended by RBS).
    """
    header_idx = raw_text.find(RESULTS_HEADER)
    if header_idx == -1:
        return []

    lines        = [l.strip() for l in raw_text[header_idx + len(RESULTS_HEADER):].split('\n')]
    stops        = []
    seen_nonzero = False
    i            = 0

    while i < len(lines):
        line = lines[i]
        if not line or line.startswith("Report"):
            i += 1
            continue

        # Format A: bare station code
        if _CODE_RE.match(line) and i + 1 < len(lines):
            m = _NAME_DATA_RE.match(lines[i + 1].strip())
            if m:
                km = float(m.group(2))
                if seen_nonzero and km == 0.0:
                    break
                if km > 0:
                    seen_nonzero = True
                stops.append({"station_code": line,
                               "station_name": m.group(1).strip(),
                               "cumulative_dist_km": m.group(2)})
                i += 2
                continue

        # Format B: code + name + distances on one line
        m = _INLINE_RE.match(line)
        if m:
            km = float(m.group(3))
            if seen_nonzero and km == 0.0:
                break
            if km > 0:
                seen_nonzero = True
            stops.append({"station_code": m.group(1),
                           "station_name": m.group(2).strip(),
                           "cumulative_dist_km": m.group(3)})
        i += 1

    return stops


def parse_distance_only(raw_text):
    m = re.search(r'(\d+\.?\d*)\s*[Kk][Mm]', raw_text)
    return float(m.group(1)) if m else None


# ── Driver ─────────────────────────────────────────────────────────────────────

def make_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    return webdriver.Chrome(options=opts)


# ── Form helpers ───────────────────────────────────────────────────────────────

def load_page(driver, url):
    driver.get(url)
    try:
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='text']"))
        )
    except TimeoutException:
        pass


def wait_for_results(driver):
    try:
        WebDriverWait(driver, RESULTS_TIMEOUT).until(
            lambda d: RESULTS_HEADER in d.find_element(By.TAG_NAME, "body").text
        )
        return True
    except TimeoutException:
        return False


def find_input(driver, name_hints=(), nth=None):
    for h in name_hints:
        try:
            return driver.find_element(By.NAME, h)
        except NoSuchElementException:
            pass
    if nth is not None:
        try:
            return driver.find_elements(By.XPATH, "//input[@type='text']")[nth - 1]
        except IndexError:
            pass
    return None


def fill_station(driver, field, code):
    field.clear()
    field.send_keys(code)
    driver.execute_script("arguments[0].value = arguments[1];", field, code)


def try_radio(driver, values):
    for val in values:
        try:
            r = driver.find_element(By.XPATH,
                f"//input[@type='radio' and (@value='{val}' or @id='{val}')]")
            if not r.is_selected():
                driver.execute_script("arguments[0].click();", r)
            return True
        except NoSuchElementException:
            continue
    return False


def try_select(driver, name, text, fallback="ALL"):
    try:
        sel = Select(driver.find_element(By.NAME, name))
    except NoSuchElementException:
        return False
    for t in [text, fallback]:
        try:
            sel.select_by_visible_text(t)
            return True
        except Exception:
            pass
    return False


def find_submit(driver):
    for sel in [
        (By.XPATH, "//input[@type='submit']"),
        (By.XPATH, "//button[@type='submit']"),
        (By.XPATH, "//button[contains(text(),'FIND') or contains(text(),'Find')]"),
    ]:
        try:
            return driver.find_element(*sel)
        except NoSuchElementException:
            continue
    return None


def dump_form(driver):
    print("\n--- FORM FIELDS ---")
    for tag in ["input", "select", "button"]:
        for el in driver.find_elements(By.TAG_NAME, tag):
            print(f"  <{tag}> name={el.get_attribute('name')!r} "
                  f"type={el.get_attribute('type')!r} "
                  f"value={el.get_attribute('value')!r} "
                  f"text={el.text[:50]!r}")
    print("--- END ---\n")


# ── Scrape functions ───────────────────────────────────────────────────────────

def scrape_shortest(driver, origin, destination, debug=False):
    load_page(driver, URLS["shortest"])
    if debug:
        dump_form(driver)

    src = find_input(driver, name_hints=["source", "src", "srcCode"], nth=1)
    dst = find_input(driver, name_hints=["destination", "dest", "destCode"], nth=2)
    if src is None or dst is None:
        return None, "Cannot find station input fields"

    fill_station(driver, src, origin)
    fill_station(driver, dst, destination)
    try_select(driver, "gaugeType", "Broad") or try_radio(driver, ["Broad", "BG", "B"])
    try_radio(driver, ["Goods", "G", "goods"])

    submit = find_submit(driver)
    if submit is None:
        return None, "Cannot find submit button"

    try:
        submit.click()
    except UnexpectedAlertPresentException:
        pass

    # Dismiss alert and retry once
    try:
        driver.switch_to.alert.accept()
        inputs = driver.find_elements(By.XPATH, "//input[@type='text']")
        if len(inputs) >= 2:
            driver.execute_script("arguments[0].value=arguments[1]", inputs[0], origin)
            driver.execute_script("arguments[0].value=arguments[1]", inputs[1], destination)
        find_submit(driver).click()
    except Exception:
        pass

    wait_for_results(driver)

    if debug:
        print(driver.page_source[:3000])

    return driver.find_element(By.TAG_NAME, "body").text, None


def scrape_rational(driver, origin, destination, commodity="ALL", debug=False):
    load_page(driver, URLS["rational"])
    if debug:
        dump_form(driver)

    src = find_input(driver, name_hints=["srcCode", "source", "src"], nth=1)
    dst = find_input(driver, name_hints=["destCode", "destination", "dest"], nth=2)
    if src is None or dst is None:
        return None, "Cannot find station input fields"

    fill_station(driver, src, origin)
    fill_station(driver, dst, destination)
    try_select(driver, "gaugeType", "Broad")
    try_select(driver, "commodity", commodity)

    submit = find_submit(driver)
    if submit is None:
        return None, "Cannot find submit button"

    try:
        submit.click()
    except UnexpectedAlertPresentException:
        pass

    # Dismiss alert and retry once
    try:
        driver.switch_to.alert.accept()
        inputs = driver.find_elements(By.XPATH, "//input[@type='text']")
        if len(inputs) >= 2:
            driver.execute_script("arguments[0].value=arguments[1]", inputs[0], origin)
            driver.execute_script("arguments[0].value=arguments[1]", inputs[1], destination)
        try_select(driver, "commodity", commodity)
        find_submit(driver).click()
    except Exception:
        pass

    wait_for_results(driver)

    if debug:
        print(driver.page_source[:3000])

    return driver.find_element(By.TAG_NAME, "body").text, None


# ── Result builder ─────────────────────────────────────────────────────────────

def build_result(mode, pair, page_text, err):
    origin      = pair["origin"]
    destination = pair["destination"]
    commodity   = pair.get("commodity", "Unknown")

    result = {
        "origin":      origin,
        "destination": destination,
        "source":      "RBS-ShortPath" if mode == "shortest" else "RBS-RationalRoute",
        "company":     pair.get("company", ""),
        "commodity":   commodity,
        "rakes":       pair.get("rakes", []),
        "route":       [],
        "distance_km": None,
        "error":       err,
        "raw_text":    None,
    }
    if mode == "rational":
        result["commodity_queried"] = COMMODITY_MAP.get(commodity, "ALL")

    if err or page_text is None:
        return result

    result["raw_text"] = page_text[:2000]
    stops = parse_route(page_text)

    if stops:
        result["route"]                  = stops
        result["total_stations"]         = len(stops)
        result["intermediate_stations"]  = len(stops) - 2
        try:
            result["distance_km"] = float(stops[-1]["cumulative_dist_km"])
        except (ValueError, IndexError):
            pass
    else:
        dist = parse_distance_only(page_text)
        if dist is not None:
            result["distance_km"] = dist
            result["error"]       = "Distance only — no station table returned"
        else:
            result["error"] = "No route rows parsed — check raw_text"

    return result


# ── I/O helpers ────────────────────────────────────────────────────────────────

def load_pairs():
    with open(OD_PAIRS_FILE) as f:
        return json.load(f)["od_pairs"]


def load_existing(output_file):
    if not output_file.exists():
        return [], set()
    with open(output_file) as f:
        data = json.load(f)
    done = {(r["origin"], r["destination"])
            for r in data
            if r.get("route") or r.get("distance_km") is not None}
    return data, done


def save(results, output_file):
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)


# ── Main ───────────────────────────────────────────────────────────────────────

def _run_one(pair, mode, headless, debug):
    """Scrape a single pair in its own Chrome instance. Called from worker threads."""
    o, d      = pair["origin"], pair["destination"]
    commodity = COMMODITY_MAP.get(pair.get("commodity", "Unknown"), "ALL")
    driver    = make_driver(headless)
    try:
        if mode == "shortest":
            page_text, err = scrape_shortest(driver, o, d, debug=debug)
        else:
            page_text, err = scrape_rational(driver, o, d, commodity=commodity, debug=debug)
    finally:
        driver.quit()
    return build_result(mode, pair, page_text, err)


def main():
    argv     = sys.argv[1:]
    headless = "--headless" in argv
    debug    = "--debug"    in argv
    plain    = [a for a in argv if not a.startswith("--")]

    # --workers N  (default 1)
    workers = 1
    for i, a in enumerate(argv):
        if a == "--workers" and i + 1 < len(argv):
            try:
                workers = max(1, int(argv[i + 1]))
            except ValueError:
                pass

    if not plain or plain[0] not in ("shortest", "rational"):
        print("Usage: python scrape.py <shortest|rational> [ORIGIN DEST] [--headless] [--workers N] [--debug]")
        sys.exit(1)

    mode   = plain[0]
    single = None
    if len(plain) == 3:
        single = {"origin": plain[1], "destination": plain[2],
                  "company": "test", "commodity": "Unknown"}

    pairs       = [single] if single else load_pairs()
    output_file = OUTPUT_FILES[mode]
    existing, done = ([], set()) if single else load_existing(output_file)

    todo = [p for p in pairs if (p["origin"], p["destination"]) not in done]

    print(f"RBS {'Shortest Path' if mode == 'shortest' else 'Rational Routes'} Scraper")
    print(f"URL: {URLS[mode]}")
    print(f"Pairs: {len(pairs)} | Done: {len(done)} | Remaining: {len(todo)} | Workers: {workers}")
    print(f"Mode: {'headless' if headless else 'visible'} | debug: {debug}\n")

    results   = list(existing)
    save_lock = threading.Lock()
    completed = [0]

    def on_done(pair, r):
        o, d = pair["origin"], pair["destination"]
        commodity = COMMODITY_MAP.get(pair.get("commodity", "Unknown"), "ALL")
        completed[0] += 1
        label = f"[{completed[0]}/{len(todo)}] {pair.get('company', '')}: {o}->{d}"
        if mode == "rational":
            label += f" [{commodity}]"
        if r["route"]:
            status = f"OK {len(r['route'])} stations | {r['distance_km']} km"
        elif r.get("distance_km") is not None:
            status = f"~ {r['distance_km']} km (distance only)"
        else:
            status = f"FAIL {r['error']}"
        print(f"{label} ... {status}", flush=True)
        with save_lock:
            results.append(r)
            save(results, output_file)

    if workers == 1:
        # Single-driver path — reuse one Chrome instance (faster per-pair overhead)
        driver = make_driver(headless)
        try:
            for pair in todo:
                o, d      = pair["origin"], pair["destination"]
                commodity = COMMODITY_MAP.get(pair.get("commodity", "Unknown"), "ALL")
                if mode == "shortest":
                    page_text, err = scrape_shortest(driver, o, d, debug=debug)
                else:
                    page_text, err = scrape_rational(driver, o, d, commodity=commodity, debug=debug)
                r = build_result(mode, pair, page_text, err)
                on_done(pair, r)
                time.sleep(0.8)
        finally:
            driver.quit()
    else:
        # Multi-driver path — one Chrome per worker thread
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_run_one, pair, mode, headless, debug): pair
                       for pair in todo}
            for future in as_completed(futures):
                pair = futures[future]
                try:
                    r = future.result()
                except Exception as e:
                    r = build_result(mode, pair, None, f"Worker exception: {e}")
                on_done(pair, r)

    success   = sum(1 for r in results if r.get("route"))
    dist_only = sum(1 for r in results if not r.get("route") and r.get("distance_km") is not None)
    failed    = sum(1 for r in results if not r.get("route") and r.get("distance_km") is None)
    print(f"\nDone. {success} full routes | {dist_only} distance-only | {failed} failed")
    print(f"Output: {output_file}")


if __name__ == "__main__":
    main()
