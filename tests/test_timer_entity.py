"""Tests for SchedulerTimerEntity."""
from datetime import timedelta

import pytest
import homeassistant.util.dt as dt_util
from homeassistant.components.timer import STATUS_ACTIVE, STATUS_IDLE, STATUS_PAUSED

from custom_components.scheduler.timer import SchedulerTimerEntity
from custom_components.scheduler.store import ScheduleEntry
from custom_components.scheduler import const


class DummyCoordinator:
    def __init__(self):
        self.id = "test"


class DummyActionHandler:
    def __init__(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def patch_action_handler(monkeypatch):
    monkeypatch.setattr(
        "custom_components.scheduler.base_entity.ActionHandler", DummyActionHandler
    )


def _make_schedule(**overrides):
    now = dt_util.utcnow().isoformat()
    base = {
        "schedule_id": "abc",
        "weekdays": [],
        "start_date": None,
        "end_date": None,
        "timeslots": [{const.ATTR_ACTIONS: []}],
        "repeat_type": const.REPEAT_TYPE_SINGLE,
        "name": "Test Timer",
        "enabled": True,
        "timer_type": const.TIMER_TYPE_TRANSIENT,
        "created_at": now,
        "updated_at": now,
        "last_run": None,
        "last_error": None,
        "execution_count": 0,
        "failure_count": 0,
        "duration": 60,
        "started_at": now,
        "persistent": False,
    }
    base.update(overrides)
    return ScheduleEntry(**base)


def test_timer_entity_state_active(hass):
    entity = SchedulerTimerEntity(DummyCoordinator(), hass, "abc", "timer.abc")
    schedule = _make_schedule(started_at=(dt_util.utcnow() - timedelta(seconds=10)).isoformat())
    entity.schedule = schedule

    assert entity.state == STATUS_ACTIVE


def test_timer_entity_state_paused(hass):
    entity = SchedulerTimerEntity(DummyCoordinator(), hass, "abc", "timer.abc")
    schedule = _make_schedule(enabled=False)
    entity.schedule = schedule

    assert entity.state == STATUS_PAUSED


def test_timer_entity_state_idle_when_expired(hass):
    entity = SchedulerTimerEntity(DummyCoordinator(), hass, "abc", "timer.abc")
    schedule = _make_schedule(started_at=(dt_util.utcnow() - timedelta(seconds=120)).isoformat())
    entity.schedule = schedule

    assert entity.state == STATUS_IDLE


def test_timer_entity_extra_state_attributes(hass):
    entity = SchedulerTimerEntity(DummyCoordinator(), hass, "abc", "timer.abc")
    schedule = _make_schedule()
    entity.schedule = schedule

    attrs = entity.extra_state_attributes
    assert attrs["entity_type"] == "timer"
