"""Calendar entity showing upcoming scheduler triggers."""

import logging
from datetime import timedelta

import homeassistant.util.dt as dt_util
from homeassistant.components.calendar import CalendarEntity, CalendarEvent

from . import const

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, _config_entry, async_add_entities):
    """Set up Scheduler calendar entity."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    async_add_entities([SchedulerTimelineCalendar(coordinator, hass)])


class SchedulerTimelineCalendar(CalendarEntity):
    """Calendar view for scheduler triggers."""

    def __init__(self, coordinator, hass):
        self.coordinator = coordinator
        self.hass = hass
        self._attr_name = "Scheduler Timeline"
        self._attr_unique_id = f"{const.DOMAIN}_timeline"

    @property
    def event(self):
        return None

    async def async_get_events(self, hass, start_date, end_date):
        """Return calendar events within a date range."""
        events: list[CalendarEvent] = []
        schedules = self.coordinator.store.schedules.values()
        for schedule in schedules:
            name = schedule.name or "Scheduler"

            # transient timers: derive trigger from started_at + duration
            if schedule.timer_type == const.TIMER_TYPE_TRANSIENT:
                if schedule.started_at and schedule.duration:
                    started = dt_util.parse_datetime(schedule.started_at)
                    if started is None:
                        continue
                    trigger = dt_util.as_local(started) + timedelta(
                        seconds=schedule.duration
                    )
                    if start_date <= trigger <= end_date:
                        events.append(
                            CalendarEvent(
                                summary=name,
                                start=trigger,
                                end=trigger + timedelta(minutes=1),
                            )
                        )
                continue

            # calendar schedules use stored timestamps from entity state when available
            entity = self.hass.data[const.DOMAIN]["schedules"].get(schedule.schedule_id)
            timestamps = []
            if entity is not None:
                state = entity.async_get_entity_state()
                timestamps = state.get("timestamps") or []

            for ts in timestamps:
                dt = dt_util.parse_datetime(ts)
                if dt is None:
                    continue
                dt = dt_util.as_local(dt)
                if dt < start_date or dt > end_date:
                    continue
                events.append(
                    CalendarEvent(
                        summary=name,
                        start=dt,
                        end=dt + timedelta(minutes=1),
                    )
                )
        return events
