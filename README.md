# RBS Route Scraper

A Selenium-based scraper for [Indian Railways RBS (Rates Branch System)](https://rbs.indianrail.gov.in) that extracts freight route data for Origin-Destination station pairs.

## What it does

Given a pair of station codes, it queries RBS and returns the full list of intermediate stations, cumulative distances, and total route distance — for both the **Shortest Path** and the **Rational Route** (the officially prescribed freight route by commodity).

## How it works

1. Opens Chrome and navigates to the RBS form
2. Fills in the origin/destination station codes, gauge (Broad), and commodity
3. Waits for the results table to appear (explicit wait, no fixed sleep)
4. Parses the station table — RBS renders rows in two formats (code on its own line followed by name+data, or all on one line) and sometimes appends the reverse route; the parser handles both
5. Saves results to JSON, resuming from where it left off if interrupted

## Installation

```bash
pip install -r requirements.txt
```

Chrome must be installed. ChromeDriver is managed automatically by `webdriver-manager`.

## Usage

```bash
# Shortest path — all OD pairs in od_pairs.json
python scrape.py shortest

# Rational route — all OD pairs
python scrape.py rational

# Test a single pair (any valid RBS station codes)
python scrape.py shortest OCIG PBJT
python scrape.py rational TSLJ ATLP

# Headless Chrome
python scrape.py shortest --headless

# Parallel workers — runs N Chrome instances simultaneously (~Nx faster)
python scrape.py shortest --headless --workers 3
python scrape.py rational --headless --workers 4

# Debug mode (dumps form fields + page source)
python scrape.py rational OCIG PBJT --debug
```

> Keep `--workers` at 3–4 max to avoid rate-limiting by the RBS server.

## Input

Create an `od_pairs.json` file:

```json
{
  "od_pairs": [
    { "origin": "OCIG", "destination": "PBJT", "commodity": "Clinker" },
    { "origin": "TSLJ", "destination": "ATLP", "commodity": "Steel Products" }
  ]
}
```

Supported commodity values for rational routes: `Clinker`, `Steel Products`, `Coal`, `Cement`, `Iron Ore`, `Fertilizer`, `Food Grains`, `Container`. Anything else defaults to `ALL`.

## Output

Results are saved to `routes_output.json` (shortest) or `rational_routes_output.json` (rational). Each entry:

```json
{
  "origin": "OCIG",
  "destination": "PBJT",
  "distance_km": 322.28,
  "total_stations": 26,
  "route": [
    { "station_code": "OCIG", "station_name": "PVT. SDG OF M/S DALMIA CEMENT (BHARAT) LTD.", "cumulative_dist_km": "0.0" },
    { "station_code": "GP",   "station_name": "Rajgangpur", "cumulative_dist_km": "2.34" }
  ]
}
```

The scraper resumes automatically — already-completed pairs are skipped on re-run.

## Station codes

Station codes are the standard Indian Railways station codes (2–6 uppercase characters), the same ones used in FOIS, NTES, and RBS.

## Limitations

- **Chrome required** — headless mode needs Chrome installed on the host machine
- **Rate limiting** — the RBS server is a public government portal; keep workers at 3–4 max
- **Site availability** — RBS may be down during Indian Railways maintenance windows (typically late night IST)
- **Parsing** — the parser handles the two known RBS row formats; if the site changes its layout, parsing may need updating

## License

MIT
