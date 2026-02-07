"""Store constants."""
import logging
import voluptuous as vol
import re
import homeassistant.util.dt as dt_util
from homeassistant.helpers import config_validation as cv
from homeassistant.const import (
    WEEKDAYS,
    ATTR_ENTITY_ID,
    SUN_EVENT_SUNRISE,
    SUN_EVENT_SUNSET,
    ATTR_SERVICE,
    ATTR_SERVICE_DATA,
    CONF_CONDITIONS,
    CONF_ATTRIBUTE,
    ATTR_NAME,
)

VERSION = "2026.2.0"

DOMAIN = "scheduler"

SUN_ENTITY = "sun.sun"

DAY_TYPE_DAILY = "daily"
DAY_TYPE_WORKDAY = "workday"
DAY_TYPE_WEEKEND = "weekend"

WORKDAY_ENTITY = "binary_sensor.workday_sensor"

ATTR_SKIP_CONDITIONS = "skip_conditions"
ATTR_CONDITION_TYPE = "condition_type"
CONDITION_TYPE_AND = "and"
CONDITION_TYPE_OR = "or"

ATTR_MATCH_TYPE = "match_type"
MATCH_TYPE_EQUAL = "is"
MATCH_TYPE_UNEQUAL = "not"
MATCH_TYPE_BELOW = "below"
MATCH_TYPE_ABOVE = "above"

ATTR_TRACK_CONDITIONS = "track_conditions"

ATTR_REPEAT_TYPE = "repeat_type"
REPEAT_TYPE_REPEAT = "repeat"
REPEAT_TYPE_SINGLE = "single"
REPEAT_TYPE_PAUSE = "pause"

ATTR_TIMER_TYPE = "timer_type"
TIMER_TYPE_CALENDAR = "calendar"
TIMER_TYPE_TRANSIENT = "transient"
ATTR_DURATION = "duration"
ATTR_STARTED_AT = "started_at"
ATTR_PERSISTENT = "persistent"

EVENT = "scheduler_updated"

SERVICE_REMOVE = "remove"
SERVICE_EDIT = "edit"
SERVICE_ADD = "add"
SERVICE_COPY = "copy"
SERVICE_DISABLE_ALL = "disable_all"
SERVICE_ENABLE_ALL = "enable_all"
SERVICE_VALIDATE = "validate"
SERVICE_RUN_IN = "run_in"

OffsetTimePattern = re.compile(r"^([a-z]+)([-|\+]{1})([0-9:]+)$")
DatePattern = re.compile(r"^[0-9]+\-[0-9]+\-[0-9]+$")

ATTR_START = "start"
ATTR_STOP = "stop"
ATTR_TIMESLOTS = "timeslots"
ATTR_WEEKDAYS = "weekdays"
ATTR_ENABLED = "enabled"
ATTR_SCHEDULE_ID = "schedule_id"
ATTR_ACTIONS = "actions"
ATTR_VALUE = "value"
ATTR_TAGS = "tags"
ATTR_SCHEDULES = "schedules"
ATTR_START_DATE = "start_date"
ATTR_END_DATE = "end_date"

EVENT_TIMER_FINISHED = "scheduler_timer_finished"
EVENT_TIMER_UPDATED = "scheduler_timer_updated"
EVENT_ITEM_UPDATED = "scheduler_item_updated"
EVENT_ITEM_CREATED = "scheduler_item_created"
EVENT_ITEM_REMOVED = "scheduler_item_removed"
EVENT_STARTED = "scheduler_started"
EVENT_WORKDAY_SENSOR_UPDATED = "workday_sensor_updated"
EVENT_ACTION_SUCCEEDED = "scheduler_action_succeeded"
EVENT_ACTION_FAILED = "scheduler_action_failed"

STATE_INIT = "init"
STATE_READY = "ready"
STATE_COMPLETED = "completed"

ACTION_RETRY_MAX = 3
ACTION_RETRY_BASE_DELAY = 5


def validate_time(time):
    res = OffsetTimePattern.match(time)
    if not res:
        if dt_util.parse_time(time):
            return time
        else:
            raise vol.Invalid("Invalid time entered: {}".format(time))
    else:
        if res.group(1) not in [SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET]:
            raise vol.Invalid("Invalid time entered: {}".format(time))
        elif res.group(2) not in ["+", "-"]:
            raise vol.Invalid("Invalid time entered: {}".format(time))
        elif not dt_util.parse_time(res.group(3)):
            raise vol.Invalid("Invalid time entered: {}".format(time))
        else:
            return time


def validate_date(value: str) -> str:
    """Input must be either none or a valid date."""
    if value is None:
        return None
    date = dt_util.parse_date(value)
    if date is None:
        raise vol.Invalid("Invalid date entered: {}".format(value))
    else:
        return date.strftime("%Y-%m-%d")


CONDITION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_VALUE): vol.Any(int, float, str),
        vol.Optional(CONF_ATTRIBUTE): cv.string,
        vol.Required(ATTR_MATCH_TYPE): vol.In(
            [MATCH_TYPE_EQUAL, MATCH_TYPE_UNEQUAL, MATCH_TYPE_BELOW, MATCH_TYPE_ABOVE]
        ),
    }
)

ACTION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_SERVICE): cv.entity_id,
        vol.Optional(ATTR_SERVICE_DATA): dict,
    }
)

TIMESLOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_START): validate_time,
        vol.Optional(ATTR_STOP): validate_time,
        vol.Optional(CONF_CONDITIONS): vol.All(
            cv.ensure_list, vol.Length(min=1), [CONDITION_SCHEMA]
        ),
        vol.Optional(ATTR_CONDITION_TYPE): vol.In(
            [
                CONDITION_TYPE_AND,
                CONDITION_TYPE_OR,
            ]
        ),
        vol.Optional(ATTR_TRACK_CONDITIONS): cv.boolean,
        vol.Required(ATTR_ACTIONS): vol.All(
            cv.ensure_list, vol.Length(min=1), [ACTION_SCHEMA]
        ),
    }
)

ADD_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_WEEKDAYS, default=[DAY_TYPE_DAILY]): vol.All(
            cv.ensure_list,
            vol.Unique(),
            vol.Length(min=1),
            [
                vol.In(
                    WEEKDAYS
                    + [
                        DAY_TYPE_WORKDAY,
                        DAY_TYPE_WEEKEND,
                        DAY_TYPE_DAILY,
                    ]
                )
            ],
        ),
        vol.Optional(ATTR_START_DATE, default=None): validate_date,
        vol.Optional(ATTR_END_DATE, default=None): validate_date,
        vol.Required(ATTR_TIMESLOTS): vol.All(
            cv.ensure_list, vol.Length(min=1), [TIMESLOT_SCHEMA]
        ),
        vol.Required(ATTR_REPEAT_TYPE): vol.In(
            [
                REPEAT_TYPE_REPEAT,
                REPEAT_TYPE_SINGLE,
                REPEAT_TYPE_PAUSE,
            ]
        ),
        vol.Optional(ATTR_TIMER_TYPE, default=TIMER_TYPE_CALENDAR): vol.In(
            [
                TIMER_TYPE_CALENDAR,
                TIMER_TYPE_TRANSIENT,
            ]
        ),
        vol.Optional(ATTR_NAME): vol.Any(cv.string, None),
        vol.Optional(ATTR_TAGS): vol.All(cv.ensure_list, vol.Unique(), [cv.string]),
    }
)

EDIT_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_WEEKDAYS): vol.All(
            cv.ensure_list,
            vol.Unique(),
            vol.Length(min=1),
            [
                vol.In(
                    WEEKDAYS
                    + [
                        DAY_TYPE_WORKDAY,
                        DAY_TYPE_WEEKEND,
                        DAY_TYPE_DAILY,
                    ]
                )
            ],
        ),
        vol.Optional(ATTR_START_DATE, default=None): validate_date,
        vol.Optional(ATTR_END_DATE, default=None): validate_date,
        vol.Optional(ATTR_TIMESLOTS): vol.All(
            cv.ensure_list, vol.Length(min=1), [TIMESLOT_SCHEMA]
        ),
        vol.Optional(ATTR_REPEAT_TYPE): vol.In(
            [
                REPEAT_TYPE_REPEAT,
                REPEAT_TYPE_SINGLE,
                REPEAT_TYPE_PAUSE,
            ]
        ),
        vol.Optional(ATTR_TIMER_TYPE): vol.In(
            [
                TIMER_TYPE_CALENDAR,
                TIMER_TYPE_TRANSIENT,
            ]
        ),
        vol.Optional(ATTR_NAME): vol.Any(cv.string, None),
        vol.Optional(ATTR_TAGS): vol.All(cv.ensure_list, vol.Unique(), [cv.string]),
    }
)

RUN_IN_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DURATION): cv.time_period,
        vol.Required(ATTR_ACTIONS): vol.All(
            cv.ensure_list, vol.Length(min=1), [ACTION_SCHEMA]
        ),
        vol.Optional(ATTR_NAME): vol.Any(cv.string, None),
        vol.Optional(ATTR_PERSISTENT): cv.boolean,
    }
)


class SchedulerLogger:
    """Structured logging wrapper for scheduler component.
    
    Provides context-aware logging with schedule_id tagging for easier
    debugging and troubleshooting.
    """

    def __init__(self, name: str):
        """Initialize the logger."""
        self._logger = logging.getLogger(name)

    def _format_message(self, msg: str, schedule_id: str | None = None, **kwargs) -> str:
        """Format message with schedule context."""
        if schedule_id:
            msg = f"[{schedule_id}] {msg}"
        if kwargs:
            # Add extra context as key=value pairs
            context = " ".join(f"{k}={v}" for k, v in kwargs.items())
            msg = f"{msg} ({context})"
        return msg

    def debug(self, msg: str, schedule_id: str | None = None, **kwargs):
        """Log debug message."""
        self._logger.debug(self._format_message(msg, schedule_id, **kwargs))

    def info(self, msg: str, schedule_id: str | None = None, **kwargs):
        """Log info message."""
        self._logger.info(self._format_message(msg, schedule_id, **kwargs))

    def warning(self, msg: str, schedule_id: str | None = None, **kwargs):
        """Log warning message."""
        self._logger.warning(self._format_message(msg, schedule_id, **kwargs))

    def error(self, msg: str, schedule_id: str | None = None, **kwargs):
        """Log error message."""
        self._logger.error(self._format_message(msg, schedule_id, **kwargs))

    def exception(self, msg: str, schedule_id: str | None = None, **kwargs):
        """Log exception with traceback."""
        self._logger.exception(self._format_message(msg, schedule_id, **kwargs))


def get_logger(name: str) -> SchedulerLogger:
    """Get a structured logger for the scheduler component."""
    return SchedulerLogger(name)

