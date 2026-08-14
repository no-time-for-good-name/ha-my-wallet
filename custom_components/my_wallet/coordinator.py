"""Data update coordinator for a single wallet."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_BASE_CURRENCY,
    CONF_SCAN_INTERVAL,
    CONF_VALORS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    VALOR_AMOUNT,
    VALOR_SYMBOL,
)
from .yahoo import Quote, fetch_fx_rates, fetch_quotes

_LOGGER = logging.getLogger(__name__)


@dataclass
class ValorData:
    """Computed state for a single valor inside the wallet."""

    symbol: str
    amount: float
    quote: Quote | None = None
    fx_rate: float | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.quote is not None and self.fx_rate is not None

    @property
    def value(self) -> float | None:
        """Valor value converted to the wallet base currency."""
        if not self.available:
            return None
        return self.amount * self.quote.price * self.fx_rate


@dataclass
class WalletData:
    """Result of one coordinator update."""

    valors: dict[str, ValorData] = field(default_factory=dict)

    @property
    def total(self) -> float | None:
        """Wallet total in base currency; None when nothing is available."""
        values = [v.value for v in self.valors.values() if v.value is not None]
        if not values:
            return None
        return sum(values)

    @property
    def all_available(self) -> bool:
        return all(v.available for v in self.valors.values())


class WalletCoordinator(DataUpdateCoordinator[WalletData]):
    """Coordinator that periodically refreshes all valors of one wallet."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.base_currency: str = entry.data[CONF_BASE_CURRENCY]
        interval = min(MAX_SCAN_INTERVAL, max(MIN_SCAN_INTERVAL, int(entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.data.get(CONF_NAME, entry.title)}",
            update_interval=timedelta(minutes=interval),
        )

    @property
    def valors(self) -> list[dict[str, Any]]:
        """Configured valors of this wallet (list of {symbol, amount})."""
        return list(self.entry.data.get(CONF_VALORS, []))

    async def _async_update_data(self) -> WalletData:
        valors = self.valors
        if not valors:
            return WalletData()

        session = async_get_clientsession(self.hass)
        symbols = [v[VALOR_SYMBOL] for v in valors]
        try:
            quotes = await fetch_quotes(session, symbols)
        except aiohttp.ClientError as err:  # pragma: no cover - defensive
            raise UpdateFailed(f"Yahoo Finance request failed: {err}") from err

        # Collect distinct currency pairs that need an FX rate.
        pairs: set[tuple[str, str]] = {
            (quote.currency, self.base_currency)
            for quote in quotes.values()
            if quote is not None and quote.currency != self.base_currency
        }
        fx_rates = await fetch_fx_rates(session, pairs) if pairs else {}

        data = WalletData()
        for valor in valors:
            symbol = valor[VALOR_SYMBOL]
            amount = float(valor[VALOR_AMOUNT])
            quote = quotes.get(symbol)
            item = ValorData(symbol=symbol, amount=amount, quote=quote)
            if quote is None:
                item.error = "quote_unavailable"
            elif quote.currency == self.base_currency:
                item.fx_rate = 1.0
            else:
                item.fx_rate = fx_rates.get((quote.currency, self.base_currency))
                if item.fx_rate is None:
                    item.error = "fx_rate_unavailable"
            data.valors[symbol] = item

        if not data.all_available:
            _LOGGER.warning(
                "Wallet %s: some valors could not be updated: %s",
                self.name,
                [s for s, v in data.valors.items() if not v.available],
            )
        return data
