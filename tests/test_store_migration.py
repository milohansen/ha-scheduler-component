"""Tests for storage migration and error tracking."""
import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, patch

from custom_components.scheduler.store import (
    ScheduleStorage,
    ScheduleEntry,
    STORAGE_VERSION,
)
from custom_components.scheduler import const


class TestStorageMigration:
    """Test storage schema version migration."""

    @pytest.mark.asyncio
    async def test_migrate_v3_to_v4(self, hass):
        """Test migration from v3 to v4 adds new fields."""
        storage = ScheduleStorage(hass)
        
        # Mock v3 data
        v3_data = {
            "schedules": [
                {
                    const.ATTR_SCHEDULE_ID: "abc123",
                    const.ATTR_WEEKDAYS: ["monday"],
                    const.ATTR_START_DATE: None,
                    const.ATTR_END_DATE: None,
                    const.ATTR_TIMESLOTS: [],
                    const.ATTR_REPEAT_TYPE: "repeat",
                    "name": "Test Schedule",
                    const.ATTR_ENABLED: True,
                }
            ],
            "tags": [],
        }
        
        # Migrate
        v4_data = storage._migrate_to_v4(v3_data)
        
        # Check version updated
        assert v4_data["version"] == 4
        
        # Check new fields added
        schedule = v4_data["schedules"][0]
        assert "timer_type" in schedule
        assert schedule["timer_type"] == "calendar"
        assert "created_at" in schedule
        assert "updated_at" in schedule
        assert "last_run" in schedule
        assert "last_error" in schedule
        assert "execution_count" in schedule
        assert schedule["execution_count"] == 0
        assert "failure_count" in schedule
        assert schedule["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_load_handles_missing_v4_fields(self, hass):
        """Test loading data without v4 fields uses defaults."""
        storage = ScheduleStorage(hass)
        
        # Mock load to return v3-style data
        with patch.object(storage._store, 'async_load', return_value={
            "schedules": [{
                const.ATTR_SCHEDULE_ID: "test123",
                const.ATTR_WEEKDAYS: ["daily"],
                const.ATTR_START_DATE: None,
                const.ATTR_END_DATE: None,
                const.ATTR_TIMESLOTS: [],
                const.ATTR_REPEAT_TYPE: "repeat",
                "name": "Test",
                const.ATTR_ENABLED: True,
            }],
            "tags": [],
        }):
            await storage.async_load()
        
        # Check schedule loaded with defaults
        schedule = storage.schedules["test123"]
        assert schedule.timer_type == "calendar"
        assert schedule.created_at is not None
        assert schedule.updated_at is not None
        assert schedule.last_run is None
        assert schedule.last_error is None
        assert schedule.execution_count == 0
        assert schedule.failure_count == 0


class TestScheduleErrorTracking:
    """Test error tracking functionality."""

    @pytest.mark.asyncio
    async def test_create_schedule_sets_timestamps(self, hass):
        """Test creating a schedule sets created_at and updated_at."""
        storage = ScheduleStorage(hass)
        
        schedule_data = {
            const.ATTR_WEEKDAYS: ["monday"],
            const.ATTR_TIMESLOTS: [],
            const.ATTR_REPEAT_TYPE: "repeat",
            "name": "Test Schedule",
        }
        
        schedule = storage.async_create_schedule(schedule_data)
        
        assert schedule is not None
        assert schedule.created_at is not None
        assert schedule.updated_at is not None
        assert schedule.timer_type == "calendar"
        assert schedule.execution_count == 0
        assert schedule.failure_count == 0

    @pytest.mark.asyncio
    async def test_update_schedule_updates_timestamp(self, hass):
        """Test updating a schedule updates updated_at timestamp."""
        storage = ScheduleStorage(hass)
        
        # Create initial schedule
        schedule_data = {
            const.ATTR_WEEKDAYS: ["monday"],
            const.ATTR_TIMESLOTS: [],
            const.ATTR_REPEAT_TYPE: "repeat",
        }
        schedule = storage.async_create_schedule(schedule_data)
        original_updated_at = schedule.updated_at
        
        # Wait a moment (in real scenario, would be later)
        import time
        time.sleep(0.01)
        
        # Update schedule
        updated = storage.async_update_schedule(
            schedule.schedule_id,
            {"name": "Updated Name"}
        )
        
        # Check updated_at changed
        assert updated.updated_at != original_updated_at
        assert updated.name == "Updated Name"

    @pytest.mark.asyncio
    async def test_save_includes_v4_fields(self, hass):
        """Test saving schedules includes v4 fields."""
        storage = ScheduleStorage(hass)
        
        # Create schedule with error tracking
        schedule_data = {
            const.ATTR_WEEKDAYS: ["monday"],
            const.ATTR_TIMESLOTS: [],
            const.ATTR_REPEAT_TYPE: "repeat",
            "timer_type": "transient",
            "execution_count": 5,
            "failure_count": 1,
            "last_error": "Test error",
        }
        storage.async_create_schedule(schedule_data)
        
        # Get save data
        save_data = storage._data_to_save()
        
        # Check version
        assert save_data["version"] == STORAGE_VERSION
        
        # Check schedule has v4 fields
        saved_schedule = save_data["schedules"][0]
        assert "timer_type" in saved_schedule
        assert saved_schedule["timer_type"] == "transient"
        assert "execution_count" in saved_schedule
        assert saved_schedule["execution_count"] == 5
        assert "failure_count" in saved_schedule
        assert saved_schedule["failure_count"] == 1
        assert "last_error" in saved_schedule
        assert saved_schedule["last_error"] == "Test error"


class TestSchedulerLogger:
    """Test structured logging wrapper."""

    def test_logger_formats_with_schedule_id(self):
        """Test logger adds schedule_id to messages."""
        from custom_components.scheduler.const import get_logger
        
        logger = get_logger(__name__)
        
        # Test formatting
        formatted = logger._format_message("Test message", schedule_id="abc123")
        assert "[abc123]" in formatted
        assert "Test message" in formatted

    def test_logger_formats_with_kwargs(self):
        """Test logger adds extra context from kwargs."""
        from custom_components.scheduler.const import get_logger
        
        logger = get_logger(__name__)
        
        formatted = logger._format_message(
            "Test message",
            schedule_id="abc123",
            entity_id="light.kitchen",
            slot=2
        )
        assert "[abc123]" in formatted
        assert "entity_id=light.kitchen" in formatted
        assert "slot=2" in formatted

    def test_logger_without_schedule_id(self):
        """Test logger works without schedule_id."""
        from custom_components.scheduler.const import get_logger
        
        logger = get_logger(__name__)
        
        formatted = logger._format_message("Test message")
        assert "[" not in formatted  # No schedule_id bracket
        assert "Test message" in formatted


# Fixtures
@pytest.fixture
def hass():
    """Mock Home Assistant instance."""
    from homeassistant.helpers.storage import Store
    
    mock_hass = Mock()
    mock_hass.data = {}
    
    # Mock the store
    mock_store = Mock(spec=Store)
    mock_store.async_load = AsyncMock(return_value=None)
    mock_store.async_save = AsyncMock()
    mock_store.async_delay_save = Mock()
    
    with patch('custom_components.scheduler.store.Store', return_value=mock_store):
        yield mock_hass
