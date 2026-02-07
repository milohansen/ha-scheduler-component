import logging
import datetime
from typing import Any
from dataclasses import asdict
import copy

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
import homeassistant.util.dt as dt_util
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    ATTR_ENTITY_ID,
)
from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState

from . import const
from .store import ScheduleEntry, async_get_registry
from .timer import TimerHandler
from .actions import ActionHandler

_LOGGER = logging.getLogger(__name__)

def date_in_future(date_string: str):
    now = dt_util.as_local(dt_util.utcnow())
    date = dt_util.parse_date(date_string)
    if date is None:
        return False
    diff = date - now.date()
    return diff.days > 0

class BaseSchedulerEntity(Entity):
    """Base class for Scheduler entities."""

    def __init__(self, coordinator, hass, schedule_id: str) -> None:
        """Initialize the schedule entity."""
        self.coordinator = coordinator
        self.hass = hass
        self.schedule_id = schedule_id
        self.schedule = None
        self._logger = const.SchedulerLogger(_LOGGER, schedule_id)

        self._state = None
        self._timer = None
        self._timestamps = []
        self._next_entries = []
        self._current_slot = None
        self._init = True
        self._tags = []

        self._listeners = [
            async_dispatcher_connect(
                self.hass, const.EVENT_ITEM_UPDATED, self.async_item_updated
            ),
            async_dispatcher_connect(
                self.hass, const.EVENT_TIMER_UPDATED, self.async_timer_updated
            ),
            async_dispatcher_connect(
                self.hass, const.EVENT_TIMER_FINISHED, self.async_timer_finished
            ),
        ]

    @property
    def device_info(self) -> DeviceInfo:
        """Return info for device registry."""
        device = self.coordinator.id
        return {
            "identifiers": {(const.DOMAIN, device)},
            "name": "Scheduler",
            "model": "Scheduler",
            "sw_version": const.VERSION,
            "manufacturer": "@nielsfaber",
        }

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        if self.schedule and self.schedule.name:
            return self.schedule.name
        else:
            return f"Schedule #{self.schedule_id}"

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self.schedule_id}"

    @property
    def weekdays(self):
        return self.schedule.weekdays if self.schedule else None

    @property
    def entities(self):
        entities = []
        if not self.schedule:
            return
        for timeslot in self.schedule.timeslots:
            for action in timeslot.actions:
                if action.entity_id and action.entity_id not in entities:
                    entities.append(action.entity_id)
        return entities

    @property
    def actions(self):
        if not self.schedule:
            return
        return [
            (
                {
                    "service": timeslot.actions[0].service,
                }
                if not timeslot.actions[0].service_data
                else {
                    "service": timeslot.actions[0].service,
                    "service_data": timeslot.actions[0].service_data,
                }
            )
            for timeslot in self.schedule.timeslots
        ]

    @property
    def timeslots(self):
        timeslots = []
        if not self.schedule:
            return
        for timeslot in self.schedule.timeslots:
            if timeslot.stop:
                timeslots.append(
                    f"{timeslot.start} - {timeslot.stop}"
                )
            else:
                timeslots.append(timeslot.start)
        return timeslots

    @property
    def tags(self):
        return self._tags

    @property
    def extra_state_attributes(self):
        """Return the data of the entity."""
        output = {
            "weekdays": self.weekdays,
            "timeslots": self.timeslots,
            "entities": self.entities,
            "actions": self.actions,
            "current_slot": self._current_slot,
            "next_slot": self._next_entries[0] if len(self._next_entries) else None,
            "next_trigger": (
                self._timestamps[self._next_entries[0]]
                if len(self._next_entries)
                else None
            ),
            "tags": self.tags,
        }

        if self.schedule:
            output.update({
                "created_at": self.schedule.created_at,
                "updated_at": self.schedule.updated_at,
                "timer_type": self.schedule.timer_type,
                "last_run": self.schedule.last_run,
                "last_error": self.schedule.last_error,
                "execution_count": self.schedule.execution_count,
                "failure_count": self.schedule.failure_count,
            })

        return output

    @callback
    async def async_item_updated(self, id: str):
        """update internal properties when schedule config was changed"""
        if id != self.schedule_id:
            return

        store = await async_get_registry(self.hass)
        self.schedule = store.async_get_schedule(self.schedule_id)
        self._tags = self.coordinator.async_get_tags_for_schedule(self.schedule_id)

        if self.schedule is None:
            raise ValueError(f"Schedule with id {self.schedule_id} not found in store")

        self._update_state_from_schedule()
        self._init = True

        if self.hass is None:
            return

        self.async_write_ha_state()

    def _update_state_from_schedule(self):
        """Update internal state based on schedule enabled status."""
        pass

    @callback
    async def async_timer_updated(self, id: str):
        """update internal properties when schedule timer was changed"""
        if id != self.schedule_id:
            return
        if self.schedule is None:
            return

        self._next_entries = self._timer_handler.slot_queue
        self._timestamps = list(
            map(
                lambda x: datetime.datetime.isoformat(x), self._timer_handler.timestamps
            )
        )

        if self._current_slot is not None and self._timer_handler.current_slot is None:
            # we are leaving a timeslot, stop execution of actions
            if (
                len(self.schedule.timeslots) == 1
                and self.schedule.repeat_type == const.REPEAT_TYPE_REPEAT
            ):
                await self._action_handler.async_empty_queue(restore_time=9)
            else:
                await self._action_handler.async_empty_queue()

            if self._current_slot == (
                len(self.schedule.timeslots) - 1
            ) and (
                not self.schedule.end_date
                or not date_in_future(self.schedule.end_date)
            ):
                # last timeslot has ended
                if self.schedule.repeat_type == const.REPEAT_TYPE_PAUSE:
                    self._logger.debug("Has finished the last timeslot, turning off")
                    await self.async_turn_off_schedule()
                    self._state = const.STATE_COMPLETED

                elif self.schedule.repeat_type == const.REPEAT_TYPE_SINGLE:
                    self._logger.debug("Has finished the last timeslot, removing")
                    self.coordinator.async_delete_schedule(self.schedule_id)

        self._current_slot = self._timer_handler.current_slot

        if self._state not in [STATE_OFF, AlarmControlPanelState.TRIGGERED]:
            if len(self._next_entries) < 1:
                self._state = STATE_UNAVAILABLE
            else:
                now = dt_util.as_local(dt_util.utcnow())
                if ((self._timer_handler._next_trigger or now) - now).total_seconds() < 0:
                    self._state = const.STATE_COMPLETED
                else:
                    self._state = (
                        STATE_ON if self.schedule.enabled else STATE_OFF
                    )

        if self._init:
            # initial startpoint for timer calculated, fire actions if currently overlapping with timeslot
            if self._current_slot is not None and self._state != STATE_OFF:
                skip_initial_execution = False
                if (
                    self.coordinator.state == const.STATE_INIT
                    and self.coordinator.time_shutdown
                ):
                    ts_shutdown = self.coordinator.time_shutdown
                    now = dt_util.as_local(dt_util.utcnow())
                    start_time = self.schedule.timeslots[self._current_slot].start
                    start_of_timeslot = self._timer_handler.calculate_timestamp(
                        start_time, ts_shutdown
                    )
                    if start_of_timeslot is not None and start_of_timeslot > now:
                        skip_initial_execution = True

                if skip_initial_execution:
                    self._logger.debug("Was already executed before shutdown, initial timeslot is skipped.")
                else:
                    self._logger.debug("Is starting in a timeslot, proceed with actions")
                await self._action_handler.async_queue_actions(
                    self.schedule.timeslots[self._current_slot],
                    skip_initial_execution,
                )
            self._init = False

        if self.hass is None:
            return

        self.async_write_ha_state()

    async def async_turn_off_schedule(self):
        """To be implemented by platforms to turn off the schedule."""
        pass

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        store = await async_get_registry(self.hass)
        self.schedule = store.async_get_schedule(self.schedule_id)
        self._tags = self.coordinator.async_get_tags_for_schedule(self.schedule_id)

        self._timer_handler = TimerHandler(self.hass, self.schedule_id)
        self._action_handler = ActionHandler(self.hass, self.schedule_id)

    async def async_will_remove_from_hass(self):
        """remove entity from hass."""
        self._logger.debug("Is removed from hass")

        if self._timer:
            self._timer()
            self._timer = None
        await self._action_handler.async_empty_queue()
        await self._timer_handler.async_unload()

        while len(self._listeners):
            self._listeners.pop()()

    @callback
    async def async_timer_finished(self, id: str):
        """fire actions when timer is finished"""
        if id != self.schedule_id:
            return

        if self._state not in [STATE_OFF, const.STATE_COMPLETED] and self.schedule is not None:
            self._current_slot = self._timer_handler.current_slot if self.schedule.timer_type == const.TIMER_TYPE_CALENDAR else 0
            if self._current_slot is not None:
                self._logger.debug("Is triggered, proceed with actions")
                await self._action_handler.async_queue_actions(
                    self.schedule.timeslots[self._current_slot]
                )

        @callback
        async def async_trigger_finished(_now):
            """internal timer is finished, reset the schedule"""
            if self._state == AlarmControlPanelState.TRIGGERED:
                self._state = STATE_ON

            if self.schedule and self.schedule.timer_type == const.TIMER_TYPE_TRANSIENT:
                self._logger.debug("Transient timer finished, removing schedule")
                self.coordinator.async_delete_schedule(self.schedule_id)
                return

            await self._timer_handler.async_start_timer()

        from homeassistant.helpers.event import async_call_later
        self._timer = async_call_later(self.hass, 60, async_trigger_finished)
        if self._state == STATE_ON:
            self._state = AlarmControlPanelState.TRIGGERED

        self.async_write_ha_state()

    @callback
    def async_get_entity_state(self):
        """fetch schedule data for websocket API"""
        data = copy.copy(asdict(self.schedule) if self.schedule else {})
        if not data:
            data = {}
        data.update(
            {
                "next_entries": self._next_entries,
                "timestamps": self._timestamps,
                "name": self.schedule.name if self.schedule else "",
                "entity_id": self.entity_id,
                "tags": self.tags,
            }
        )
        return data
