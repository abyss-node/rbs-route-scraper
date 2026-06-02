# RBS Route Scraper

A Selenium-based scraper for [Indian Railways RBS (Rates Branch System)](https://rbs.indianrail.gov.in) that extracts freight route data for Origin-Destination station pairs.

## What it does

Given a pair of station codes, it queries RBS and returns the full list of intermediate stations, cumulative distances, and total route distance — for both the **Shortest Path** and the **Rational Route** (the officially prescribed freight route by commodity).

## Installation

```bash
pip install selenium webdriver-manager
```

Chrome must be installed. ChromeDriver is managed automatically.

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

# Debug mode (dumps form fields + page source)
python scrape.py rational OCIG PBJT --debug
```

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
    { "station_code": "GP",   "station_name": "Rajgangpur", "cumulative_dist_km": "2.34" },
    ...
  ]
}
```

The scraper resumes automatically — already-completed pairs are skipped on re-run.

## Station codes

Station codes are the standard Indian Railways station codes (2–6 uppercase characters), the same ones used in FOIS, NTES, and RBS.
