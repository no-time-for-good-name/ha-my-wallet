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
    COMMON_CURRENCIES,
    CONF_BASE_CURRENCY,
    CONF_INVESTED_AMOUNT,
    CONF_SCAN_INTERVAL,
    CONF_VALORS,
    CONF_WALLET_NAME,
    DEFAULT_BASE_CURRENCY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TARGET_SHARE_SUM_TOLERANCE,
    VALOR_AMOUNT,
    VALOR_SYMBOL,
    VALOR_TARGET_SHARE,
)

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
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WALLET_NAME, default=name): str,
            vol.Required(CONF_BASE_CURRENCY, default=currency): _CURRENCY_SELECTOR,
            vol.Required(CONF_SCAN_INTERVAL, default=interval): _INTERVAL_SELECTOR,
            # Suggested value (not default): an empty optional field is then omitted
            # from the submitted data instead of being sent as null, which the
            # number selector would reject with "expected float".
            vol.Optional(
                CONF_INVESTED_AMOUNT, description={"suggested_value": invested}
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
                return await self.async_step_valor()
        return self.async_show_form(
            step_id="user",
            data_schema=_settings_schema(
                self._name, self._currency, self._interval, self._invested
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
            CONF_VALORS: self._valors,
        }
        if self._invested is not None:
            data[CONF_INVESTED_AMOUNT] = self._invested
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
        return self.async_show_menu(step_id="init", menu_options=["settings", "add_valor", "remove_valor", "edit_valor"])

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
                        # None clears a previously set amount and disables profit tracking.
                        CONF_INVESTED_AMOUNT: _normalize_invested(
                            user_input.get(CONF_INVESTED_AMOUNT)
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
