import logging
import datetime
from typing import Any

from homeassistant.components.timer import (
    TimerEntity,
    TimerState,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
)
import homeassistant.util.dt as dt_util

from . import const
from .base_entity import BaseSchedulerEntity
from .store import ScheduleEntry

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Scheduler timer platform."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]

    @callback
    def async_add_entity(schedule: ScheduleEntry):
        """Add timer for Scheduler."""
        if schedule.timer_type != const.TIMER_TYPE_TRANSIENT:
            return

        schedule_id = schedule.schedule_id
        entity_id = f"timer.schedule_{schedule_id}"

        entity = SchedulerTimerEntity(coordinator, hass, schedule_id, entity_id)
        hass.data[const.DOMAIN]["schedules"][schedule_id] = entity
        async_add_entities([entity])

    for entry in coordinator.store.schedules.values():
        async_add_entity(entry)

    async_dispatcher_connect(hass, const.EVENT_ITEM_CREATED, async_add_entity)

class SchedulerTimerEntity(BaseSchedulerEntity, TimerEntity):
    """Defines a Scheduler timer entity."""

    def __init__(self, coordinator, hass, schedule_id: str, entity_id: str) -> None:
        """Initialize the timer entity."""
        super().__init__(coordinator, hass, schedule_id)
        self.entity_id = entity_id

    @property
    def state(self):
        """Return the state of the entity."""
        if not self._timer_handler or not self._timer_handler._next_trigger:
            return TimerState.IDLE

        now = dt_util.as_local(dt_util.utcnow())
        if self._timer_handler._next_trigger <= now:
            return TimerState.IDLE

        return TimerState.ACTIVE

    @property
    def remaining(self) -> datetime.timedelta | None:
        """Return the remaining time."""
        if not self._timer_handler or not self._timer_handler._next_trigger:
            return None

        now = dt_util.as_local(dt_util.utcnow())
        remaining = self._timer_handler._next_trigger - now
        if remaining.total_seconds() <= 0:
            return datetime.timedelta(0)
        return remaining

    def _update_state_from_schedule(self):
        """Update internal state."""
        pass

    async def async_turn_off_schedule(self):
        """Turn off schedule."""
        self.coordinator.async_delete_schedule(self.schedule_id)
