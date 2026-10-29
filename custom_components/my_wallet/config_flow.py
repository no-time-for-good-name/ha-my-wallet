"""Config flow for My Wallet."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    ATTR_AMOUNT,
    ATTR_OPERATION,
    ATTR_QUANTITY,
    ATTR_SYMBOL,
    ATTR_UNIT_PRICE,
    COMMON_CURRENCIES,
    CONF_BASE_CURRENCY,
    CONF_CASH_AMOUNT,
    CONF_INVESTED_AMOUNT,
    CONF_REBALANCE_PRECISION,
    CONF_SCAN_INTERVAL,
    CONF_VALORS,
    CONF_WALLET_NAME,
    DEFAULT_BASE_CURRENCY,
    DEFAULT_REBALANCE_PRECISION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_REBALANCE_PRECISION,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OPERATION_BUY,
    OPERATION_DEPOSIT,
    OPERATION_SELL,
    OPERATION_SET,
    OPERATION_WITHDRAW,
    TARGET_SHARE_SUM_TOLERANCE,
    VALOR_AMOUNT,
    VALOR_SYMBOL,
    VALOR_TARGET_SHARE,
)
from .coordinator import WalletCoordinator

_CURRENCY_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=COMMON_CURRENCIES,
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)

_AMOUNT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=10_000_000_000,
        step=0.01,
        mode=selector.NumberSelectorMode.BOX,
    )
)

_INTERVAL_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=MIN_SCAN_INTERVAL,
        max=MAX_SCAN_INTERVAL,
        step=1,
        unit_of_measurement="min",
        mode=selector.NumberSelectorMode.BOX,
    )
)

_REBALANCE_PRECISION_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=MAX_REBALANCE_PRECISION,
        step=1,
        mode=selector.NumberSelectorMode.BOX,
    )
)

_TARGET_SHARE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=100,
        step=0.01,
        unit_of_measurement="%",
        mode=selector.NumberSelectorMode.BOX,
    )
)


def _valor_schema(
    symbol: str | None = None,
    amount: float | None = None,
    target_share: float | None = None,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(VALOR_SYMBOL, default=symbol): str,
            vol.Required(VALOR_AMOUNT, default=amount): _AMOUNT_SELECTOR,
            # Suggested value (not default): an empty optional field is then
            # omitted from the submitted data instead of being sent as null.
            vol.Optional(
                VALOR_TARGET_SHARE, description={"suggested_value": target_share}
            ): vol.Any(None, _TARGET_SHARE_SELECTOR),
        }
    )


def _normalize_invested(value: Any) -> float | None:
    """Return a positive invested amount or None when tracking is disabled."""
    if value is None:
        return None
    invested = float(value)
    return invested if invested > 0 else None


def _normalize_precision(value: Any) -> int:
    """Clamp the rebalance precision to the allowed 0..MAX range."""
    if value is None:
        return DEFAULT_REBALANCE_PRECISION
    return min(MAX_REBALANCE_PRECISION, max(0, int(value)))


def _normalize_cash(value: Any) -> float | None:
    """Return a non-negative cash amount or None when tracking is disabled."""
    if value is None:
        return None
    cash = float(value)
    return cash if cash >= 0 else None


def _operation_schema(
    current: float | None = None,
    operation: str | None = None,
    amount: float | None = None,
) -> vol.Schema:
    """Form for adjusting the invested amount from the options menu."""
    return vol.Schema(
        {
            vol.Required(
                ATTR_OPERATION,
                default=operation or OPERATION_DEPOSIT,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[OPERATION_DEPOSIT, OPERATION_WITHDRAW, OPERATION_SET],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(ATTR_AMOUNT, default=amount): _AMOUNT_SELECTOR,
        }
    )


def _cash_operation_schema(
    current: float | None = None,
    operation: str | None = None,
    amount: float | None = None,
) -> vol.Schema:
    """Form for adjusting the tracked free cash from the options menu."""
    return vol.Schema(
        {
            vol.Required(
                ATTR_OPERATION,
                default=operation or OPERATION_DEPOSIT,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[OPERATION_DEPOSIT, OPERATION_WITHDRAW, OPERATION_SET],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(ATTR_AMOUNT, default=amount): _AMOUNT_SELECTOR,
        }
    )


_TRADE_PRECISION_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=1_000_000_000,
        step=0.001,
        mode=selector.NumberSelectorMode.BOX,
    )
)

_TRADE_PRICE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=10_000_000_000,
        step=0.001,
        mode=selector.NumberSelectorMode.BOX,
    )
)


def _trade_schema(
    valors: Iterable[dict[str, Any]],
    operation: str | None = None,
    symbol: str | None = None,
    quantity: float | None = None,
    unit_price: float | None = None,
) -> vol.Schema:
    """Form for buying / selling a valor from the options menu."""
    schema: dict[Any, Any] = {
        vol.Required(
            ATTR_OPERATION,
            default=operation or OPERATION_BUY,
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[OPERATION_BUY, OPERATION_SELL],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(ATTR_SYMBOL, default=symbol): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[v[VALOR_SYMBOL] for v in valors],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required(ATTR_QUANTITY, default=quantity): _TRADE_PRECISION_SELECTOR,
        vol.Optional(
            ATTR_UNIT_PRICE, description={"suggested_value": unit_price}
        ): vol.Any(None, _TRADE_PRICE_SELECTOR),
    }
    return vol.Schema(schema)


def _normalize_target_share(value: Any) -> float | None:
    """Return a target share in (0, 100] or None when no target is set."""
    if value is None:
        return None
    target = float(value)
    return target if target > 0 else None


def _targets_sum(valors: Iterable[dict[str, Any]]) -> float:
    """Sum of configured target shares."""
    return sum(float(v.get(VALOR_TARGET_SHARE) or 0) for v in valors)


def _target_sum_exceeded(others: Iterable[dict[str, Any]], target: float) -> bool:
    """Whether adding `target` on top of the other valors would exceed 100%."""
    return _targets_sum(others) + target > 100 + TARGET_SHARE_SUM_TOLERANCE


def _settings_schema(
    name: str | None = None,
    currency: str = DEFAULT_BASE_CURRENCY,
    interval: int = DEFAULT_SCAN_INTERVAL,
    invested: float | None = None,
    precision: int = DEFAULT_REBALANCE_PRECISION,
    cash: float | None = None,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WALLET_NAME, default=name): str,
            vol.Required(CONF_BASE_CURRENCY, default=currency): _CURRENCY_SELECTOR,
            vol.Required(CONF_SCAN_INTERVAL, default=interval): _INTERVAL_SELECTOR,
            vol.Required(
                CONF_REBALANCE_PRECISION, default=precision
            ): _REBALANCE_PRECISION_SELECTOR,
            # Suggested value (not default): an empty optional field is then omitted
            # from the submitted data instead of being sent as null, which the
            # number selector would reject with "expected float".
            vol.Optional(
                CONF_INVESTED_AMOUNT, description={"suggested_value": invested}
            ): vol.Any(None, _AMOUNT_SELECTOR),
            # Same pattern: an empty field disables cash tracking entirely.
            vol.Optional(
                CONF_CASH_AMOUNT, description={"suggested_value": cash}
            ): vol.Any(None, _AMOUNT_SELECTOR),
        }
    )


class MyWalletConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial creation of a wallet."""

    VERSION = 1

    def __init__(self) -> None:
        self._valors: list[dict[str, Any]] = []
        self._name: str | None = None
        self._currency: str = DEFAULT_BASE_CURRENCY
        self._interval: int = DEFAULT_SCAN_INTERVAL
        self._invested: float | None = None
        self._precision: int = DEFAULT_REBALANCE_PRECISION
        self._cash: float | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1: basic wallet settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_WALLET_NAME].strip()
            if not name:
                errors[CONF_WALLET_NAME] = "invalid_name"
            else:
                self._name = name
                self._currency = user_input[CONF_BASE_CURRENCY]
                self._interval = user_input[CONF_SCAN_INTERVAL]
                self._invested = _normalize_invested(user_input.get(CONF_INVESTED_AMOUNT))
                self._precision = _normalize_precision(
                    user_input.get(CONF_REBALANCE_PRECISION)
                )
                self._cash = _normalize_cash(user_input.get(CONF_CASH_AMOUNT))
                return await self.async_step_valor()
        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(
                self._name, self._currency, self._interval, self._invested, self._precision, self._cash
            ),
            errors=errors,
        )

    async def async_step_valor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 2..n: add valors one by one until the user stops."""
        errors: dict[str, str] = {}
        # Re-submit the entered values when the form is shown again after an error.
        schema = _valor_schema(
            user_input.get(VALOR_SYMBOL) if user_input else None,
            float(user_input[VALOR_AMOUNT]) if user_input else None,
            user_input.get(VALOR_TARGET_SHARE) if user_input else None,
        ).extend({vol.Optional("add_another", default=True): bool})
        if user_input is not None:
            symbol = user_input[VALOR_SYMBOL].strip().upper()
            amount = float(user_input[VALOR_AMOUNT])
            target = _normalize_target_share(user_input.get(VALOR_TARGET_SHARE))
            if not symbol:
                errors[VALOR_SYMBOL] = "invalid_symbol"
            elif any(v[VALOR_SYMBOL] == symbol for v in self._valors):
                errors[VALOR_SYMBOL] = "symbol_exists"
            elif target is not None and _target_sum_exceeded(self._valors, target):
                errors[VALOR_TARGET_SHARE] = "target_sum_exceeded"
            else:
                valor: dict[str, Any] = {VALOR_SYMBOL: symbol, VALOR_AMOUNT: amount}
                if target is not None:
                    valor[VALOR_TARGET_SHARE] = target
                self._valors.append(valor)
                if not user_input.get("add_another"):
                    return self._create_entry()
                # Re-show an empty form for the next valor.
                return self.async_show_form(
                    step_id="valor",
                    data_schema=_valor_schema().extend(
                        {vol.Optional("add_another", default=True): bool}
                    ),
                    errors=errors,
                )
        return self.async_show_form(step_id="valor", data_schema=schema, errors=errors)

    def _create_entry(self) -> FlowResult:
        data: dict[str, Any] = {
            CONF_WALLET_NAME: self._name,
            CONF_BASE_CURRENCY: self._currency,
            CONF_SCAN_INTERVAL: self._interval,
            CONF_REBALANCE_PRECISION: self._precision,
            CONF_VALORS: self._valors,
        }
        if self._invested is not None:
            data[CONF_INVESTED_AMOUNT] = self._invested
        if self._cash is not None:
            data[CONF_CASH_AMOUNT] = self._cash
        return self.async_create_entry(title=self._name or "Wallet", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MyWalletOptionsFlow:
        return MyWalletOptionsFlow(config_entry)


class MyWalletOptionsFlow(config_entries.OptionsFlowWithConfigEntry):
    """Manage an existing wallet: settings, add / edit / remove valors."""

    _edit_symbol: str | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Show the management menu."""
        self._edit_symbol = None
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "settings",
                "add_valor",
                "edit_valor",
                "remove_valor",
                "adjust_invested",
                "adjust_cash",
                "trade",
            ],
        )

    async def async_step_adjust_invested(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Adjust the invested amount (deposit / withdraw / set)."""
        errors: dict[str, str] = {}
        current = self.config_entry.data.get(CONF_INVESTED_AMOUNT)
        current = float(current) if current is not None else 0.0
        if user_input is not None:
            operation: str = user_input[ATTR_OPERATION]
            amount = float(user_input[ATTR_AMOUNT])
            if operation == OPERATION_SET:
                new_value = amount
            elif operation == OPERATION_DEPOSIT:
                new_value = current + amount
            else:  # OPERATION_WITHDRAW
                new_value = current - amount
                if new_value < 0:
                    errors[ATTR_AMOUNT] = "invested_negative"
            if not errors:
                return await self._save(
                    self._valors(),
                    **{CONF_INVESTED_AMOUNT: round(new_value, 2)},
                )
        return self.async_show_form(
            step_id="adjust_invested",
            data_schema=_operation_schema(current),
            errors=errors,
            description_placeholders={"current": f"{current:g}"},
        )

    async def async_step_adjust_cash(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Adjust the tracked free cash (deposit / withdraw / set)."""
        errors: dict[str, str] = {}
        current = self.config_entry.data.get(CONF_CASH_AMOUNT)
        current = float(current) if current is not None else 0.0
        if user_input is not None:
            operation: str = user_input[ATTR_OPERATION]
            amount = float(user_input[ATTR_AMOUNT])
            if operation == OPERATION_SET:
                new_value = amount
            elif operation == OPERATION_DEPOSIT:
                new_value = current + amount
            else:  # OPERATION_WITHDRAW
                new_value = current - amount
                if new_value < 0:
                    errors[ATTR_AMOUNT] = "cash_negative"
            if not errors:
                return await self._save(
                    self._valors(),
                    **{CONF_CASH_AMOUNT: round(new_value, 2)},
                )
        return self.async_show_form(
            step_id="adjust_cash",
            data_schema=_cash_operation_schema(current),
            errors=errors,
            description_placeholders={"current": f"{current:g}"},
        )

    async def async_step_trade(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Buy or sell a valor; the free cash follows the trade."""
        errors: dict[str, str] = {}
        valors = self._valors()
        if not valors:
            return self.async_abort(reason="no_valors")
        coordinator: WalletCoordinator | None = getattr(
            self.config_entry, "runtime_data", None
        )
        if user_input is not None:
            symbol: str = user_input[ATTR_SYMBOL]
            operation: str = user_input[ATTR_OPERATION]
            quantity = float(user_input[ATTR_QUANTITY])
            precision = _normalize_precision(
                self.config_entry.data.get(CONF_REBALANCE_PRECISION)
            )
            quantity = round(quantity, precision)
            valor_entry = next(
                (v for v in valors if v[VALOR_SYMBOL] == symbol), None
            )
            held = float(valor_entry[VALOR_AMOUNT]) if valor_entry else 0.0
            if quantity <= 0:
                errors[ATTR_QUANTITY] = "quantity_too_small"
            elif operation == OPERATION_SELL and quantity > held:
                errors[ATTR_QUANTITY] = "not_enough_units"
            else:
                new_amount = (
                    held + quantity if operation == OPERATION_BUY else held - quantity
                )
                new_valors = [
                    dict(v, **{VALOR_AMOUNT: new_amount})
                    if v[VALOR_SYMBOL] == symbol
                    else v
                    for v in valors
                ]
                extra: dict[str, Any] = {CONF_VALORS: new_valors}
                cash = self.config_entry.data.get(CONF_CASH_AMOUNT)
                unit_price = user_input.get(ATTR_UNIT_PRICE)
                if cash is not None:
                    if unit_price is not None:
                        amount_cash = round(quantity * float(unit_price), 2)
                    else:
                        valor_data = (
                            coordinator.data.valors.get(symbol)
                            if coordinator is not None and coordinator.data
                            else None
                        )
                        if (
                            valor_data is None
                            or valor_data.quote is None
                            or not valor_data.quote.price
                            or valor_data.fx_rate is None
                        ):
                            errors[ATTR_UNIT_PRICE] = "unit_price_required"
                            amount_cash = 0.0
                        else:
                            amount_cash = round(
                                quantity
                                * float(valor_data.quote.price)
                                * float(valor_data.fx_rate),
                                2,
                            )
                    if not errors:
                        new_cash = (
                            float(cash) - amount_cash
                            if operation == OPERATION_BUY
                            else float(cash) + amount_cash
                        )
                        if new_cash < 0:
                            errors[ATTR_AMOUNT] = "cash_negative"
                        else:
                            extra[CONF_CASH_AMOUNT] = round(new_cash, 2)
                if not errors:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={**self.config_entry.data, **extra},
                    )
                    if coordinator is not None:
                        self.hass.async_create_task(
                            coordinator.async_request_refresh()
                        )
                    return self.async_create_entry(title="", data={})
        current_cash = self.config_entry.data.get(CONF_CASH_AMOUNT)
        return self.async_show_form(
            step_id="trade",
            data_schema=_trade_schema(valors),
            errors=errors,
            description_placeholders={
                "cash": f"{float(current_cash):g}" if current_cash is not None else "—"
            },
        )

    def _valors(self) -> list[dict[str, Any]]:
        return list(self.config_entry.data.get(CONF_VALORS, []))

    async def _save(self, valors: list[dict[str, Any]], **extra: Any) -> FlowResult:
        data = dict(self.config_entry.data)
        data[CONF_VALORS] = valors
        data.update(extra)
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
        return self.async_create_entry(title="", data={})

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_WALLET_NAME].strip()
            if not name:
                errors[CONF_WALLET_NAME] = "invalid_name"
            else:
                return await self._save(
                    self._valors(),
                    **{
                        CONF_WALLET_NAME: name,
                        CONF_BASE_CURRENCY: user_input[CONF_BASE_CURRENCY],
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                        CONF_REBALANCE_PRECISION: _normalize_precision(
                            user_input.get(CONF_REBALANCE_PRECISION)
                        ),
                        # None clears a previously set amount and disables profit tracking.
                        CONF_INVESTED_AMOUNT: _normalize_invested(
                            user_input.get(CONF_INVESTED_AMOUNT)
                        ),
                        # None clears a previously set amount and disables cash tracking.
                        CONF_CASH_AMOUNT: _normalize_cash(
                            user_input.get(CONF_CASH_AMOUNT)
                        ),
                    },
                )
        data = self.config_entry.data
        return self.async_show_form(
            step_id="settings",
            data_schema=_settings_schema(
                data.get(CONF_WALLET_NAME, self.config_entry.title),
                data.get(CONF_BASE_CURRENCY, DEFAULT_BASE_CURRENCY),
                data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                data.get(CONF_INVESTED_AMOUNT),
                data.get(CONF_REBALANCE_PRECISION, DEFAULT_REBALANCE_PRECISION),
                data.get(CONF_CASH_AMOUNT),
            ),
            errors=errors,
        )

    async def async_step_add_valor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        valors = self._valors()
        schema = _valor_schema(
            user_input.get(VALOR_SYMBOL) if user_input else None,
            float(user_input[VALOR_AMOUNT]) if user_input else None,
            user_input.get(VALOR_TARGET_SHARE) if user_input else None,
        )
        if user_input is not None:
            symbol = user_input[VALOR_SYMBOL].strip().upper()
            if not symbol:
                errors[VALOR_SYMBOL] = "invalid_symbol"
            elif any(v[VALOR_SYMBOL] == symbol for v in valors):
                errors[VALOR_SYMBOL] = "symbol_exists"
            else:
                target = _normalize_target_share(user_input.get(VALOR_TARGET_SHARE))
                if target is not None and _target_sum_exceeded(valors, target):
                    errors[VALOR_TARGET_SHARE] = "target_sum_exceeded"
                else:
                    valor: dict[str, Any] = {
                        VALOR_SYMBOL: symbol,
                        VALOR_AMOUNT: float(user_input[VALOR_AMOUNT]),
                    }
                    if target is not None:
                        valor[VALOR_TARGET_SHARE] = target
                    valors.append(valor)
                    return await self._save(valors)
        return self.async_show_form(step_id="add_valor", data_schema=schema, errors=errors)

    async def async_step_remove_valor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        valors = self._valors()
        if not valors:
            return self.async_abort(reason="no_valors")
        if user_input is not None:
            symbol = user_input[VALOR_SYMBOL]
            return await self._save([v for v in valors if v[VALOR_SYMBOL] != symbol])
        return self.async_show_form(
            step_id="remove_valor",
            data_schema=vol.Schema(
                {
                    vol.Required(VALOR_SYMBOL): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[v[VALOR_SYMBOL] for v in valors],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_valor(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Ask which valor to edit, then delegate to the fields step."""
        valors = self._valors()
        if not valors:
            return self.async_abort(reason="no_valors")
        if user_input is not None:
            self._edit_symbol = user_input[VALOR_SYMBOL]
            current = next(v for v in valors if v[VALOR_SYMBOL] == self._edit_symbol)
            return self.async_show_form(
                step_id="edit_valor_fields",
                data_schema=vol.Schema(
                    {
                        vol.Required(VALOR_AMOUNT, default=current[VALOR_AMOUNT]): _AMOUNT_SELECTOR,
                        # Clearing the field removes the target share.
                        vol.Optional(
                            VALOR_TARGET_SHARE,
                            description={"suggested_value": current.get(VALOR_TARGET_SHARE)},
                        ): vol.Any(None, _TARGET_SHARE_SELECTOR),
                    }
                ),
                description_placeholders={VALOR_SYMBOL: self._edit_symbol},
            )
        return self.async_show_form(
            step_id="edit_valor",
            data_schema=vol.Schema(
                {
                    vol.Required(VALOR_SYMBOL): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[v[VALOR_SYMBOL] for v in valors],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_edit_valor_fields(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Apply the new amount / target share for the previously selected valor."""
        symbol = self._edit_symbol
        valors = self._valors()
        target = _normalize_target_share(user_input.get(VALOR_TARGET_SHARE))
        others = (v for v in valors if v[VALOR_SYMBOL] != symbol)
        if target is not None and _target_sum_exceeded(others, target):
            return self.async_show_form(
                step_id="edit_valor_fields",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            VALOR_AMOUNT, default=float(user_input[VALOR_AMOUNT])
                        ): _AMOUNT_SELECTOR,
                        vol.Optional(
                            VALOR_TARGET_SHARE,
                            description={"suggested_value": user_input.get(VALOR_TARGET_SHARE)},
                        ): vol.Any(None, _TARGET_SHARE_SELECTOR),
                    }
                ),
                description_placeholders={VALOR_SYMBOL: symbol},
                errors={VALOR_TARGET_SHARE: "target_sum_exceeded"},
            )
        new_valors = []
        for v in valors:
            if v[VALOR_SYMBOL] != symbol:
                new_valors.append(v)
                continue
            item = dict(v)  # preserve keys we do not edit
            item[VALOR_AMOUNT] = float(user_input[VALOR_AMOUNT])
            if target is not None:
                item[VALOR_TARGET_SHARE] = target
            else:
                item.pop(VALOR_TARGET_SHARE, None)
            new_valors.append(item)
        return await self._save(new_valors)
