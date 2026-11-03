"""Sensor platform for My Wallet: one sensor per valor plus a wallet total."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform, entity_registry as er, selector
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MyWalletConfigEntry
from .const import (
    ATTR_AMOUNT,
    ATTR_CASH,
    ATTR_DAY_CHANGE,
    ATTR_DAY_CHANGE_PCT,
    ATTR_FX_RATE,
    ATTR_INVESTED,
    ATTR_OPERATION,
    ATTR_PREVIOUS_CLOSE,
    ATTR_QUANTITY,
    ATTR_QUOTE_CURRENCY,
    ATTR_REBALANCE_AMOUNT,
    ATTR_REBALANCE_PRECISION,
    ATTR_REBALANCE_UNITS,
    ATTR_SHARE,
    ATTR_SHARE_DEVIATION,
    ATTR_SHORT_NAME,
    ATTR_SYMBOL,
    ATTR_TARGET_SHARE,
    ATTR_TOTAL,
    ATTR_UNIT_PRICE,
    ATTR_VALUE,
    CONF_CASH_AMOUNT,
    CONF_INVESTED_AMOUNT,
    CONF_REBALANCE_PRECISION,
    CONF_VALORS,
    CONF_WALLET_NAME,
    DOMAIN,
    DEFAULT_REBALANCE_PRECISION,
    MAX_REBALANCE_PRECISION,
    OPERATION_BUY,
    OPERATION_DEPOSIT,
    OPERATION_SELL,
    OPERATION_SET,
    OPERATION_WITHDRAW,
    SERVICE_ADJUST_CASH,
    SERVICE_ADJUST_INVESTED,
    SERVICE_REFRESH,
    SERVICE_TRADE,
    VALOR_AMOUNT,
    VALOR_SYMBOL,
)
from .coordinator import ValorData, WalletCoordinator, WalletData

_REFRESH_SCHEMA: dict[str, Any] = {}


def _invested_amount(entry: ConfigEntry) -> float | None:
    """Positive invested amount from the entry, or None when profit tracking is off."""
    value = entry.data.get(CONF_INVESTED_AMOUNT)
    if value is None or float(value) <= 0:
        return None
    return float(value)


def _cash_amount(entry: ConfigEntry) -> float | None:
    """Tracked free cash from the entry, or None when cash tracking is off."""
    value = entry.data.get(CONF_CASH_AMOUNT)
    if value is None:
        return None
    return max(0.0, float(value))


def _resolve_entry(hass: HomeAssistant, entity_id: str) -> ConfigEntry:
    """Config entry of the wallet that owns the given sensor entity."""
    registry = er.async_get(hass)
    entity_entry = registry.async_get(entity_id)
    resolved_entry_id = (
        entity_entry.config_entry_id
        if entity_entry and entity_entry.config_entry_id
        else None
    )
    if resolved_entry_id is None:
        raise HomeAssistantError(
            f"{entity_id} does not belong to a {DOMAIN} config entry."
        )
    target_entry = hass.config_entries.async_get_entry(resolved_entry_id)
    if target_entry is None or target_entry.domain != DOMAIN:
        raise HomeAssistantError(f"Unknown {DOMAIN} config entry for {entity_id}.")
    return target_entry


def _update_entry_data(
    hass: HomeAssistant, entry: ConfigEntry, **changes: float
) -> None:
    """Persist value changes in the entry; the update listener reloads the wallet."""
    data = dict(entry.data)
    data.update(changes)
    hass.config_entries.async_update_entry(entry, data=data)


def _unit_price_from_call(
    service: ServiceCall, coordinator: WalletCoordinator, symbol: str
) -> float:
    """Explicit unit price from the call, or the latest available quote."""
    explicit = service.data.get(ATTR_UNIT_PRICE)
    if explicit is not None:
        price = float(explicit)
        if price <= 0:
            raise HomeAssistantError("Unit price must be positive.")
        return price
    valor = coordinator.data.valors.get(symbol)
    if (
        valor is None
        or valor.quote is None
        or not valor.quote.price
        or valor.fx_rate is None
    ):
        raise HomeAssistantError(
            f"No live quote available for {symbol}; pass {ATTR_UNIT_PRICE} explicitly."
        )
    return float(valor.quote.price) * float(valor.fx_rate)


def _share(valor: ValorData, total: float | None) -> float | None:
    """Actual share of the valor in the wallet total, in percent (2 decimals)."""
    if total is None or valor.value is None or total <= 0:
        return None
    return round(valor.value / total * 100, 2)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MyWalletConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create all sensors for a wallet."""
    coordinator: WalletCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        WalletTotalSensor(coordinator, entry),
        WalletInvestedSensor(coordinator, entry),
        WalletCashSensor(coordinator, entry),
        WalletProfitSensor(coordinator, entry),
        WalletProfitPctSensor(coordinator, entry),
    ]
    entities.extend(
        ValorSensor(coordinator, entry, valor[VALOR_SYMBOL])
        for valor in coordinator.valors
    )
    entities.extend(
        ValorDeviationSensor(coordinator, entry, valor[VALOR_SYMBOL])
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

    # my_wallet.adjust_invested: deposit / withdraw / set the invested amount.
    if not hass.services.has_service(DOMAIN, SERVICE_ADJUST_INVESTED):

        @callback
        async def _async_adjust_invested(service: ServiceCall) -> None:
            entity_id: str = service.data[ATTR_ENTITY_ID]
            target_entry = _resolve_entry(hass, entity_id)
            operation: str = service.data[ATTR_OPERATION]
            amount = float(service.data[ATTR_AMOUNT])
            current = target_entry.data.get(CONF_INVESTED_AMOUNT)
            current = float(current) if current is not None else 0.0
            if operation == OPERATION_SET:
                new_value = amount
            elif operation == OPERATION_DEPOSIT:
                new_value = current + amount
            else:  # OPERATION_WITHDRAW
                new_value = current - amount
                if new_value < 0:
                    raise HomeAssistantError(
                        "Withdrawal would make the invested amount negative "
                        f"(current: {current}, withdrawal: {amount})."
                    )
            changes: dict[str, float] = {
                CONF_INVESTED_AMOUNT: round(new_value, 2)
            }
            # Deposits / withdrawals of real money also change the free cash,
            # when cash tracking is enabled for this wallet.
            if operation in (OPERATION_DEPOSIT, OPERATION_WITHDRAW):
                cash = _cash_amount(target_entry)
                if cash is not None:
                    new_cash = cash + amount if operation == OPERATION_DEPOSIT else cash - amount
                    if new_cash < 0:
                        raise HomeAssistantError(
                            "Withdrawal would make the free cash negative "
                            f"(current cash: {cash}, withdrawal: {amount})."
                        )
                    changes[CONF_CASH_AMOUNT] = round(new_cash, 2)
            _update_entry_data(hass, target_entry, **changes)

        hass.services.async_register(
            DOMAIN,
            SERVICE_ADJUST_INVESTED,
            _async_adjust_invested,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_ENTITY_ID): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN, domain="sensor"
                        )
                    ),
                    vol.Required(ATTR_OPERATION): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[OPERATION_DEPOSIT, OPERATION_WITHDRAW, OPERATION_SET],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(ATTR_AMOUNT): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=10_000_000_000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

    # my_wallet.adjust_cash: deposit / withdraw / set the tracked free cash.
    if not hass.services.has_service(DOMAIN, SERVICE_ADJUST_CASH):

        @callback
        async def _async_adjust_cash(service: ServiceCall) -> None:
            entity_id: str = service.data[ATTR_ENTITY_ID]
            target_entry = _resolve_entry(hass, entity_id)
            operation: str = service.data[ATTR_OPERATION]
            amount = float(service.data[ATTR_AMOUNT])
            current = _cash_amount(target_entry) or 0.0
            if operation == OPERATION_SET:
                new_value = amount
            elif operation == OPERATION_DEPOSIT:
                new_value = current + amount
            else:  # OPERATION_WITHDRAW
                new_value = current - amount
                if new_value < 0:
                    raise HomeAssistantError(
                        "Withdrawal would make the free cash negative "
                        f"(current cash: {current}, withdrawal: {amount})."
                    )
            _update_entry_data(
                hass, target_entry, **{CONF_CASH_AMOUNT: round(new_value, 2)}
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_ADJUST_CASH,
            _async_adjust_cash,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_ENTITY_ID): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN, domain="sensor"
                        )
                    ),
                    vol.Required(ATTR_OPERATION): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[OPERATION_DEPOSIT, OPERATION_WITHDRAW, OPERATION_SET],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(ATTR_AMOUNT): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=10_000_000_000,
                            step=0.01,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

    # my_wallet.trade: buy / sell a valor by quantity; cash follows the trade.
    if not hass.services.has_service(DOMAIN, SERVICE_TRADE):

        @callback
        async def _async_trade(service: ServiceCall) -> None:
            entity_id: str = service.data[ATTR_ENTITY_ID]
            target_entry = _resolve_entry(hass, entity_id)
            coordinator: WalletCoordinator = target_entry.runtime_data
            operation: str = service.data[ATTR_OPERATION]
            symbol: str = str(service.data[ATTR_SYMBOL]).strip().upper()
            quantity = float(service.data[ATTR_QUANTITY])
            precision = min(
                MAX_REBALANCE_PRECISION,
                max(
                    0,
                    int(
                        target_entry.data.get(
                            CONF_REBALANCE_PRECISION, DEFAULT_REBALANCE_PRECISION
                        )
                    ),
                ),
            )
            quantity = round(quantity, precision)
            if quantity <= 0:
                raise HomeAssistantError(
                    f"Quantity rounds to {quantity} at the wallet precision "
                    f"({precision} decimal places); use a larger quantity."
                )
            valor_entry = next(
                (v for v in target_entry.data.get(CONF_VALORS, [])
                 if v[VALOR_SYMBOL] == symbol),
                None,
            )
            if valor_entry is None:
                raise HomeAssistantError(
                    f"{symbol} is not part of this wallet; add it first."
                )
            unit_price = _unit_price_from_call(service, coordinator, symbol)
            amount = round(quantity * unit_price, 2)
            cash = _cash_amount(target_entry)
            new_cash: float | None = None
            new_amount = float(valor_entry[VALOR_AMOUNT])
            if operation == OPERATION_BUY:
                new_amount += quantity
                if cash is not None:
                    if cash < amount:
                        raise HomeAssistantError(
                            f"Insufficient free cash for buying {quantity} x "
                            f"{symbol} for {amount} (available: {cash})."
                        )
                    new_cash = cash - amount
            else:  # OPERATION_SELL
                new_amount -= quantity
                if new_amount < 0:
                    raise HomeAssistantError(
                        f"Cannot sell {quantity} x {symbol}: only "
                        f"{valor_entry[VALOR_AMOUNT]} held."
                    )
                if cash is not None:
                    new_cash = cash + amount
            changes: dict[str, Any] = {
                CONF_VALORS: [
                    dict(v, **{VALOR_AMOUNT: new_amount})
                    if v[VALOR_SYMBOL] == symbol
                    else v
                    for v in target_entry.data.get(CONF_VALORS, [])
                ]
            }
            if cash is not None and new_cash is not None:
                changes[CONF_CASH_AMOUNT] = round(new_cash, 2)
            _update_entry_data(hass, target_entry, **changes)
            service.async_create_task(
                coordinator.async_request_refresh()
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_TRADE,
            _async_trade,
            schema=vol.Schema(
                {
                    vol.Required(ATTR_ENTITY_ID): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            integration=DOMAIN, domain="sensor"
                        )
                    ),
                    vol.Required(ATTR_OPERATION): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[OPERATION_BUY, OPERATION_SELL],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(ATTR_SYMBOL): str,
                    vol.Required(ATTR_QUANTITY): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=1_000_000_000,
                            step=0.001,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(ATTR_UNIT_PRICE): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0,
                            max=10_000_000_000,
                            step=0.001,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )


def _cleanup_orphaned_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove registry entries of valors that are no longer configured."""
    registry = er.async_get(hass)
    valid_symbols = {valor[VALOR_SYMBOL] for valor in entry.data.get(CONF_VALORS, [])}
    valid_unique_ids = {f"{entry.entry_id}_{s}" for s in valid_symbols}
    valid_unique_ids.update(f"{entry.entry_id}_{s}_deviation" for s in valid_symbols)
    valid_unique_ids.update(
        {
            f"{entry.entry_id}_total",
            f"{entry.entry_id}_invested",
            f"{entry.entry_id}_cash",
            f"{entry.entry_id}_profit",
            f"{entry.entry_id}_profit_pct",
        }
    )

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

    _attr_icon = "mdi:chart-box-outline"

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
        total = self.data.total
        share = _share(valor, total)
        if share is not None:
            attributes[ATTR_SHARE] = share
            if valor.has_target:
                # Positive deviation: the valor is overweight vs its target.
                attributes[ATTR_SHARE_DEVIATION] = round(
                    valor.value / total * 100 - valor.target_share, 2
                )
                # Positive rebalance amount: buy for this much to reach the target.
                attributes[ATTR_REBALANCE_AMOUNT] = round(
                    total * valor.target_share / 100 - valor.value, 2
                )
        if valor.has_target:
            attributes[ATTR_TARGET_SHARE] = valor.target_share
            if valor.rebalance_units is not None:
                # Positive: buy this many units, negative: sell them.
                attributes[ATTR_REBALANCE_UNITS] = valor.rebalance_units
                attributes[ATTR_REBALANCE_PRECISION] = (
                    self.coordinator.rebalance_precision
                )
        if valor.error:
            attributes["error"] = valor.error
        return attributes


class ValorDeviationSensor(WalletBaseSensor):
    """Deviation of a valor's actual share from its target share, in percent points."""

    _attr_icon = "mdi:target-variant"

    def __init__(
        self, coordinator: WalletCoordinator, entry: ConfigEntry, symbol: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._symbol = symbol
        self._attr_unique_id = f"{entry.entry_id}_{symbol}_deviation"
        self._attr_translation_key = "valor_deviation"
        self._attr_translation_placeholders = {"symbol": symbol}
        self._attr_device_class = None
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "%"

    @property
    def _valor(self) -> ValorData | None:
        return self.coordinator.data.valors.get(self._symbol)

    @property
    def native_value(self) -> float | None:
        """Actual share minus target share; positive when the valor is overweight."""
        valor = self._valor
        total = self.coordinator.data.total
        if valor is None or not valor.has_target or total is None or total <= 0:
            return None
        if valor.value is None:
            return None
        return round(valor.value / total * 100 - valor.target_share, 2)

    @property
    def available(self) -> bool:
        valor = self._valor
        return (
            super().available
            and valor is not None
            and valor.available
            and valor.has_target
            and self.coordinator.data.total is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        valor = self._valor
        total = self.coordinator.data.total
        attributes: dict[str, Any] = {ATTR_SYMBOL: self._symbol}
        if valor is None or not valor.has_target:
            return attributes
        attributes[ATTR_TARGET_SHARE] = valor.target_share
        if valor.value is None or total is None or total <= 0:
            return attributes
        attributes[ATTR_SHARE] = _share(valor, total)
        attributes[ATTR_VALUE] = round(valor.value, 2)
        # Positive rebalance amount: buy for this much to reach the target.
        attributes[ATTR_REBALANCE_AMOUNT] = round(
            total * valor.target_share / 100 - valor.value, 2
        )
        if valor.rebalance_units is not None:
            # Positive: buy this many units, negative: sell them.
            attributes[ATTR_REBALANCE_UNITS] = valor.rebalance_units
            attributes[ATTR_REBALANCE_PRECISION] = self.coordinator.rebalance_precision
        return attributes


class WalletTotalSensor(WalletBaseSensor):
    """Total value of all valors in the wallet, in base currency."""

    _attr_icon = "mdi:wallet-outline"

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
        total = data.total
        return {
            "valors": {
                symbol: {
                    "amount": valor.amount,
                    "value": valor.value,
                    "unit_price": valor.quote.price if valor.quote else None,
                    "quote_currency": valor.quote.currency if valor.quote else None,
                    "fx_rate": valor.fx_rate,
                    "share": _share(valor, total),
                    "target_share": valor.target_share,
                    "rebalance_units": valor.rebalance_units,
                }
                for symbol, valor in data.valors.items()
            },
            "unavailable_valors": sorted(
                symbol for symbol, valor in data.valors.items() if not valor.available
            ),
        }


class WalletInvestedSensor(WalletBaseSensor):
    """Amount invested in the wallet (user-provided), in base currency."""

    _attr_icon = "mdi:cash-lock"

    def __init__(self, coordinator: WalletCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_invested"
        self._attr_translation_key = "wallet_invested"

    @property
    def native_value(self) -> float | None:
        return _invested_amount(self._entry)

    @property
    def available(self) -> bool:
        return _invested_amount(self._entry) is not None


class WalletCashSensor(WalletBaseSensor):
    """Tracked free cash of the wallet, in base currency."""

    _attr_icon = "mdi:cash"

    def __init__(self, coordinator: WalletCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cash"
        self._attr_translation_key = "wallet_cash"

    @property
    def native_value(self) -> float | None:
        return _cash_amount(self._entry)

    @property
    def available(self) -> bool:
        return _cash_amount(self._entry) is not None


class WalletProfitSensor(WalletBaseSensor):
    """Unrealized profit of the wallet: total value minus invested amount.

    When cash tracking is enabled the cash counts towards the profit:
    profit = (valor total + free cash) − invested amount.
    """

    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator: WalletCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_profit"
        self._attr_translation_key = "wallet_profit"

    @property
    def _total_with_cash(self) -> float | None:
        total = self.coordinator.data.total
        if total is None:
            return None
        cash = _cash_amount(self._entry)
        return total + cash if cash is not None else total

    @property
    def native_value(self) -> float | None:
        invested = _invested_amount(self._entry)
        total = self._total_with_cash
        if invested is None or total is None:
            return None
        return round(total - invested, 2)

    @property
    def available(self) -> bool:
        return (
            super().available
            and _invested_amount(self._entry) is not None
            and self.coordinator.data.total is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_INVESTED: _invested_amount(self._entry),
            ATTR_TOTAL: self._total_with_cash,
            ATTR_CASH: _cash_amount(self._entry),
        }


class WalletProfitPctSensor(WalletBaseSensor):
    """Unrealized profit of the wallet in percent of the invested amount.

    Includes the tracked free cash when enabled (same as the profit sensor).
    """

    _attr_icon = "mdi:percent"

    def __init__(self, coordinator: WalletCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_profit_pct"
        self._attr_translation_key = "wallet_profit_pct"
        self._attr_device_class = None
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> float | None:
        invested = _invested_amount(self._entry)
        total = self.coordinator.data.total
        if invested is None or total is None:
            return None
        cash = _cash_amount(self._entry)
        if cash is not None:
            total += cash
        return round((total - invested) / invested * 100, 2)

    @property
    def available(self) -> bool:
        return (
            super().available
            and _invested_amount(self._entry) is not None
            and self.coordinator.data.total is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_INVESTED: _invested_amount(self._entry),
            ATTR_TOTAL: self.coordinator.data.total,
            ATTR_CASH: _cash_amount(self._entry),
        }
