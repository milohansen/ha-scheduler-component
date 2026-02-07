"""Calendar helper utilities for scheduler integration."""

import logging
import re
from datetime import datetime, time
from typing import Iterable

import homeassistant.util.dt as dt_util
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.components.calendar import SERVICE_GET_EVENTS
from homeassistant.components.calendar.const import (
    DOMAIN as CALENDAR_DOMAIN,
    EVENT_START_DATETIME,
    EVENT_END_DATETIME,
)

_LOGGER = logging.getLogger(__name__)


def _parse_event_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = dt_util.parse_datetime(value)
    if dt is not None:
        return dt_util.as_local(dt)
    d = dt_util.parse_date(value)
    if d is not None:
        return dt_util.as_local(datetime.combine(d, time.min))
    return None


async def async_get_calendar_events(
    hass,
    entity_id: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Fetch calendar events for an entity within a range."""
    response = await hass.services.async_call(
        CALENDAR_DOMAIN,
        SERVICE_GET_EVENTS,
        {
            ATTR_ENTITY_ID: entity_id,
            EVENT_START_DATETIME: start,
            EVENT_END_DATETIME: end,
        },
        blocking=True,
        return_response=True,
    )
    if not response:
        return []
    return response.get("events", [])


async def async_collect_events(
    hass,
    entity_ids: Iterable[str],
    start: datetime,
    end: datetime,
    match: str | None = None,
) -> list[tuple[datetime, datetime, str | None]]:
    """Collect and normalize calendar events across entities."""
    events: list[tuple[datetime, datetime, str | None]] = []

    matcher = None
    if match:
        try:
            matcher = re.compile(match, re.IGNORECASE)
        except re.error:
            _LOGGER.warning("Invalid calendar_match regex: %s", match)
            matcher = None

    for entity_id in entity_ids:
        try:
            items = await async_get_calendar_events(hass, entity_id, start, end)
        except Exception as err:
            _LOGGER.warning("Failed to fetch events for %s: %s", entity_id, err)
            continue

        for item in items:
            summary = item.get("summary")
            description = item.get("description")
            location = item.get("location")
            match_text = " ".join(
                [t for t in [summary, description, location] if t]
            ).strip()

            if matcher and match_text:
                if matcher.search(match_text) is None:
                    continue

            start_dt = _parse_event_time(item.get("start"))
            end_dt = _parse_event_time(item.get("end"))
            if start_dt is None or end_dt is None:
                continue
            if end_dt <= start_dt:
                continue
            events.append((start_dt, end_dt, summary))

    events.sort(key=lambda e: e[0])
    return events
