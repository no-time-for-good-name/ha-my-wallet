"""Async Yahoo Finance client based on the public chart endpoint.

Uses ``https://query1.finance.yahoo.com/v8/finance/chart/{symbol}`` which
works without authentication (no cookie/crumb dance) and returns everything
we need: last price, previous close, currency and the instrument name.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
REQUEST_TIMEOUT = 20
# Yahoo rejects requests without a browser-like User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


class YahooError(Exception):
    """Raised when a quote cannot be fetched or parsed."""


@dataclass
class Quote:
    """A single market quote."""

    symbol: str
    price: float
    currency: str
    previous_close: float | None = None
    short_name: str | None = None

    @property
    def day_change(self) -> float | None:
        """Absolute change vs previous close."""
        if self.previous_close is None or self.price is None:
            return None
        return self.price - self.previous_close

    @property
    def day_change_pct(self) -> float | None:
        """Percentage change vs previous close."""
        if not self.previous_close:
            return None
        return (self.price - self.previous_close) / self.previous_close * 100


def _to_float(value: Any) -> float | None:
    """Best-effort conversion, treating None/invalid as missing."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fx_symbol(from_currency: str, to_currency: str) -> str:
    """Build the Yahoo symbol for an FX pair, e.g. ``USDPLN=X``."""
    return f"{from_currency}{to_currency}=X"


async def fetch_quote(session: aiohttp.ClientSession, symbol: str) -> Quote:
    """Fetch a single quote from the chart endpoint.

    Raises YahooError on HTTP errors, timeouts or unparsable payloads.
    """
    url = CHART_URL.format(symbol=symbol)
    try:
        async with session.get(
            url, params={"range": "1d", "interval": "1d"}, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as resp:
            if resp.status != 200:
                raise YahooError(f"HTTP {resp.status} for {symbol}")
            payload: dict[str, Any] = await resp.json()
    except (TimeoutError, aiohttp.ClientError) as err:
        raise YahooError(f"Request failed for {symbol}: {err}") from err

    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        price = meta["regularMarketPrice"]
        currency = meta["currency"]
    except (KeyError, IndexError, TypeError) as err:
        raise YahooError(f"Unexpected payload for {symbol}: {err}") from err

    if price is None:
        raise YahooError(f"No price in payload for {symbol}")

    return Quote(
        symbol=symbol,
        price=float(price),
        currency=str(currency).upper(),
        previous_close=_to_float(meta.get("chartPreviousClose") or meta.get("previousClose")),
        short_name=meta.get("shortName") or meta.get("longName"),
    )


async def fetch_quotes(
    session: aiohttp.ClientSession, symbols: list[str]
) -> dict[str, Quote | None]:
    """Fetch several quotes in parallel.

    Returns a mapping of symbol to Quote, or None when that symbol failed
    (a single bad symbol must not break the whole wallet update).
    """

    async def _safe(symbol: str) -> Quote | None:
        try:
            return await fetch_quote(session, symbol)
        except YahooError as err:
            _LOGGER.warning("Yahoo Finance quote failed: %s", err)
            return None

    results = await asyncio.gather(*(_safe(sym) for sym in symbols))
    return dict(zip(symbols, results, strict=True))


async def fetch_fx_rates(
    session: aiohttp.ClientSession,
    pairs: set[tuple[str, str]],
) -> dict[tuple[str, str], float | None]:
    """Fetch FX rates for ``pairs`` of (from_currency, to_currency).

    Tries the direct Yahoo pair first (e.g. ``USDPLN=X``); if unavailable,
    falls back to the inverse pair (``PLNUSD=X``) and reciprocates it.
    """
    to_fetch: set[str] = set()
    for frm, to in pairs:
        if frm == to:
            continue
        to_fetch.add(fx_symbol(frm, to))
        to_fetch.add(fx_symbol(to, frm))

    quotes = await fetch_quotes(session, sorted(to_fetch)) if to_fetch else {}

    rates: dict[tuple[str, str], float | None] = {}
    for frm, to in pairs:
        if frm == to:
            rates[(frm, to)] = 1.0
            continue
        direct = quotes.get(fx_symbol(frm, to))
        if direct is not None:
            rates[(frm, to)] = direct.price
            continue
        inverse = quotes.get(fx_symbol(to, frm))
        if inverse is not None and inverse.price:
            rates[(frm, to)] = 1.0 / inverse.price
            continue
        _LOGGER.warning("No FX rate found for %s -> %s", frm, to)
        rates[(frm, to)] = None
    return rates
