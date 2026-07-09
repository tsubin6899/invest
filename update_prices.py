from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import csv
import io
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SYMBOLS_FILE = ROOT / "price-symbols.json"
DASHBOARD_EXPORT = ROOT / "personal-assets-demo.json"
OUTPUT_FILE = ROOT / "latest-prices.json"
INVEST_OUTPUT_FILE = ROOT / "invest" / "latest-prices.json"
RATES_OUTPUT_FILE = ROOT / "latest-rates.json"
INVEST_RATES_OUTPUT_FILE = ROOT / "invest" / "latest-rates.json"
RATE_CURRENCIES = ("USD", "JPY", "EUR", "CNY", "HKD", "THB", "KRW", "GBP", "AUD", "CAD", "SGD")


def load_symbols() -> dict[str, list[dict[str, str]]]:
    if SYMBOLS_FILE.exists():
        return json.loads(SYMBOLS_FILE.read_text(encoding="utf-8-sig"))
    if DASHBOARD_EXPORT.exists():
        data = json.loads(DASHBOARD_EXPORT.read_text(encoding="utf-8-sig"))
        return {
            "tw": [{"code": row.get("code", ""), "name": row.get("name", "")} for row in data.get("tw", [])],
            "us": [{"code": row.get("code", ""), "name": row.get("name", "")} for row in data.get("us", [])],
        }
    raise FileNotFoundError(
        "Missing price-symbols.json. Export dashboard data as JSON first, "
        "or keep personal-assets-demo.json in this folder."
    )


def yahoo_chart(symbol: str) -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=1d&interval=1d"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = payload.get("chart", {}).get("result") or []
    if not result:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(error or f"No Yahoo chart result for {symbol}")
    return result[0]


def parse_market_number(value: object) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def twse_stock_info(symbol: str, exchange: str) -> dict:
    channel = f"{exchange}_{symbol}.tw"
    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?" + urllib.parse.urlencode(
        {"ex_ch": channel, "json": "1", "delay": "0"}
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://mis.twse.com.tw/stock/fibest.jsp?lang=zh_tw",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    rows = payload.get("msgArray") or []
    if not rows:
        raise RuntimeError(f"No TWSE quote result for {channel}")
    row = rows[0]
    price = parse_market_number(row.get("z")) or parse_market_number(row.get("pz")) or parse_market_number(row.get("y"))
    previous = parse_market_number(row.get("y"))
    if price is None:
        raise RuntimeError(f"No usable TWSE price for {channel}")
    change = None if previous is None else price - previous
    change_percent = None if previous in (None, 0) else change / previous * 100
    return {
        "symbol": symbol,
        "twseChannel": channel,
        "market": "TW",
        "price": round(price, 4),
        "currency": "TWD",
        "previousClose": None if previous is None else round(previous, 4),
        "change": None if change is None else round(change, 4),
        "changePercent": None if change_percent is None else round(change_percent, 4),
        "source": "TWSE MIS official API",
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def tw_quote(symbol: str) -> dict:
    last_error: Exception | None = None
    for exchange in ("tse", "otc"):
        try:
            return twse_stock_info(symbol, exchange)
        except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(str(last_error))


def quote(symbol: str, market: str) -> dict:
    if market == "TW":
        return tw_quote(symbol)

    candidates = [symbol]

    last_error: Exception | None = None
    for yahoo_symbol in candidates:
        try:
            result = yahoo_chart(yahoo_symbol)
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice")
            previous = meta.get("chartPreviousClose") or meta.get("previousClose")
            currency = meta.get("currency") or ("TWD" if market == "TW" else "USD")
            if price is None:
                closes = result.get("indicators", {}).get("quote", [{}])[0].get("close") or []
                price = next((value for value in reversed(closes) if value is not None), None)
            if price is None:
                raise RuntimeError(f"No price in Yahoo response for {yahoo_symbol}")
            change = None if previous is None else float(price) - float(previous)
            change_percent = None if previous in (None, 0) else change / float(previous) * 100
            return {
                "symbol": symbol,
                "yahooSymbol": yahoo_symbol,
                "market": market,
                "price": round(float(price), 4),
                "currency": currency,
                "previousClose": None if previous is None else round(float(previous), 4),
                "change": None if change is None else round(float(change), 4),
                "changePercent": None if change_percent is None else round(float(change_percent), 4),
                "source": "Yahoo Finance chart API",
                "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(str(last_error))


def normalize_code(value: object) -> str:
    return str(value or "").strip().upper()


def parse_rate_number(value: object) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def decode_response_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp950", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def bot_exchange_rates() -> dict[str, float]:
    url = "https://rate.bot.com.tw/xrt/flcsv/0/day"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/csv,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        text = decode_response_bytes(response.read())
    if "幣別" not in text and "Currency" not in text:
        raise RuntimeError("Bank of Taiwan CSV is not available")

    rates = {"TWD": 1.0}
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        currency = normalize_code(str(row[0]).split()[0])
        if currency not in RATE_CURRENCIES:
            continue
        spot_buy = parse_rate_number(row[3] if len(row) > 3 else None)
        spot_sell = parse_rate_number(row[4] if len(row) > 4 else None)
        cash_buy = parse_rate_number(row[1] if len(row) > 1 else None)
        cash_sell = parse_rate_number(row[2] if len(row) > 2 else None)
        buy = spot_buy or cash_buy
        sell = spot_sell or cash_sell
        if buy and sell:
            rates[currency] = round((buy + sell) / 2, 6)
        elif sell:
            rates[currency] = round(sell, 6)
        elif buy:
            rates[currency] = round(buy, 6)
    if len(rates) <= 1:
        raise RuntimeError("No usable Bank of Taiwan rates")
    return rates


def open_exchange_rates() -> dict[str, float]:
    url = "https://open.er-api.com/v6/latest/TWD"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("result") != "success":
        raise RuntimeError(payload.get("error-type") or "Exchange rate API failed")
    source_rates = payload.get("rates") or {}
    rates = {"TWD": 1.0}
    for currency in RATE_CURRENCIES:
        value = parse_rate_number(source_rates.get(currency))
        if value:
            rates[currency] = round(1 / value, 6)
    if len(rates) <= 1:
        raise RuntimeError("No usable fallback exchange rates")
    return rates


def update_exchange_rates() -> dict:
    errors: list[dict[str, str]] = []
    for source, fetcher in (
        ("Bank of Taiwan daily CSV", bot_exchange_rates),
        ("open.er-api.com TWD base", open_exchange_rates),
    ):
        try:
            rates = fetcher()
            output = {
                "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": source,
                "rates": rates,
                "errors": errors,
            }
            rendered = json.dumps(output, ensure_ascii=False, indent=2)
            RATES_OUTPUT_FILE.write_text(rendered, encoding="utf-8")
            if INVEST_RATES_OUTPUT_FILE.parent.exists():
                INVEST_RATES_OUTPUT_FILE.write_text(rendered, encoding="utf-8")
                print(f"Wrote {INVEST_RATES_OUTPUT_FILE}")
            print(f"Wrote {RATES_OUTPUT_FILE} ({len(rates)} currencies, source: {source})")
            return output
        except Exception as exc:  # noqa: BLE001 - try fallback source before giving up.
            errors.append({"source": source, "error": str(exc)})
            print(f"FAIL exchange rates from {source} - {exc}", file=sys.stderr)

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": None,
        "rates": {},
        "errors": errors,
    }
    if RATES_OUTPUT_FILE.exists() or INVEST_RATES_OUTPUT_FILE.exists():
        print("No exchange rates fetched; kept existing latest-rates.json", file=sys.stderr)
    return output


def main() -> int:
    symbols = load_symbols()
    prices: dict[str, dict] = {}
    errors: list[dict[str, str]] = []

    for market_key, market in (("tw", "TW"), ("us", "US")):
        seen: set[str] = set()
        for row in symbols.get(market_key, []):
            code = normalize_code(row.get("code"))
            if not code or code in seen:
                continue
            seen.add(code)
            try:
                item = quote(code, market)
                prices[f"{market}:{code}"] = item
                print(f"OK {market}:{code} -> {item['price']} {item['currency']}")
            except Exception as exc:  # noqa: BLE001 - report and continue other symbols.
                errors.append({"market": market, "symbol": code, "error": str(exc)})
                print(f"FAIL {market}:{code} - {exc}", file=sys.stderr)
            time.sleep(0.25)

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prices": prices,
        "errors": errors,
    }
    if not prices and OUTPUT_FILE.exists():
        print(f"No quotes fetched; kept existing {OUTPUT_FILE}", file=sys.stderr)
        if INVEST_OUTPUT_FILE.exists():
            print(f"No quotes fetched; kept existing {INVEST_OUTPUT_FILE}", file=sys.stderr)
        update_exchange_rates()
        return 1
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    OUTPUT_FILE.write_text(rendered, encoding="utf-8")
    if INVEST_OUTPUT_FILE.parent.exists():
        INVEST_OUTPUT_FILE.write_text(rendered, encoding="utf-8")
        print(f"Wrote {INVEST_OUTPUT_FILE}")
    print(f"Wrote {OUTPUT_FILE} ({len(prices)} quotes, {len(errors)} errors)")
    update_exchange_rates()
    return 0 if prices else 1


if __name__ == "__main__":
    raise SystemExit(main())
