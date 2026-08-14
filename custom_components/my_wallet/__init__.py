"""The My Wallet integration: portfolios valued via Yahoo Finance."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import WalletCoordinator

_LOGGER = logging.getLogger(__name__)

# Type alias for config entries of this integration.
type MyWalletConfigEntry = ConfigEntry[WalletCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MyWalletConfigEntry) -> bool:
    """Set up a wallet from a config entry."""
    coordinator = WalletCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MyWalletConfigEntry) -> bool:
    """Unload a wallet."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_entry(hass: HomeAssistant, entry: MyWalletConfigEntry) -> None:
    """Reload the entry when its data (settings or valors) change."""
    await hass.config_entries.async_reload(entry.entry_id)
