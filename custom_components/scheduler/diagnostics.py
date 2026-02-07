from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from . import const

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]

    schedules = coordinator.async_get_schedules()
    tags = coordinator.async_get_tags()

    return {
        "entry": entry.as_dict(),
        "coordinator_state": coordinator.state,
        "schedules_count": len(schedules),
        "schedules": schedules,
        "tags": tags,
    }
