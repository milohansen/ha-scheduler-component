"""Binary sensors for Scheduler integration."""
import logging
from propcache import cached_property

from homeassistant.core import callback
from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import DeviceInfo

from . import const

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, _config_entry, async_add_entities):
    """Set up Scheduler binary sensors."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]

    entity = SchedulerHealthBinarySensor(coordinator, hass)
    async_add_entities([entity])


class SchedulerHealthBinarySensor(BinarySensorEntity):
    """Overall health indicator for the Scheduler integration."""

    def __init__(self, coordinator, hass) -> None:
        self.coordinator = coordinator
        self.hass = hass
        self._attr_name = "Scheduler Health"
        self._attr_unique_id = f"{const.DOMAIN}_health"
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM
        self._attr_entity_category = "diagnostic"
        self._listeners = []

    async def async_added_to_hass(self) -> None:
        """Register listeners when entity is added."""
        self._listeners.append(
            async_dispatcher_connect(
                self.hass, const.EVENT_ITEM_UPDATED, self._handle_update
            )
        )
        self._listeners.append(
            async_dispatcher_connect(
                self.hass, const.EVENT_ITEM_CREATED, self._handle_update
            )
        )
        self._listeners.append(
            async_dispatcher_connect(
                self.hass, const.EVENT_ITEM_REMOVED, self._handle_update
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up listeners when entity is removed."""
        while self._listeners:
            self._listeners.pop()()

    @callback
    def _handle_update(self, *_args):
        """Handle updates from dispatcher."""
        self.async_write_ha_state()

    @cached_property
    def is_on(self):
        """Return True if any schedule has an error."""
        for schedule in self.coordinator.store.schedules.values():
            if schedule.last_error:
                return True
        return False

    @cached_property
    def extra_state_attributes(self):
        """Return additional attributes."""
        error_schedules = [
            schedule.schedule_id
            for schedule in self.coordinator.store.schedules.values()
            if schedule.last_error
        ]
        return {
            "error_count": len(error_schedules),
            "error_schedules": error_schedules,
        }

    @cached_property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(const.DOMAIN, self.coordinator.id)},
            name="Scheduler",
            manufacturer="@nielsfaber",
            model="Scheduler",
            sw_version=const.VERSION,
        )
