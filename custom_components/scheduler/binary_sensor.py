from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from . import const

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Scheduler binary_sensor platform."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    async_add_entities([SchedulerHealthSensor(coordinator)])

class SchedulerHealthSensor(BinarySensorEntity):
    """Defines a Scheduler health sensor."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_name = "Scheduler Health"
        self._attr_unique_id = f"{coordinator.id}_health"
        self._attr_device_class = BinarySensorDeviceClass.HEALTH

    @property
    def is_on(self):
        """Return true if the binary sensor is on (healthy)."""
        for schedule in self.coordinator.store.schedules.values():
            if schedule.failure_count > 0:
                return False
        return True

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, const.EVENT_ITEM_UPDATED, self.async_write_ha_state
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, "scheduler_action_failed", self.async_write_ha_state
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, "scheduler_action_succeeded", self.async_write_ha_state
            )
        )
