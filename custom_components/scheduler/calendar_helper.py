import logging
import datetime
from typing import List, Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.calendar import (
    CalendarEvent,
    async_get_events,
)
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

class CalendarHelper:
    """Helper to interact with Home Assistant calendars."""

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    async def get_events_for_entity(
        self,
        entity_id: str,
        start_date_time: datetime.datetime,
        end_date_time: datetime.datetime
    ) -> List[CalendarEvent]:
        """Fetch events from a specific calendar entity."""
        try:
            events = await async_get_events(
                self.hass, entity_id, start_date_time, end_date_time
            )
            return events.get(entity_id, [])
        except Exception as e:
            _LOGGER.error(f"Error fetching events from {entity_id}: {e}")
            return []

    def match_event(self, event: CalendarEvent, pattern: Optional[str] = None) -> bool:
        """Check if an event matches a given title pattern (regex)."""
        if not pattern:
            return True
        import re
        try:
            return bool(re.search(pattern, event.summary, re.IGNORECASE))
        except re.error:
            _LOGGER.warning(f"Invalid regex pattern for calendar matching: {pattern}")
            return False

    async def find_next_trigger(
        self,
        entity_ids: List[str],
        pattern: Optional[str] = None
    ) -> Optional[datetime.datetime]:
        """Find the next start time of a matching event across multiple calendars."""
        now = dt_util.now()
        end = now + datetime.timedelta(days=30) # Look ahead 30 days

        next_trigger = None

        for entity_id in entity_ids:
            events = await self.get_events_for_entity(entity_id, now, end)
            for event in events:
                if self.match_event(event, pattern):
                    event_start = event.start
                    if isinstance(event_start, datetime.date) and not isinstance(event_start, datetime.datetime):
                        # All-day event
                        event_start = dt_util.start_of_local_day(event_start)

                    if event_start > now:
                        if next_trigger is None or event_start < next_trigger:
                            next_trigger = event_start

        return next_trigger
