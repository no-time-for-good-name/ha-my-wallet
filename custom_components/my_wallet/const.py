"""Constants for the My Wallet integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "my_wallet"

PLATFORMS: Final = [Platform.SENSOR]

# Config entry keys
CONF_WALLET_NAME: Final = "wallet_name"
CONF_BASE_CURRENCY: Final = "base_currency"
CONF_SCAN_INTERVAL: Final = "scan_interval"  # minutes
CONF_VALORS: Final = "valors"

# Valor keys
VALOR_SYMBOL: Final = "symbol"
VALOR_AMOUNT: Final = "amount"

# Defaults
DEFAULT_BASE_CURRENCY: Final = "EUR"
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 1440

# Service names
SERVICE_REFRESH: Final = "refresh"

# Common currencies offered in the config flow
COMMON_CURRENCIES: Final = [
    "USD",
    "EUR",
    "PLN",
    "GBP",
    "CHF",
    "CAD",
    "AUD",
    "JPY",
    "CZK",
    "NOK",
    "SEK",
    "DKK",
    "HUF",
    "RON",
    "BGN",
    "TRY",
    "INR",
    "CNY",
]

ATTR_SYMBOL: Final = "symbol"
ATTR_AMOUNT: Final = "amount"
ATTR_UNIT_PRICE: Final = "unit_price"
ATTR_QUOTE_CURRENCY: Final = "quote_currency"
ATTR_FX_RATE: Final = "fx_rate"
ATTR_DAY_CHANGE: Final = "day_change"
ATTR_DAY_CHANGE_PCT: Final = "day_change_pct"
ATTR_PREVIOUS_CLOSE: Final = "previous_close"
ATTR_SHORT_NAME: Final = "short_name"
ATTR_VALORS: Final = "valors"

UPDATE_MIN_INTERVAL: Final = timedelta(minutes=MIN_SCAN_INTERVAL)
