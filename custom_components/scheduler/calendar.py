import logging
import datetime
from typing import List, Optional

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import homeassistant.util.dt as dt_util

from . import const

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Scheduler calendar platform."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    async_add_entities([SchedulerCalendarEntity(coordinator)])

class SchedulerCalendarEntity(CalendarEntity):
    """Defines a Scheduler calendar entity."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_name = "Scheduler Timeline"
        self._attr_unique_id = f"{coordinator.id}_calendar"

    @property
    def event(self) -> Optional[CalendarEvent]:
        """Return the next upcoming event."""
        events = self._get_upcoming_events()
        return events[0] if events else None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date_time: datetime.datetime,
        end_date_time: datetime.datetime
    ) -> List[CalendarEvent]:
        """Return calendar events within a datetime range."""
        events = self._get_upcoming_events()
        return [
            event for event in events
            if start_date_time <= event.start < end_date_time
        ]

    def _get_upcoming_events(self) -> List[CalendarEvent]:
        """Generate calendar events from schedules."""
        events = []
        for schedule_id, entity in self.coordinator.hass.data[const.DOMAIN]["schedules"].items():
            if not entity.schedule or not entity.schedule.enabled:
                continue

            # Use timestamps from entities (they already calculate next occurrences)
            if entity._timestamps:
                for ts_str in entity._timestamps:
                    ts = dt_util.parse_datetime(ts_str)
                    if ts:
                        events.append(
                            CalendarEvent(
                                start=ts,
                                end=ts + datetime.timedelta(minutes=1),
                                summary=entity.name,
                                description=f"Schedule ID: {schedule_id}",
                            )
                        )

        events.sort(key=lambda x: x.start)
        return events

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, const.EVENT_TIMER_UPDATED, self.async_write_ha_state
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, const.EVENT_ITEM_UPDATED, self.async_write_ha_state
            )
        )
