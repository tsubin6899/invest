from __future__ import annotations

import csv
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SYMBOLS_FILE = ROOT / "price-symbols.json"
DASHBOARD_EXPORT = ROOT / "personal-assets-demo.json"
OUTPUT_FILE = ROOT / "latest-prices.json"
INVEST_OUTPUT_FILE = ROOT / "invest" / "latest-prices.json"
RATES_OUTPUT_FILE = ROOT / "latest-rates.json"
INVEST_RATES_OUTPUT_FILE = ROOT / "invest" / "latest-rates.json"
VALUATIONS_OUTPUT_FILE = ROOT / "latest-valuations.json"
RATE_CURRENCIES = ("USD", "JPY", "EUR", "CNY", "HKD", "THB", "KRW", "GBP", "AUD", "CAD", "SGD")
SEC_USER_AGENT = "PersonalAssetDashboard/2.0 contact=dashboard-maintainer@example.com"
WANTGOO_ETF_PAGE_URL = "https://www.wantgoo.com/stock/etf/net-value"
WANTGOO_ETF_DATA_URL = "https://www.wantgoo.com/stock/etf/daily-value-data"
SITCA_DAILY_NAV_URL = "https://www.sitca.org.tw/MemberK0000/F/03/nav.csv"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def fetch_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> object:
    request_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json,text/plain,*/*",
    }
    request_headers.update(headers or {})
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def yahoo_chart(symbol: str) -> dict:
    encoded = urllib.parse.quote(symbol, safe="")
    payload = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=1d&interval=1d")
    result = payload.get("chart", {}).get("result") or []
    if not result:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(error or f"No Yahoo chart result for {symbol}")
    return result[0]


def parse_market_number(value: object) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text or text.upper() in {"-", "--", "N/A", "NA", "NULL", "NONE"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def first_number(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = parse_market_number(row.get(key))
        if value is not None:
            return value
    return None


def first_text(row: dict, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_data_date(value: object) -> str:
    text = "".join(character for character in str(value or "") if character.isdigit())
    if len(text) == 7:
        return f"{int(text[:3]) + 1911:04d}-{text[3:5]}-{text[5:7]}"
    if len(text) >= 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return str(value or "").strip()


def date_data_status(value: object) -> str:
    normalized = normalize_data_date(value)
    try:
        age_days = (datetime.now(timezone.utc).date() - datetime.fromisoformat(normalized).date()).days
    except ValueError:
        return "partial"
    return "current" if age_days <= 5 else "stale"


def twse_stock_info(symbol: str, exchange: str) -> dict:
    channel = f"{exchange}_{symbol}.tw"
    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?" + urllib.parse.urlencode(
        {"ex_ch": channel, "json": "1", "delay": "0"}
    )
    payload = fetch_json(
        url,
        headers={"Referer": "https://mis.twse.com.tw/stock/fibest.jsp?lang=zh_tw"},
        timeout=15,
    )
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
        "instrumentType": "ETF" if is_tw_etf(symbol, "") else "EQUITY",
        "price": round(price, 4),
        "currency": "TWD",
        "previousClose": None if previous is None else round(previous, 4),
        "change": None if change is None else round(change, 4),
        "changePercent": None if change_percent is None else round(change_percent, 4),
        "source": "TWSE MIS official API",
        "fetchedAt": now_iso(),
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

    result = yahoo_chart(symbol)
    meta = result.get("meta", {})
    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    currency = meta.get("currency") or "USD"
    if price is None:
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close") or []
        price = next((value for value in reversed(closes) if value is not None), None)
    if price is None:
        raise RuntimeError(f"No price in Yahoo response for {symbol}")
    change = None if previous is None else float(price) - float(previous)
    change_percent = None if previous in (None, 0) else change / float(previous) * 100
    return {
        "symbol": symbol,
        "yahooSymbol": symbol,
        "market": market,
        "instrumentType": str(meta.get("instrumentType") or "").upper(),
        "price": round(float(price), 4),
        "currency": currency,
        "previousClose": None if previous is None else round(float(previous), 4),
        "change": None if change is None else round(float(change), 4),
        "changePercent": None if change_percent is None else round(float(change_percent), 4),
        "source": "Yahoo Finance chart API",
        "fetchedAt": now_iso(),
    }


def normalize_code(value: object) -> str:
    return str(value or "").strip().upper()


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
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        currency = normalize_code(str(row[0]).split()[0])
        if currency not in RATE_CURRENCIES:
            continue
        spot_buy = parse_market_number(row[3] if len(row) > 3 else None)
        spot_sell = parse_market_number(row[4] if len(row) > 4 else None)
        cash_buy = parse_market_number(row[1] if len(row) > 1 else None)
        cash_sell = parse_market_number(row[2] if len(row) > 2 else None)
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
    payload = fetch_json("https://open.er-api.com/v6/latest/TWD", timeout=15)
    if payload.get("result") != "success":
        raise RuntimeError(payload.get("error-type") or "Exchange rate API failed")
    source_rates = payload.get("rates") or {}
    rates = {"TWD": 1.0}
    for currency in RATE_CURRENCIES:
        value = parse_market_number(source_rates.get(currency))
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
            output = {"generatedAt": now_iso(), "source": source, "rates": rates, "errors": errors}
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

    output = {"generatedAt": now_iso(), "source": None, "rates": {}, "errors": errors}
    print("No exchange rates fetched; kept existing latest-rates.json", file=sys.stderr)
    return output


def is_tw_etf(code: str, name: str) -> bool:
    normalized_name = str(name or "").upper()
    return normalize_code(code).startswith("00") or "ETF" in normalized_name


def twse_valuations() -> tuple[dict[str, dict], str]:
    rows = fetch_json("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL")
    values: dict[str, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        code = normalize_code(first_text(row, "Code", "證券代號"))
        if not code:
            continue
        pe = first_number(row, "PEratio", "本益比")
        pb = first_number(row, "PBratio", "股價淨值比")
        dividend_yield = first_number(row, "DividendYield", "殖利率(%)", "殖利率")
        values[code] = {
            "assetType": "stock",
            "metric": "pe" if pe and pe > 0 else None,
            "value": round(pe, 2) if pe and pe > 0 else None,
            "pe": round(pe, 2) if pe and pe > 0 else None,
            "pb": round(pb, 2) if pb is not None else None,
            "dividendYield": round(dividend_yield, 2) if dividend_yield is not None else None,
            "source": "TWSE OpenAPI BWIBBU_ALL",
            "asOf": normalize_data_date(first_text(row, "Date", "資料日期")) or datetime.now(timezone.utc).date().isoformat(),
            "dataStatus": "current" if pe and pe > 0 else "partial",
        }
    return values, "TWSE OpenAPI BWIBBU_ALL"


def tpex_valuations() -> tuple[dict[str, dict], str]:
    rows = fetch_json("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis")
    values: dict[str, dict] = {}
    for row in rows if isinstance(rows, list) else []:
        code = normalize_code(first_text(
            row,
            "SecuritiesCompanyCode",
            "SecurityCode",
            "SecuritiesCode",
            "Code",
            "股票代號",
        ))
        if not code:
            continue
        pe = first_number(row, "PriceEarningRatio", "PERatio", "PEratio", "本益比")
        pb = first_number(row, "PriceBookRatio", "PBRatio", "PBratio", "股價淨值比")
        dividend_yield = first_number(row, "DividendYield", "YieldRatio", "殖利率(%)", "殖利率")
        values[code] = {
            "assetType": "stock",
            "metric": "pe" if pe and pe > 0 else None,
            "value": round(pe, 2) if pe and pe > 0 else None,
            "pe": round(pe, 2) if pe and pe > 0 else None,
            "pb": round(pb, 2) if pb is not None else None,
            "dividendYield": round(dividend_yield, 2) if dividend_yield is not None else None,
            "source": "TPEx OpenAPI tpex_mainboard_peratio_analysis",
            "asOf": normalize_data_date(first_text(row, "Date", "DataDate", "資料日期")) or datetime.now(timezone.utc).date().isoformat(),
            "dataStatus": "current" if pe and pe > 0 else "partial",
        }
    return values, "TPEx OpenAPI tpex_mainboard_peratio_analysis"


def sec_ticker_map() -> dict[str, str]:
    payload = fetch_json(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": SEC_USER_AGENT},
    )
    mapping: dict[str, str] = {}
    for item in payload.values() if isinstance(payload, dict) else []:
        ticker = normalize_code(item.get("ticker"))
        cik = int(item.get("cik_str") or 0)
        if ticker and cik:
            mapping[ticker] = f"{cik:010d}"
    return mapping


def fact_units(facts: dict, concepts: tuple[str, ...], unit: str) -> list[dict]:
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for concept in concepts:
        entries = us_gaap.get(concept, {}).get("units", {}).get(unit, [])
        if entries:
            return entries
    return []


def annual_series(entries: list[dict], *, duration: bool = True) -> list[dict]:
    by_end: dict[str, dict] = {}
    for item in entries:
        form = str(item.get("form") or "")
        if form not in {"10-K", "10-K/A"}:
            continue
        if duration:
            start, end = item.get("start"), item.get("end")
            if not start or not end:
                continue
            try:
                span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
            except ValueError:
                continue
            if span < 250:
                continue
        end = str(item.get("end") or "")
        if not end:
            continue
        existing = by_end.get(end)
        if existing is None or str(item.get("filed") or "") > str(existing.get("filed") or ""):
            by_end[end] = item
    return [by_end[key] for key in sorted(by_end, reverse=True)]


def latest_value(entries: list[dict], *, duration: bool = True, offset: int = 0) -> tuple[float | None, str]:
    series = annual_series(entries, duration=duration)
    if len(series) <= offset:
        return None, ""
    return parse_market_number(series[offset].get("val")), str(series[offset].get("end") or "")


def growth_percent(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1) * 100


def sec_stock_valuation(code: str, cik: str, price: float | None) -> dict:
    facts = fetch_json(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        headers={"User-Agent": SEC_USER_AGENT},
        timeout=25,
    )
    eps_entries = fact_units(facts, ("EarningsPerShareDiluted", "EarningsPerShareBasic"), "USD/shares")
    revenue_entries = fact_units(
        facts,
        ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
        "USD",
    )
    net_income_entries = fact_units(facts, ("NetIncomeLoss", "ProfitLoss"), "USD")
    assets_entries = fact_units(facts, ("Assets",), "USD")
    liabilities_entries = fact_units(facts, ("Liabilities",), "USD")
    equity_entries = fact_units(facts, ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"), "USD")
    cashflow_entries = fact_units(facts, ("NetCashProvidedByUsedInOperatingActivities",), "USD")
    capex_entries = fact_units(facts, ("PaymentsToAcquirePropertyPlantAndEquipment",), "USD")
    share_entries = fact_units(facts, ("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"), "shares")

    eps, eps_asof = latest_value(eps_entries)
    previous_eps, _ = latest_value(eps_entries, offset=1)
    revenue, revenue_asof = latest_value(revenue_entries)
    previous_revenue, _ = latest_value(revenue_entries, offset=1)
    net_income, income_asof = latest_value(net_income_entries)
    assets, assets_asof = latest_value(assets_entries, duration=False)
    liabilities, _ = latest_value(liabilities_entries, duration=False)
    equity, equity_asof = latest_value(equity_entries, duration=False)
    previous_equity, _ = latest_value(equity_entries, duration=False, offset=1)
    cashflow, cashflow_asof = latest_value(cashflow_entries)
    capex, _ = latest_value(capex_entries)
    shares, shares_asof = latest_value(share_entries)

    pe = price / eps if price and eps and eps > 0 else None
    pb = price / (equity / shares) if price and equity and equity > 0 and shares and shares > 0 else None
    roe = net_income / ((equity + previous_equity) / 2) * 100 if net_income is not None and equity and previous_equity else None
    debt_ratio = liabilities / assets * 100 if liabilities is not None and assets else None
    market_cap = price * shares if price and shares else None
    fcf_yield = (cashflow - (capex or 0)) / market_cap * 100 if cashflow is not None and market_cap else None
    metrics = {
        "assetType": "stock",
        "metric": "pe" if pe and pe > 0 else None,
        "value": round(pe, 2) if pe and pe > 0 else None,
        "pe": round(pe, 2) if pe and pe > 0 else None,
        "pb": round(pb, 2) if pb is not None else None,
        "roe": round(roe, 2) if roe is not None else None,
        "revenueGrowth": round(growth_percent(revenue, previous_revenue), 2) if growth_percent(revenue, previous_revenue) is not None else None,
        "epsGrowth": round(growth_percent(eps, previous_eps), 2) if growth_percent(eps, previous_eps) is not None else None,
        "debtRatio": round(debt_ratio, 2) if debt_ratio is not None else None,
        "fcfYield": round(fcf_yield, 2) if fcf_yield is not None else None,
        "source": "SEC EDGAR Company Facts (latest annual filing)",
        "asOf": max(eps_asof, revenue_asof, income_asof, assets_asof, equity_asof, cashflow_asof, shares_asof),
    }
    available = sum(metrics.get(key) is not None for key in ("pe", "pb", "roe", "revenueGrowth", "epsGrowth", "debtRatio", "fcfYield"))
    metrics["dataStatus"] = "current" if available >= 4 else "partial" if available else "manual-needed"
    return metrics


def wantgoo_etf_valuations() -> tuple[dict[str, dict], str]:
    payload = fetch_json(
        WANTGOO_ETF_DATA_URL,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": WANTGOO_ETF_PAGE_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=25,
    )
    rows = payload if isinstance(payload, list) else []
    values: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = normalize_code(row.get("stockNo"))
        nav = parse_market_number(row.get("bookValue"))
        market_price = parse_market_number(row.get("deal"))
        if not code or nav is None or nav <= 0:
            continue
        premium = (market_price / nav - 1) * 100 if market_price is not None else None
        as_of = normalize_data_date(row.get("date"))
        status = date_data_status(as_of)
        values[code] = {
            "assetType": "etf",
            "metric": "premium" if premium is not None else None,
            "value": round(premium, 4) if premium is not None else None,
            "nav": round(nav, 4),
            "premium": round(premium, 4) if premium is not None else None,
            "navMarketPrice": round(market_price, 4) if market_price is not None else None,
            "source": "WantGoo ETF 淨值及折溢價",
            "referenceUrl": WANTGOO_ETF_PAGE_URL,
            "referenceLabel": "WantGoo 折溢價",
            "asOf": as_of,
            "dataStatus": status,
            "stale": status == "stale",
        }
    if not values:
        raise RuntimeError("WantGoo returned no usable ETF NAV rows")
    return values, "WantGoo ETF 淨值及折溢價"


def sitca_etf_valuations() -> tuple[dict[str, dict], str]:
    req = urllib.request.Request(
        SITCA_DAILY_NAV_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/csv,text/plain,*/*",
            "Referer": "https://www.sitca.org.tw/",
        },
    )
    with urllib.request.urlopen(req, timeout=40) as response:
        text = decode_response_bytes(response.read())

    values: dict[str, dict] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 12:
            continue
        as_of = normalize_data_date(row[0])
        code = normalize_code(row[11])
        nav = parse_market_number(row[6])
        currency = normalize_code(row[10])
        if not code or nav is None or nav <= 0 or currency != "TWD":
            continue
        existing = values.get(code)
        if existing and str(existing.get("asOf") or "") > as_of:
            continue
        status = date_data_status(as_of)
        values[code] = {
            "assetType": "etf",
            "metric": None,
            "value": None,
            "nav": round(nav, 4),
            "source": "投信投顧公會每日淨值",
            "referenceUrl": WANTGOO_ETF_PAGE_URL,
            "referenceLabel": "WantGoo 折溢價",
            "asOf": as_of,
            "dataStatus": status,
            "stale": status == "stale",
        }
    if not values:
        raise RuntimeError("SITCA returned no usable TWD fund NAV rows")
    return values, "SITCA daily NAV open data"


def tw_etf_valuations() -> tuple[dict[str, dict], list[dict[str, str]], list[dict[str, str]]]:
    source_results: dict[str, dict[str, dict]] = {}
    references: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for source_name, url, fetcher, optional in (
        ("WantGoo ETF 淨值及折溢價", WANTGOO_ETF_PAGE_URL, wantgoo_etf_valuations, True),
        ("投信投顧公會每日淨值", SITCA_DAILY_NAV_URL, sitca_etf_valuations, False),
    ):
        try:
            values, _ = fetcher()
            source_results[source_name] = values
            references.append({"name": source_name, "url": url, "status": "available"})
            print(f"OK ETF NAV from {source_name} -> {len(values)} rows")
        except Exception as exc:  # noqa: BLE001 - the other source remains usable.
            references.append({"name": source_name, "url": url, "status": "unavailable", "error": str(exc)})
            if not optional:
                errors.append({"source": source_name, "error": str(exc)})
            print(f"FAIL ETF NAV from {source_name} - {exc}", file=sys.stderr)

    wantgoo = source_results.get("WantGoo ETF 淨值及折溢價") or {}
    sitca = source_results.get("投信投顧公會每日淨值") or {}
    combined: dict[str, dict] = {}
    for code in set(sitca) | set(wantgoo):
        if code in wantgoo:
            combined[code] = {**wantgoo[code]}
            if code in sitca:
                combined[code]["source"] = "WantGoo ETF 淨值及折溢價；投信投顧公會交叉查核"
                combined[code]["crossCheckNav"] = sitca[code].get("nav")
                combined[code]["crossCheckAsOf"] = sitca[code].get("asOf")
        else:
            combined[code] = {**sitca[code]}
    return combined, references, errors


def load_previous_valuations() -> dict[str, dict]:
    try:
        payload = json.loads(VALUATIONS_OUTPUT_FILE.read_text(encoding="utf-8-sig"))
        return payload.get("valuations") or {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def update_valuations(symbols: dict[str, list[dict[str, str]]], prices: dict[str, dict]) -> dict:
    previous = load_previous_valuations()
    valuations: dict[str, dict] = {}
    errors: list[dict[str, str]] = []
    tw_sources: list[tuple[dict[str, dict], str]] = []

    for source_name, fetcher in (("TWSE OpenAPI", twse_valuations), ("TPEx OpenAPI", tpex_valuations)):
        try:
            tw_sources.append(fetcher())
        except Exception as exc:  # noqa: BLE001 - retain other sources and prior values.
            errors.append({"source": source_name, "error": str(exc)})
            print(f"FAIL valuations from {source_name} - {exc}", file=sys.stderr)

    combined_tw: dict[str, dict] = {}
    for values, _ in tw_sources:
        combined_tw.update(values)

    etf_values, reference_sources, etf_errors = tw_etf_valuations()
    errors.extend(etf_errors)

    for row in symbols.get("tw", []):
        code = normalize_code(row.get("code"))
        if not code:
            continue
        key = f"TW:{code}"
        if is_tw_etf(code, str(row.get("name") or "")):
            if code in etf_values:
                valuations[key] = etf_values[code]
            elif key in previous and parse_market_number(previous[key].get("nav")):
                valuations[key] = {**previous[key], "dataStatus": "stale", "stale": True}
            else:
                valuations[key] = {
                    "assetType": "etf",
                    "metric": None,
                    "value": None,
                    "source": "ETF classification; NAV source unavailable",
                    "referenceUrl": WANTGOO_ETF_PAGE_URL,
                    "referenceLabel": "WantGoo 折溢價",
                    "asOf": "",
                    "dataStatus": "manual-needed",
                }
        elif code in combined_tw:
            valuations[key] = combined_tw[code]
        elif key in previous:
            valuations[key] = {**previous[key], "dataStatus": "stale", "stale": True}
        else:
            valuations[key] = {
                "assetType": "stock",
                "metric": None,
                "value": None,
                "source": "TWSE/TPEx OpenAPI",
                "asOf": "",
                "dataStatus": "manual-needed",
            }

    us_rows = [row for row in symbols.get("us", []) if normalize_code(row.get("code"))]
    try:
        ticker_map = sec_ticker_map()
    except Exception as exc:  # noqa: BLE001 - keep classifications and prior metrics.
        ticker_map = {}
        errors.append({"source": "SEC ticker map", "error": str(exc)})
        print(f"FAIL SEC ticker map - {exc}", file=sys.stderr)

    seen: set[str] = set()
    for row in us_rows:
        code = normalize_code(row.get("code"))
        if code in seen:
            continue
        seen.add(code)
        key = f"US:{code}"
        quote_item = prices.get(key) or {}
        instrument_type = str(quote_item.get("instrumentType") or "").upper()
        name = str(row.get("name") or "").upper()
        is_etf = instrument_type == "ETF" or "ETF" in name
        if is_etf:
            valuations[key] = {
                "assetType": "etf",
                "metric": None,
                "value": None,
                "source": "Yahoo instrument classification; fund metrics require manual or issuer data",
                "asOf": datetime.now(timezone.utc).date().isoformat(),
                "dataStatus": "manual-needed",
            }
            continue
        cik = ticker_map.get(code)
        if cik:
            try:
                valuations[key] = sec_stock_valuation(code, cik, parse_market_number(quote_item.get("price")))
                print(f"OK SEC:{code} -> {valuations[key].get('dataStatus')}")
                time.sleep(0.12)
                continue
            except Exception as exc:  # noqa: BLE001 - preserve prior metrics when SEC fact structures vary.
                errors.append({"source": "SEC Company Facts", "symbol": code, "error": str(exc)})
                print(f"FAIL SEC:{code} - {exc}", file=sys.stderr)
        if key in previous:
            valuations[key] = {**previous[key], "dataStatus": "stale", "stale": True}
        else:
            valuations[key] = {
                "assetType": "stock" if cik or instrument_type == "EQUITY" else "unknown",
                "metric": None,
                "value": None,
                "source": "SEC EDGAR Company Facts",
                "asOf": "",
                "dataStatus": "manual-needed",
            }

    output = {
        "schemaVersion": 3,
        "generatedAt": now_iso(),
        "source": "TWSE OpenAPI + TPEx OpenAPI + SEC EDGAR + WantGoo/SITCA ETF NAV",
        "referenceSources": reference_sources,
        "valuations": valuations,
        "errors": errors,
    }
    VALUATIONS_OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {VALUATIONS_OUTPUT_FILE} ({len(valuations)} valuations, {len(errors)} errors)")
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

    output = {"generatedAt": now_iso(), "prices": prices, "errors": errors}
    if prices:
        rendered = json.dumps(output, ensure_ascii=False, indent=2)
        OUTPUT_FILE.write_text(rendered, encoding="utf-8")
        if INVEST_OUTPUT_FILE.parent.exists():
            INVEST_OUTPUT_FILE.write_text(rendered, encoding="utf-8")
            print(f"Wrote {INVEST_OUTPUT_FILE}")
        print(f"Wrote {OUTPUT_FILE} ({len(prices)} quotes, {len(errors)} errors)")
    else:
        print(f"No quotes fetched; kept existing {OUTPUT_FILE}", file=sys.stderr)

    update_valuations(symbols, prices)
    update_exchange_rates()
    return 0 if prices else 1


if __name__ == "__main__":
    raise SystemExit(main())
