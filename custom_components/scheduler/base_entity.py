"""Base schedule entity for shared logic."""

import logging
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
)

from . import const
from .store import ScheduleEntry, async_get_registry
from .actions import ActionHandler
from .timer import TimerHandler, CountdownTimerHandler

_LOGGER = logging.getLogger(__name__)


class BaseScheduleEntity:
    """Shared logic for scheduler entities."""

    schedule: ScheduleEntry
    _timer_handler: TimerHandler | CountdownTimerHandler

    def __init__(self, coordinator, hass, schedule_id: str) -> None:
        self.coordinator = coordinator
        self.hass = hass
        self.schedule_id = schedule_id
        self._tags = []
        self._action_handler = ActionHandler(self.hass, self.schedule_id)

    def _create_timer_handler(self):
        """Create a timer handler based on schedule type."""
        if self.schedule and self.schedule.timer_type == const.TIMER_TYPE_TRANSIENT:
            return CountdownTimerHandler(self.hass, self.schedule_id)
        return TimerHandler(self.hass, self.schedule_id)

    async def async_load_schedule(self) -> None:
        """Load schedule from storage and refresh handlers."""
        store = await async_get_registry(self.hass)
        sched = store.async_get_schedule(self.schedule_id)
        if sched is None:
            raise ValueError(f"Schedule with id {self.schedule_id} not found in store")
        self.schedule = sched
        self._tags = self.coordinator.async_get_tags_for_schedule(self.schedule_id)

        await self._ensure_timer_handler()

    async def _ensure_timer_handler(self) -> None:
        """Create or recreate timer handler if schedule type changed."""

        if self._timer_handler is None:
            self._timer_handler = self._create_timer_handler()
            return

        expected_handler = (
            CountdownTimerHandler
            if self.schedule.timer_type == const.TIMER_TYPE_TRANSIENT
            else TimerHandler
        )
        if not isinstance(self._timer_handler, expected_handler):
            try:
                await self._timer_handler.async_unload()  # type: ignore
            except Exception as _:
                pass
            self._timer_handler = self._create_timer_handler()

    async def _track_execution_success(self):
        """Track successful action execution."""
        from datetime import datetime, timezone

        store = await async_get_registry(self.hass)
        now = datetime.now(timezone.utc).isoformat()

        updates = {
            "last_run": now,
            "last_error": None,
            "execution_count": self.schedule.execution_count + 1,
        }

        history = list(self.schedule.execution_history or [])
        history.append(
            {
                "timestamp": now,
                "status": "success",
            }
        )
        updates[const.ATTR_EXECUTION_HISTORY] = history[-const.EXECUTION_HISTORY_MAX :]

        self.schedule = store.async_update_schedule(self.schedule_id, updates)
        async_dispatcher_send(self.hass, const.EVENT_ITEM_UPDATED, self.schedule_id)
        _LOGGER.debug("Schedule %s executed successfully", self.schedule_id)

    async def _track_execution_failure(self, error_message: str):
        """Track failed action execution."""
        from datetime import datetime, timezone

        store = await async_get_registry(self.hass)
        now = datetime.now(timezone.utc).isoformat()

        updates = {
            "last_run": now,
            "last_error": error_message,
            "execution_count": self.schedule.execution_count + 1,
            "failure_count": self.schedule.failure_count + 1,
        }

        history = list(self.schedule.execution_history or [])
        history.append(
            {
                "timestamp": now,
                "status": "failure",
                "error": error_message,
            }
        )
        updates[const.ATTR_EXECUTION_HISTORY] = history[-const.EXECUTION_HISTORY_MAX :]

        self.schedule = store.async_update_schedule(self.schedule_id, updates)
        async_dispatcher_send(self.hass, const.EVENT_ITEM_UPDATED, self.schedule_id)

        async_create_notification(
            self.hass,
            f"Schedule '{self.schedule.name or self.schedule_id}' failed: {error_message}",
            title="Scheduler Action Failed",
            notification_id=f"scheduler_error_{self.schedule_id}",
        )
        _LOGGER.error(
            "Schedule %s execution failed: %s", self.schedule_id, error_message
        )
