"""Initialization of Scheduler switch platform."""

import logging
from typing import Any
from propcache import cached_property
import voluptuous as vol

import homeassistant.util.dt as dt_util
from homeassistant.components.switch.const import DOMAIN as PLATFORM
from homeassistant.helpers import entity_platform, config_validation as cv
from homeassistant.const import (
    STATE_OFF,
    ATTR_ENTITY_ID,
    ATTR_TIME,
    EntityCategory,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import ToggleEntity
from homeassistant.util import slugify
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
)
from . import const
from .store import ScheduleEntry
from .base_entity import BaseSchedulerEntity

_LOGGER = logging.getLogger(__name__)


SERVICE_RUN_ACTION = "run_action"
RUN_ACTION_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_TIME): cv.time,
        vol.Optional(const.ATTR_SKIP_CONDITIONS): cv.boolean,
    }
)


async def async_setup_entry(hass, _config_entry, async_add_entities):
    """Set up the Scheduler switch devices."""

    coordinator = hass.data[const.DOMAIN]["coordinator"]

    @callback
    def async_add_entity(schedule: ScheduleEntry):
        """Add switch for Scheduler."""
        if schedule.timer_type != const.TIMER_TYPE_CALENDAR:
            return

        schedule_id = schedule.schedule_id
        name = schedule.name

        if name and len(slugify(name)):
            entity_id = "{}.schedule_{}".format(PLATFORM, slugify(name))
        else:
            entity_id = "{}.schedule_{}".format(PLATFORM, schedule_id)

        entity = ScheduleEntity(coordinator, hass, schedule_id, entity_id)
        hass.data[const.DOMAIN]["schedules"][schedule_id] = entity
        async_add_entities([entity])

    for entry in coordinator.store.schedules.values():
        async_add_entity(entry)

    async_dispatcher_connect(hass, const.EVENT_ITEM_CREATED, async_add_entity)

    platform = entity_platform.current_platform.get()
    if platform is not None:
        platform.async_register_entity_service(
            SERVICE_RUN_ACTION, RUN_ACTION_SCHEMA, "async_service_run_action"
        )
    else:
        _LOGGER.error(f"Couldn't register service {SERVICE_RUN_ACTION}, platform not found")


class ScheduleEntity(BaseSchedulerEntity, ToggleEntity):
    """Defines a Scheduler switch entity."""

    def __init__(self, coordinator, hass, schedule_id: str, entity_id: str) -> None:
        """Initialize the schedule entity."""
        super().__init__(coordinator, hass, schedule_id)
        self.entity_id = entity_id

    def _update_state_from_schedule(self):
        if self.schedule.enabled and self._state in [STATE_OFF, const.STATE_COMPLETED]:
            from homeassistant.const import STATE_ON
            self._state = STATE_ON
        elif not self.schedule.enabled and self._state not in [STATE_OFF, const.STATE_COMPLETED]:
            self._state = STATE_OFF

    async def async_turn_off_schedule(self):
        await self.async_turn_off()

    @cached_property
    def icon(self):
        """Return icon."""
        return "mdi:calendar-clock"

    @cached_property
    def entity_category(self):
        """Return EntityCategory."""
        return EntityCategory.CONFIG

    @property
    def is_on(self):
        """Return true if entity is on."""
        from homeassistant.const import STATE_ON
        return self._state not in [STATE_OFF, const.STATE_COMPLETED]

    async def async_turn_off(self, **kwargs: Any):
        """turn off a schedule"""
        if self.schedule is not None and self.schedule.enabled:
            await self._action_handler.async_empty_queue()
            self.coordinator.async_edit_schedule(
                self.schedule_id, {const.ATTR_ENABLED: False}
            )

    async def async_turn_on(self, **kwargs: Any):
        """turn on a schedule"""
        if self.schedule is not None and not self.schedule.enabled:
            self.coordinator.async_edit_schedule(
                self.schedule_id, {const.ATTR_ENABLED: True}
            )

    async def async_service_run_action(self, time=None, skip_conditions=False):
        """Manually trigger the execution of the actions of a timeslot"""
        now = dt_util.as_local(dt_util.utcnow())
        if time is not None:
            now = now.replace(hour=time.hour, minute=time.minute, second=time.second)

        (slot, ts) = self._timer_handler.current_timeslot(now)

        if (
            slot is None
            and time is None
            and self.schedule is not None
            and len(self.schedule.timeslots) == 1
        ):
            slot = 0

        if slot is None:
            self._logger.info(f"Has no active timeslot at {now.strftime('%H:%M:%S')}")
            return

        schedule_slot = self.schedule.timeslots[slot]

        if skip_conditions:
            # Create a shallow copy and clear conditions
            from dataclasses import replace
            schedule_slot = replace(schedule_slot, conditions=[])

        self._logger.debug(f"Executing actions, timeslot {slot}, skip_conditions {skip_conditions}")
        await self._action_handler.async_queue_actions(schedule_slot)
