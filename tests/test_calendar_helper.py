"""Tests for calendar helper."""
import pytest
from datetime import timedelta

import homeassistant.util.dt as dt_util

from custom_components.scheduler.calendar_helper import _parse_event_time
from custom_components.scheduler.calendar_helper import async_collect_events


def test_parse_event_time_datetime():
    now = dt_util.utcnow()
    result = _parse_event_time(now.isoformat())
    assert result is not None


def test_parse_event_time_date():
    today = dt_util.now().date().isoformat()
    result = _parse_event_time(today)
    assert result is not None


def test_parse_event_time_invalid():
    assert _parse_event_time("invalid") is None


@pytest.mark.asyncio
async def test_calendar_match_regex(hass, monkeypatch):
    async def _fake_get_events(hass, entity_id, start, end):
        return [
            {"summary": "Home Assistant", "start": start.isoformat(), "end": end.isoformat()},
            {"summary": "Other", "start": start.isoformat(), "end": end.isoformat()},
        ]

    monkeypatch.setattr(
        "custom_components.scheduler.calendar_helper.async_get_calendar_events",
        _fake_get_events,
    )

    events = await async_collect_events(
        hass,
        ["calendar.test"],
        dt_util.now(),
        dt_util.now() + timedelta(days=1),
        match="home",
    )

    assert len(events) == 1
