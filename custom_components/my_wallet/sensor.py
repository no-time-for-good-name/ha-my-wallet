"""Sensor platform for My Wallet: one sensor per valor plus a wallet total."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyWalletConfigEntry
from .const import (
    ATTR_AMOUNT,
    ATTR_DAY_CHANGE,
    ATTR_DAY_CHANGE_PCT,
    ATTR_FX_RATE,
    ATTR_PREVIOUS_CLOSE,
    ATTR_QUOTE_CURRENCY,
    ATTR_SHORT_NAME,
    ATTR_SYMBOL,
    ATTR_UNIT_PRICE,
    CONF_VALORS,
    CONF_WALLET_NAME,
    DOMAIN,
    SERVICE_REFRESH,
    VALOR_SYMBOL,
)
from .coordinator import WalletCoordinator, WalletData

_REFRESH_SCHEMA: dict[str, Any] = {}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyWalletConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create all sensors for a wallet."""
    coordinator: WalletCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        WalletTotalSensor(coordinator, entry),
    ]
    entities.extend(
        ValorSensor(coordinator, entry, valor[VALOR_SYMBOL])
        for valor in coordinator.valors
    )
    async_add_entities(entities)
    _cleanup_orphaned_entities(hass, entry)

    # my_wallet.refresh: force an immediate update of the wallet.
    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        platform = entity_platform.async_get_current_platform()
        platform.async_register_entity_service(
            SERVICE_REFRESH,
            _REFRESH_SCHEMA,
            "async_refresh_wallet",
        )


def _cleanup_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove registry entries of valors that are no longer configured."""
    registry = er.async_get(hass)
    valid_symbols = {valor[VALOR_SYMBOL] for valor in entry.data.get(CONF_VALORS, [])}
    valid_unique_ids = {f"{entry.entry_id}_{s}" for s in valid_symbols}
    valid_unique_ids.add(f"{entry.entry_id}_total")

    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id not in valid_unique_ids:
            registry.async_remove(entity_entry.entity_id)


class WalletBaseSensor(CoordinatorEntity[WalletCoordinator], SensorEntity):
    """Common base for wallet sensors."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator: WalletCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = coordinator.base_currency

    @property
    def device_info(self) -> DeviceInfo:
        """All sensors of one wallet are grouped into a single device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.data.get(CONF_WALLET_NAME, self._entry.title),
            manufacturer="My Wallet",
            model="Wallet",
        )

    async def async_refresh_wallet(self) -> None:
        """Service handler for my_wallet.refresh."""
        await self.coordinator.async_request_refresh()


class ValorSensor(WalletBaseSensor):
    """Value of a single valor (amount x price x FX rate) in base currency."""

    def __init__(
        self, coordinator: WalletCoordinator, entry: ConfigEntry, symbol: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._symbol = symbol
        self._attr_unique_id = f"{entry.entry_id}_{symbol}"
        self._attr_translation_key = "valor"
        self._attr_translation_placeholders = {"symbol": symbol}

    @property
    def data(self) -> WalletData:
        return self.coordinator.data

    @property
    def native_value(self) -> float | None:
        valor = self.data.valors.get(self._symbol)
        if valor is None:
            return None
        return round(valor.value, 2) if valor.value is not None else None

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.data.valors.get(self._symbol) is not None
            and self.data.valors[self._symbol].available
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        valor = self.data.valors.get(self._symbol)
        if valor is None or valor.quote is None:
            return {ATTR_SYMBOL: self._symbol}
        quote = valor.quote
        attributes: dict[str, Any] = {
            ATTR_SYMBOL: self._symbol,
            ATTR_AMOUNT: valor.amount,
            ATTR_UNIT_PRICE: quote.price,
            ATTR_QUOTE_CURRENCY: quote.currency,
            ATTR_FX_RATE: valor.fx_rate,
            ATTR_PREVIOUS_CLOSE: quote.previous_close,
            ATTR_DAY_CHANGE: quote.day_change,
            ATTR_DAY_CHANGE_PCT: quote.day_change_pct,
        }
        if quote.short_name:
            attributes[ATTR_SHORT_NAME] = quote.short_name
        if valor.error:
            attributes["error"] = valor.error
        return attributes


class WalletTotalSensor(WalletBaseSensor):
    """Total value of all valors in the wallet, in base currency."""

    def __init__(self, coordinator: WalletCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_total"
        self._attr_translation_key = "wallet_total"

    @property
    def native_value(self) -> float | None:
        total = self.coordinator.data.total
        return round(total, 2) if total is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "valors": {
                symbol: {
                    "amount": valor.amount,
                    "value": valor.value,
                    "unit_price": valor.quote.price if valor.quote else None,
                    "quote_currency": valor.quote.currency if valor.quote else None,
                    "fx_rate": valor.fx_rate,
                }
                for symbol, valor in data.valors.items()
            },
            "unavailable_valors": sorted(
                symbol for symbol, valor in data.valors.items() if not valor.available
            ),
        }
