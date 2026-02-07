import logging
import secrets
from collections import OrderedDict
from typing import MutableMapping, cast
from dataclasses import dataclass, field, asdict, replace

from homeassistant.core import callback, HomeAssistant
from homeassistant.loader import bind_hass
from homeassistant.const import (
    ATTR_NAME,
    CONF_CONDITIONS,
)
from homeassistant.helpers.storage import Store
from . import const

_LOGGER = logging.getLogger(__name__)

DATA_REGISTRY = f"{const.DOMAIN}_storage"
STORAGE_KEY = f"{const.DOMAIN}.storage"
STORAGE_VERSION = 4
SAVE_DELAY = 10


@dataclass(frozen=True, slots=True)
class ActionEntry:
    """Action storage Entry."""

    service: str = ""
    entity_id: str | None = None
    service_data: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConditionEntry:
    """Condition storage Entry."""

    entity_id: str | None = None
    attribute: str | None = None
    value: str | None = None
    match_type: str | None = None

@dataclass(frozen=True, slots=True)
class TimeslotEntry:
    """Timeslot storage Entry."""

    start: str | None = None
    stop: str | None = None
    conditions: list = field(default_factory=list)
    condition_type: str | None = None
    track_conditions: bool = False
    actions: list = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    """Schedule storage Entry."""

    schedule_id: str
    weekdays: list = field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None
    timeslots: list = field(default_factory=list)
    repeat_type: str | None = None
    name: str | None = None
    enabled: bool = True
    # Schema v4 additions
    timer_type: str = "calendar"  # "calendar" or "transient"
    created_at: str | None = None
    updated_at: str | None = None
    last_run: str | None = None
    last_error: str | None = None
    execution_count: int = 0
    failure_count: int = 0
    duration: int | None = None
    started_at: str | None = None
    persistent: bool | None = None

    def __getitem__(self, item):
        return getattr(self, item)


@dataclass(frozen=True, slots=True)
class TagEntry:
    """Tag storage Entry."""

    name: str | None = None
    schedules: list = field(default_factory=list)


def parse_schedule_data(data: dict):
    if const.ATTR_TIMESLOTS in data:
        timeslots = []
        for item in data[const.ATTR_TIMESLOTS]:
            timeslot = TimeslotEntry(**item)
            if CONF_CONDITIONS in item and item[CONF_CONDITIONS]:
                conditions = []
                for condition in item[CONF_CONDITIONS]:
                    conditions.append(ConditionEntry(**condition))
                timeslot = replace(timeslot, **{CONF_CONDITIONS: conditions})
            if const.ATTR_ACTIONS in item and item[const.ATTR_ACTIONS]:
                actions = []
                for action in item[const.ATTR_ACTIONS]:
                    actions.append(ActionEntry(**action))
                timeslot = replace(timeslot, **{const.ATTR_ACTIONS: actions})
            timeslots.append(timeslot)
        data[const.ATTR_TIMESLOTS] = timeslots
    return data



class ScheduleStorage:
    """Class to hold scheduler data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the storage."""
        self.hass = hass
        self.schedules: MutableMapping[str, ScheduleEntry] = {}
        self.tags: MutableMapping[str, TagEntry] = {}
        self.time_shutdown: str | None = None
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True)

    async def async_load(self) -> None:
        """Load the registry of schedule entries."""
        data = await self._store.async_load()
        schedules: "OrderedDict[str, ScheduleEntry]" = OrderedDict()
        tags: "OrderedDict[str, TagEntry]" = OrderedDict()

        if data is not None:
            # Handle migration from older versions
            data_version = data.get("version", 3)
            if data_version < 4:
                data = self._migrate_to_v4(data)
                _LOGGER.info("Migrated scheduler storage from version %d to 4", data_version)

            if "schedules" in data:
                for entry in data["schedules"]:
                    entry = parse_schedule_data(entry)
                    schedules[entry[const.ATTR_SCHEDULE_ID]] = ScheduleEntry(
                        schedule_id=entry[const.ATTR_SCHEDULE_ID],
                        weekdays=entry[const.ATTR_WEEKDAYS],
                        start_date=entry[const.ATTR_START_DATE],
                        end_date=entry[const.ATTR_END_DATE],
                        timeslots=entry[const.ATTR_TIMESLOTS],
                        repeat_type=entry[const.ATTR_REPEAT_TYPE],
                        name=entry[ATTR_NAME],
                        enabled=entry[const.ATTR_ENABLED],
                        # Schema v4 fields with defaults for backwards compatibility
                        timer_type=entry.get("timer_type", const.TIMER_TYPE_CALENDAR),
                        created_at=entry.get("created_at"),
                        updated_at=entry.get("updated_at"),
                        last_run=entry.get("last_run"),
                        last_error=entry.get("last_error"),
                        execution_count=entry.get("execution_count", 0),
                        failure_count=entry.get("failure_count", 0),
                        duration=entry.get("duration"),
                        started_at=entry.get("started_at"),
                        persistent=entry.get("persistent"),
                    )

            if "tags" in data:
                for entry in data["tags"]:
                    tags[entry[ATTR_NAME]] = TagEntry(
                        name=entry[ATTR_NAME],
                        schedules=entry[const.ATTR_SCHEDULES],
                    )

            if "time_shutdown" in data:
                self.time_shutdown = data["time_shutdown"]

        self.schedules = schedules
        self.tags = tags

    def _migrate_to_v4(self, data: dict) -> dict:
        """Migrate storage data from v3 to v4.
        
        Adds timer_type, created_at, updated_at, and error tracking fields
        to existing schedules with default values.
        """
        if "schedules" in data:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            
            for entry in data["schedules"]:
                # Add v4 fields with defaults
                entry.setdefault("timer_type", const.TIMER_TYPE_CALENDAR)
                entry.setdefault("created_at", now)
                entry.setdefault("updated_at", now)
                entry.setdefault("last_run", None)
                entry.setdefault("last_error", None)
                entry.setdefault("execution_count", 0)
                entry.setdefault("failure_count", 0)
                entry.setdefault("duration", None)
                entry.setdefault("started_at", None)
                entry.setdefault("persistent", None)
        
        data["version"] = 4
        return data

    @callback
    def async_schedule_save(self) -> None:
        """Schedule saving the registry of schedules."""
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    async def async_save(self) -> None:
        """Save the registry of schedules."""
        await self._store.async_save(self._data_to_save())

    @callback
    def _data_to_save(self) -> dict:
        """Return data for the registry for schedules to store in a file."""
        store_data: dict = {"version": STORAGE_VERSION}

        store_data["schedules"] = []
        store_data["tags"] = []

        for entry in self.schedules.values():
            if (
                entry.timer_type == const.TIMER_TYPE_TRANSIENT
                and entry.persistent is False
            ):
                continue
            item = {
                const.ATTR_SCHEDULE_ID: entry.schedule_id,
                const.ATTR_TIMESLOTS: [],
                const.ATTR_WEEKDAYS: entry.weekdays,
                const.ATTR_START_DATE: entry.start_date,
                const.ATTR_END_DATE: entry.end_date,
                const.ATTR_REPEAT_TYPE: entry.repeat_type,
                ATTR_NAME: entry.name,
                const.ATTR_ENABLED: entry.enabled,
                # Schema v4 fields
                "timer_type": entry.timer_type,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "last_run": entry.last_run,
                "last_error": entry.last_error,
                "execution_count": entry.execution_count,
                "failure_count": entry.failure_count,
                "duration": entry.duration,
                "started_at": entry.started_at,
                "persistent": entry.persistent,
            }
            for slot in entry.timeslots:
                timeslot = {
                    const.ATTR_START: slot.start,
                    const.ATTR_STOP: slot.stop,
                    CONF_CONDITIONS: [],
                    const.ATTR_CONDITION_TYPE: slot.condition_type,
                    const.ATTR_TRACK_CONDITIONS: slot.track_conditions,
                    const.ATTR_ACTIONS: [],
                }
                if slot.conditions:
                    for condition in slot.conditions:
                        timeslot[CONF_CONDITIONS].append(asdict(condition))
                if slot.actions:
                    for action in slot.actions:
                        timeslot[const.ATTR_ACTIONS].append(asdict(action))
                item[const.ATTR_TIMESLOTS].append(timeslot)
            store_data["schedules"].append(item)

        store_data["tags"] = [asdict(entry) for entry in self.tags.values()]

        if self.time_shutdown:
            store_data["time_shutdown"] = self.time_shutdown

        return store_data

    async def async_delete(self):
        """Delete config."""
        _LOGGER.warning("Removing scheduler configuration data!")
        self.schedules = {}
        self.tags = {}
        await self._store.async_remove()

    @callback
    def async_get_schedule(self, entity_id) -> ScheduleEntry | None:
        """Get an existing ScheduleEntry by id."""
        res = self.schedules.get(entity_id)
        return res if res else None

    @callback
    def async_get_schedules(self) -> dict:
        """Get an existing ScheduleEntry by id."""
        res = {}
        for (key, val) in self.schedules.items():
            res[key] = asdict(val)
        return res

    @callback
    def async_create_schedule(self, data: dict) -> ScheduleEntry | None:
        """Create a new ScheduleEntry."""
        from datetime import datetime, timezone
        
        if const.ATTR_SCHEDULE_ID in data:
            schedule_id = data[const.ATTR_SCHEDULE_ID]
            del data[const.ATTR_SCHEDULE_ID]
            if schedule_id in self.schedules:
                return None
        else:
            schedule_id = secrets.token_hex(3)
            while schedule_id in self.schedules:
                schedule_id = secrets.token_hex(3)

        # Set timestamps for new schedules
        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        data.setdefault("timer_type", const.TIMER_TYPE_CALENDAR)

        if data.get("timer_type") == const.TIMER_TYPE_TRANSIENT:
            data.setdefault("started_at", now)
            duration = data.get("duration")
            if duration is not None and data.get("persistent") is None:
                data["persistent"] = duration >= 300

        data = parse_schedule_data(data)
        new_schedule = ScheduleEntry(**data, schedule_id=schedule_id)
        self.schedules[schedule_id] = new_schedule
        self.async_schedule_save()
        return new_schedule

    @callback
    def async_delete_schedule(self, schedule_id: str) -> bool:
        """Delete ScheduleEntry."""
        if schedule_id in self.schedules:
            del self.schedules[schedule_id]
            self.async_schedule_save()
            return True
        return False

    @callback
    def async_update_schedule(self, schedule_id: str, changes: dict) -> ScheduleEntry:
        """Update existing ScheduleEntry."""
        from datetime import datetime, timezone
        
        old = self.schedules[schedule_id]
        changes = parse_schedule_data(changes)
        
        # Update the updated_at timestamp
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        new = self.schedules[schedule_id] = replace(old, **changes)
        self.async_schedule_save()
        return new

    @callback
    def async_get_tag(self, name: str) -> dict | None:
        """Get an existing TagEntry by id."""
        res = self.tags.get(name)
        return asdict(res) if res else None

    @callback
    def async_get_tags(self) -> dict:
        """Get an existing TagEntry by id."""
        res = {}
        for (key, val) in self.tags.items():
            res[key] = asdict(val)
        return res

    @callback
    def async_create_tag(self, data: dict) -> TagEntry | None:
        """Create a new TagEntry."""
        name = data[ATTR_NAME] if ATTR_NAME in data else None
        if not name or name in data:
            return None

        new_tag = TagEntry(**data)
        self.tags[name] = new_tag
        self.async_schedule_save()
        return new_tag

    @callback
    def async_delete_tag(self, name: str) -> bool:
        """Delete TagEntry."""
        if name in self.tags:
            del self.tags[name]
            self.async_schedule_save()
            return True
        return False

    @callback
    def async_update_tag(self, name: str, changes: dict) -> TagEntry:
        """Update existing TagEntry."""
        old = self.tags[name]
        changes = parse_schedule_data(changes)
        new = self.tags[name] = replace(old, **changes)
        self.async_schedule_save()
        return new

    @callback
    def async_get_time_shutdown(self) -> str | None:
        """Get the shutdown time and clear the stored value afterwards."""
        res = self.time_shutdown
        self.time_shutdown = None
        self.async_schedule_save()
        return res

    @callback
    async def async_set_time_shutdown(self, value: str):
        """Set the shutdown time and store it immediately."""
        self.time_shutdown = value
        await self.async_save()

@bind_hass
async def async_get_registry(hass: HomeAssistant) -> ScheduleStorage:
    """Return alarmo storage instance."""
    task = hass.data.get(DATA_REGISTRY)

    if task is None:

        async def _load_reg() -> ScheduleStorage:
            registry = ScheduleStorage(hass)
            await registry.async_load()
            return registry

        task = hass.data[DATA_REGISTRY] = hass.async_create_task(_load_reg())

    return cast(ScheduleStorage, await task)
