import logging
import datetime


import homeassistant.util.dt as dt_util
from homeassistant.const import (
    WEEKDAYS,
    STATE_ON,
    STATE_OFF,
)
from homeassistant.core import (
    HomeAssistant,
    callback,
)
from homeassistant.util import slugify
from homeassistant.components.timer import (
    ATTR_DURATION as TIMER_ATTR_DURATION,
    ATTR_REMAINING as TIMER_ATTR_REMAINING,
    ATTR_FINISHES_AT as TIMER_ATTR_FINISHES_AT,
    STATUS_ACTIVE,
    STATUS_IDLE,
    STATUS_PAUSED,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from propcache import cached_property


from . import const
from .store import async_get_registry
from .base_entity import BaseScheduleEntity
from .calendar_helper import async_collect_events

_LOGGER = logging.getLogger(__name__)

ATTR_NEXT_RISING = "next_rising"
ATTR_NEXT_SETTING = "next_setting"
ATTR_WORKDAYS = "workdays"


async def async_setup_entry(hass, _config_entry, async_add_entities):
    """Set up Scheduler timer entities (transient schedules)."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]

    @callback
    def async_add_entity(schedule):
        if schedule.timer_type != const.TIMER_TYPE_TRANSIENT:
            return

        name = schedule.name
        schedule_id = schedule.schedule_id
        if name:
            entity_id = f"timer.schedule_{slugify(name)}"
        else:
            entity_id = f"timer.schedule_{schedule_id}"

        entity = SchedulerTimerEntity(coordinator, hass, schedule_id, entity_id)
        hass.data[const.DOMAIN]["schedules"][schedule_id] = entity
        async_add_entities([entity])

    for entry in coordinator.store.schedules.values():
        async_add_entity(entry)

    async_dispatcher_connect(hass, const.EVENT_ITEM_CREATED, async_add_entity)


def has_sun(time_str: str):
    return const.OffsetTimePattern.match(time_str)


def is_same_day(dateA: datetime.datetime, dateB: datetime.datetime):
    return dateA.date() == dateB.date()


def days_until_date(date_string: str, ts: datetime.datetime):
    date = dt_util.parse_date(date_string)
    if date is None:
        raise ValueError(f"invalid date string: {date_string}")
    diff = date - ts.date()
    return diff.days


def find_closest_from_now(date_arr: list):
    now = dt_util.as_local(dt_util.utcnow())
    minimum = None
    for item in date_arr:
        if item is not None:
            if minimum is None:
                minimum = item
            elif item > now:
                if item < minimum or minimum < now:
                    minimum = item
            else:
                if item < minimum and minimum < now:
                    minimum = item
    return minimum


class TimerHandler:
    def __init__(self, hass: HomeAssistant, id: str):
        """init"""
        self.hass = hass
        self.id = id
        self._weekdays = []
        self._start_date = None
        self._end_date = None
        self._timeslots = []
        self._timer = None
        self._next_trigger = None
        self._next_slot = None
        self._sun_tracker = None
        self._workday_tracker = None
        self._watched_times: list[str] = []
        self._calendar_entities: list[str] | None = None
        self._calendar_match: str | None = None
        self._calendar_lookahead_days: int | None = None
        self._calendar_events: list[
            tuple[datetime.datetime, datetime.datetime, str | None]
        ] = []

        self.slot_queue = []
        self.timestamps = []
        self.current_slot = None

        self.hass.loop.create_task(self.async_reload_data())

        @callback
        async def async_item_updated(id: str):
            if id == self.id:
                await self.async_reload_data()

        self._update_listener = async_dispatcher_connect(
            self.hass, const.EVENT_ITEM_UPDATED, async_item_updated
        )

    async def async_reload_data(self):
        """load schedule data into timer class object and start timer"""
        store = await async_get_registry(self.hass)
        data = store.async_get_schedule(self.id)

        if data is None:
            return

        self._weekdays = data[const.ATTR_WEEKDAYS]
        self._start_date = data[const.ATTR_START_DATE]
        self._end_date = data[const.ATTR_END_DATE]
        self._timeslots = [
            dict((k, slot[k]) for k in [const.ATTR_START, const.ATTR_STOP] if k in slot)
            for slot in data[const.ATTR_TIMESLOTS]
        ]
        self._calendar_entities = data.get(const.ATTR_CALENDAR_ENTITIES)
        self._calendar_match = data.get(const.ATTR_CALENDAR_MATCH)
        self._calendar_lookahead_days = data.get(const.ATTR_CALENDAR_LOOKAHEAD)
        await self.async_start_timer()

    async def async_unload(self):
        """unload a timer class object"""
        await self.async_stop_timer()
        self._update_listener()
        self._next_trigger = None

    async def async_start_timer(self):
        if self._calendar_entities:
            await self._update_calendar_events()
            await self._start_calendar_timer()
            return

        [current_slot, timestamp_end] = self.current_timeslot()
        [next_slot, timestamp_next] = self.next_timeslot()

        self._watched_times = []
        if timestamp_next is not None and next_slot is not None:
            self._watched_times.append(self._timeslots[next_slot][const.ATTR_START])
        if timestamp_end is not None and current_slot is not None:
            self._watched_times.append(self._timeslots[current_slot][const.ATTR_STOP])

        # the next trigger time is next slot or end of current slot (whichever comes first)
        timestamp = find_closest_from_now([timestamp_end, timestamp_next])
        self._timer_is_endpoint = (
            timestamp != timestamp_next and timestamp == timestamp_end
        )
        if timestamp == timestamp_next and timestamp is not None:
            self._next_slot = next_slot
        else:
            self._next_slot = None

        self.current_slot = current_slot
        self._next_trigger = timestamp

        await self.async_start_sun_tracker()
        now = dt_util.as_local(dt_util.utcnow())

        if timestamp is not None:
            if self._timer:
                self._timer()

            if (timestamp - now).total_seconds() < 0:
                self._timer = None
                _LOGGER.debug(
                    "Timer of {} is not set because it is in the past ({})".format(
                        self.id, timestamp
                    )
                )
            else:
                self._timer = async_track_point_in_time(
                    self.hass, self.async_timer_finished, timestamp
                )
                _LOGGER.debug("Timer of {} set for {}".format(self.id, timestamp))
                await self.async_start_workday_tracker()

        async_dispatcher_send(self.hass, const.EVENT_TIMER_UPDATED, self.id)

    async def _update_calendar_events(self):
        """Fetch calendar events for configured entities."""
        now = dt_util.as_local(dt_util.utcnow())
        lookahead = self._calendar_lookahead_days or const.CALENDAR_LOOKAHEAD_DAYS
        end = now + datetime.timedelta(days=lookahead)
        self._calendar_events = await async_collect_events(
            self.hass,
            self._calendar_entities or [],
            now,
            end,
            self._calendar_match,
        )

    async def _start_calendar_timer(self):
        """Start timer based on calendar events."""
        now = dt_util.as_local(dt_util.utcnow())
        current_event = next(
            (e for e in self._calendar_events if e[0] <= now <= e[1]),
            None,
        )
        next_event = next((e for e in self._calendar_events if e[0] > now), None)

        timestamp_end = current_event[1] if current_event else None
        timestamp_next = next_event[0] if next_event else None

        self.slot_queue = [0] if (timestamp_next or timestamp_end) else []
        self.timestamps = [t for t in [timestamp_next] if t is not None]

        timestamp = find_closest_from_now([timestamp_end, timestamp_next])
        self._timer_is_endpoint = (
            timestamp != timestamp_next and timestamp == timestamp_end
        )
        if timestamp == timestamp_next and timestamp is not None:
            self._next_slot = 0
        else:
            self._next_slot = None

        self.current_slot = 0 if current_event else None
        self._next_trigger = timestamp

        if timestamp is not None:
            if self._timer:
                self._timer()
            if (timestamp - now).total_seconds() < 0:
                self._timer = None
            else:
                self._timer = async_track_point_in_time(
                    self.hass, self.async_timer_finished, timestamp
                )
        async_dispatcher_send(self.hass, const.EVENT_TIMER_UPDATED, self.id)

    async def async_stop_timer(self):
        """stop the timer"""
        if self._timer:
            self._timer()
            self._timer = None
        await self.async_stop_sun_tracker()
        await self.async_stop_workday_tracker()

    async def async_start_sun_tracker(self):
        """check for changes in the sun sensor"""
        if (
            self._next_trigger is not None
            and any(has_sun(x) for x in self._watched_times)
        ) or (
            self._next_trigger is None
            and all(has_sun(x[const.ATTR_START]) for x in self._timeslots)
        ):
            # install sun tracker for updating timer when sun changes
            # initially the time calculation may fail due to the sun entity being unavailable

            if self._sun_tracker is not None:
                # the tracker is already running
                return

            @callback
            async def async_sun_updated(_event) -> None:
                """the sun entity was updated"""
                # sun entity changed
                if self._next_trigger is None:
                    # sun entity has initialized
                    await self.async_start_timer()
                    return
                ts = find_closest_from_now(
                    [self.calculate_timestamp(x) for x in self._watched_times]
                )
                if not ts or not self._next_trigger:
                    # sun entity became unavailable (or other corner case)
                    await self.async_start_timer()
                    return
                # we are re-scheduling an existing timer
                delta = (ts - self._next_trigger).total_seconds()
                if abs(delta) >= 60 and abs(delta) < 2000:
                    # only reschedule if the difference is at least a minute
                    # only reschedule if this doesnt cause the timer to shift to another day (+/- 24 hrs delta)
                    # only reschedule if this doesnt cause the timer to shift to another hour (due to DST change)
                    await self.async_start_timer()

            self._sun_tracker = async_track_state_change_event(
                self.hass, const.SUN_ENTITY, async_sun_updated
            )
        else:
            # clear existing tracker
            await self.async_stop_sun_tracker()

    async def async_stop_sun_tracker(self):
        """stop checking for changes in the sun sensor"""
        if self._sun_tracker:
            self._sun_tracker()
            self._sun_tracker = None

    async def async_start_workday_tracker(self):
        """check for changes in the workday sensor"""
        if (
            const.DAY_TYPE_WORKDAY in self._weekdays
            or const.DAY_TYPE_WEEKEND in self._weekdays
        ):
            # install tracker for updating timer when workday sensor changes

            if self._workday_tracker is not None:
                # the tracker is already running
                return

            @callback
            async def async_workday_updated():
                """the workday sensor was updated"""
                [current_slot, timestamp_end] = self.current_timeslot()
                [next_slot, timestamp_next] = self.next_timeslot()
                ts_next = find_closest_from_now([timestamp_end, timestamp_next])

                # workday entity changed
                if not ts_next or not self._next_trigger:
                    # timer was not yet set
                    await self.async_start_timer()
                else:
                    # we are re-scheduling an existing timer
                    delta = (ts_next - self._next_trigger).total_seconds()
                    if abs(delta) >= 60:
                        # only reschedule if the difference is at least a minute
                        await self.async_start_timer()

            self._workday_tracker = async_dispatcher_connect(
                self.hass, const.EVENT_WORKDAY_SENSOR_UPDATED, async_workday_updated
            )
        else:
            # clear existing tracker
            await self.async_stop_workday_tracker()

    async def async_stop_workday_tracker(self):
        """stop checking for changes in the workday sensor"""
        if self._workday_tracker:
            self._workday_tracker()
            self._workday_tracker = None

    async def async_timer_finished(self, _time):
        """the timer is finished"""
        if not self._timer_is_endpoint:
            # timer marks the start of a new timeslot
            self.current_slot = self._next_slot
            _LOGGER.debug(
                "Timer {} has reached slot {}".format(self.id, self.current_slot)
            )
            async_dispatcher_send(self.hass, const.EVENT_TIMER_FINISHED, self.id)
            # don't automatically reset, wait for external reset after 1 minute
            # await self.async_start_timer()
            await self.async_stop_timer()
        else:
            # timer marks the end of a timeslot
            _LOGGER.debug(
                "Timer {} has reached end of timeslot, resetting..".format(self.id)
            )
            await self.async_start_timer()

    def day_in_weekdays(self, ts: datetime.datetime) -> bool:
        """check if the day of a datetime object is in the allowed list of days"""
        day = WEEKDAYS[ts.weekday()]
        workday_sensor = self.hass.states.get(const.WORKDAY_ENTITY)

        if (
            workday_sensor
            and workday_sensor.state in [STATE_ON, STATE_OFF]
            and is_same_day(ts, dt_util.as_local(dt_util.utcnow()))
        ):
            # state of workday sensor is used for evaluating workday vs weekend
            if const.DAY_TYPE_WORKDAY in self._weekdays:
                return workday_sensor.state == STATE_ON
            elif const.DAY_TYPE_WEEKEND in self._weekdays:
                return workday_sensor.state == STATE_OFF

        if workday_sensor and ATTR_WORKDAYS in workday_sensor.attributes:
            # workday sensor defines a list of workdays
            workday_list = workday_sensor.attributes[ATTR_WORKDAYS]
            weekend_list = [e for e in WEEKDAYS if e not in workday_list]
        else:
            # assume workdays are mon-fri
            workday_list = WEEKDAYS[0:5]
            weekend_list = WEEKDAYS[5:7]

        if const.DAY_TYPE_DAILY in self._weekdays or not len(self._weekdays):
            return True
        elif const.DAY_TYPE_WORKDAY in self._weekdays and day in workday_list:
            return True
        elif const.DAY_TYPE_WEEKEND in self._weekdays and day in weekend_list:
            return True
        return day in self._weekdays

    def calculate_timestamp(
        self,
        time_str: str,
        now: datetime.datetime | None = None,
        iteration: int = 0,
        reverse_direction: bool = False,
    ) -> datetime.datetime | None:
        """calculate the next occurence of a time string"""
        if time_str is None:
            return None
        if now is None:
            now = dt_util.as_local(dt_util.utcnow())

        res = has_sun(time_str)
        if not res:
            # fixed time
            time = dt_util.parse_time(time_str)
            if time is None:
                raise ValueError(f"invalid time string: {time_str}")
            ts = dt_util.find_next_time_expression_time(
                now, [time.second], [time.minute], [time.hour]
            )
        else:
            # relative to sunrise/sunset
            sun = self.hass.states.get(const.SUN_ENTITY)
            if not sun:
                return None
            ts = None
            if (
                res.group(1) == const.SUN_EVENT_SUNRISE
                and ATTR_NEXT_RISING in sun.attributes
            ):
                ts = dt_util.parse_datetime(sun.attributes[ATTR_NEXT_RISING])
            elif (
                res.group(1) == const.SUN_EVENT_SUNSET
                and ATTR_NEXT_SETTING in sun.attributes
            ):
                ts = dt_util.parse_datetime(sun.attributes[ATTR_NEXT_SETTING])
            if not ts:
                return None
            ts = dt_util.as_local(ts)
            ts = ts.replace(second=0)
            time_sun = datetime.timedelta(
                hours=ts.hour, minutes=ts.minute, seconds=ts.second
            )
            offset = dt_util.parse_time(res.group(3))
            if offset is None:
                raise ValueError(f"invalid time string: {res.group(3)}")
            offset = datetime.timedelta(
                hours=offset.hour, minutes=offset.minute, seconds=offset.second
            )
            if res.group(2) == "-":
                if (time_sun - offset).total_seconds() >= 0:
                    ts = ts - offset
                else:
                    # prevent offset to shift the time past the extends of the day
                    ts = ts.replace(hour=0, minute=0, second=0)
            else:
                if (time_sun + offset).total_seconds() <= 86340:
                    ts = ts + offset
                else:
                    # prevent offset to shift the time past the extends of the day
                    ts = ts.replace(hour=23, minute=59, second=0)
            ts = dt_util.find_next_time_expression_time(
                now, [ts.second], [ts.minute], [ts.hour]
            )

        time_delta = datetime.timedelta(seconds=1)

        if self.day_in_weekdays(ts) and (
            (ts - now).total_seconds() > 0 or iteration > 0
        ):

            if self._start_date and days_until_date(self._start_date, ts) > 0:
                # start date is in the future, jump to start date
                end_of_day = ts.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)
                days_delta = days_until_date(self._start_date, end_of_day)
                if days_delta:
                    time_delta = datetime.timedelta(days=days_delta)

            elif self._end_date and days_until_date(self._end_date, ts) < 0:
                # end date is in the past, jump to end date
                time_delta = datetime.timedelta(
                    days=days_until_date(self._end_date, ts)
                )
                reverse_direction = True

            else:
                # date restrictions are met
                return ts


        elif reverse_direction:
            time_delta = datetime.timedelta(days=-1)

        # calculate next timestamp
        next_day = dt_util.find_next_time_expression_time(
            now + time_delta, [0], [0], [0]
        )
        if iteration > 15:
            _LOGGER.warning(
                "failed to calculate next timeslot for schedule {}".format(self.id)
            )
            return None
        return self.calculate_timestamp(
            time_str, next_day, iteration + 1, reverse_direction
        )

    def next_timeslot(self):
        """calculate the closest timeslot from now"""
        if self._calendar_entities:
            now = dt_util.as_local(dt_util.utcnow())
            next_event = next((e for e in self._calendar_events if e[0] > now), None)
            return (0, next_event[0] if next_event else None)

        now = dt_util.as_local(dt_util.utcnow())
        # calculate next start of all timeslots
        timestamps = [
            self.calculate_timestamp(slot[const.ATTR_START], now)
            for slot in self._timeslots
        ]

        # calculate timeslot that will start soonest (or closest in the past)
        remaining = [
            abs((ts - now).total_seconds()) if ts is not None else now.timestamp()
            for ts in timestamps
        ]
        slot_order = sorted(range(len(remaining)), key=lambda k: remaining[k])

        # filter out timeslots that cannot be computed
        for i in range(len(timestamps)):
            if timestamps[i] is None:
                slot_order.remove(i)
        timestamps = [e for e in timestamps if e is not None]

        self.slot_queue = slot_order
        self.timestamps = timestamps

        next_slot = slot_order[0] if len(slot_order) > 0 else None

        return (next_slot, timestamps[next_slot] if next_slot is not None else None)

    def current_timeslot(self, now: datetime.datetime | None = None):
        """calculate the end of the timeslot that is overlapping now"""
        if now is None:
            now = dt_util.as_local(dt_util.utcnow())

        if self._calendar_entities:
            current_event = next(
                (e for e in self._calendar_events if e[0] <= now <= e[1]),
                None,
            )
            if current_event:
                return (0, current_event[1])
            return (None, None)

        def unwrap_end_of_day(time_str: str):
            if time_str == "00:00:00":
                return "23:59:59"
            else:
                return time_str

        # calculate next stop of all timeslots
        timestamps = []
        for slot in self._timeslots:
            if slot[const.ATTR_STOP] is not None:
                timestamps.append(
                    self.calculate_timestamp(
                        unwrap_end_of_day(slot[const.ATTR_STOP]), now
                    )
                )
            else:
                ts = self.calculate_timestamp(slot[const.ATTR_START], now)
                if ts is None:
                    timestamps.append(None)
                else:
                    ts = ts + datetime.timedelta(minutes=1)
                    timestamps.append(
                        self.calculate_timestamp(ts.strftime("%H:%M:%S"), now)
                    )

        # calculate timeslot that will end soonest
        remaining = [
            (ts - now).total_seconds() if ts is not None else now.timestamp()
            for ts in timestamps
        ]
        (next_slot_end, val) = sorted(
            enumerate(remaining), key=lambda i: (i[1] < 0, abs(i[1]))
        )[0]

        stop = timestamps[next_slot_end]
        if stop is not None:
            # calculate last start of timeslot that will end soonest
            if (stop - now).total_seconds() < 0:
                # end of timeslot is in the past
                return (None, None)

            start = self.calculate_timestamp(
                self._timeslots[next_slot_end][const.ATTR_START],
                stop - datetime.timedelta(days=1),
            )

            if start is not None:
                elapsed = (now - start).total_seconds()
                if elapsed > 0:
                    # timeslot is currently overlapping
                    return (
                        next_slot_end,
                        stop
                        if self._timeslots[next_slot_end][const.ATTR_STOP] is not None
                        else None,
                    )
        return (None, None)


class CountdownTimerHandler:
    """Timer handler for transient countdown schedules."""

    def __init__(self, hass: HomeAssistant, id: str):
        self.hass = hass
        self.id = id
        self._timer = None
        self._trigger_time: datetime.datetime | None = None
        self._update_listener = None

        self.slot_queue: list[int] = []
        self.timestamps: list[datetime.datetime] = []
        self.current_slot: int | None = None

        self.hass.loop.create_task(self.async_reload_data())

        @callback
        async def async_item_updated(schedule_id: str):
            if schedule_id == self.id:
                await self.async_reload_data()

        self._update_listener = async_dispatcher_connect(
            self.hass, const.EVENT_ITEM_UPDATED, async_item_updated
        )

    async def async_reload_data(self):
        """Load countdown data and start timer."""
        store = await async_get_registry(self.hass)
        data = store.async_get_schedule(self.id)

        if data is None:
            return

        if not data.get(const.ATTR_ENABLED, True):
            self._trigger_time = None
            self.slot_queue = []
            self.timestamps = []
            self.current_slot = None
            await self.async_stop_timer()
            return

        started_at = data.get(const.ATTR_STARTED_AT)
        duration = data.get(const.ATTR_DURATION)

        if not started_at or duration is None:
            self._trigger_time = None
            self.slot_queue = []
            self.timestamps = []
            self.current_slot = None
            await self.async_stop_timer()
            return

        ts = dt_util.parse_datetime(started_at)
        if ts is None:
            self._trigger_time = None
            await self.async_stop_timer()
            return

        self._trigger_time = dt_util.as_local(ts) + datetime.timedelta(seconds=duration)
        self.slot_queue = [0]
        self.timestamps = [self._trigger_time]
        self.current_slot = None
        await self.async_start_timer()

    async def async_unload(self):
        """Unload countdown timer."""
        await self.async_stop_timer()
        if self._update_listener:
            self._update_listener()
            self._update_listener = None

    async def async_start_timer(self):
        """Start the countdown timer."""
        if self._trigger_time is None:
            return

        if self._timer:
            self._timer()

        now = dt_util.as_local(dt_util.utcnow())
        if (self._trigger_time - now).total_seconds() <= 0:
            _LOGGER.debug("Countdown timer %s is in the past, triggering now", self.id)
            self.current_slot = 0
            async_dispatcher_send(self.hass, const.EVENT_TIMER_FINISHED, self.id)
            return

        self._timer = async_track_point_in_time(
            self.hass, self.async_timer_finished, self._trigger_time
        )
        _LOGGER.debug("Countdown timer %s set for %s", self.id, self._trigger_time)

        async_dispatcher_send(self.hass, const.EVENT_TIMER_UPDATED, self.id)

    async def async_stop_timer(self):
        """Stop the countdown timer."""
        if self._timer:
            self._timer()
            self._timer = None

    async def async_timer_finished(self, _time):
        """Countdown has finished."""
        self.current_slot = 0
        async_dispatcher_send(self.hass, const.EVENT_TIMER_FINISHED, self.id)
        await self.async_stop_timer()

    def current_timeslot(self, now: datetime.datetime | None = None):
        """Return the current timeslot for transient timers."""
        if self._trigger_time is None:
            return (None, None)
        return (0, self._trigger_time)


class SchedulerTimerEntity(BaseScheduleEntity, RestoreEntity):
    """Timer entity for transient schedules."""

    def __init__(self, coordinator, hass, schedule_id: str, entity_id: str) -> None:
        BaseScheduleEntity.__init__(self, coordinator, hass, schedule_id)
        self.entity_id = entity_id
        self._state = STATUS_IDLE
        self._listeners = []

    async def async_added_to_hass(self) -> None:
        await self.async_load_schedule()
        self._listeners.append(
            async_dispatcher_connect(
                self.hass, const.EVENT_ITEM_UPDATED, self.async_item_updated
            )
        )
        self._listeners.append(
            async_dispatcher_connect(
                self.hass, const.EVENT_TIMER_UPDATED, self.async_timer_updated
            )
        )
        self._listeners.append(
            async_dispatcher_connect(
                self.hass, const.EVENT_TIMER_FINISHED, self.async_timer_finished
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        while self._listeners:
            self._listeners.pop()()
        if self._timer_handler:
            await self._timer_handler.async_unload()

    @callback
    async def async_item_updated(self, schedule_id: str):
        if schedule_id != self.schedule_id:
            return
        await self.async_load_schedule()
        self.async_write_ha_state()

    @callback
    async def async_timer_updated(self, schedule_id: str):
        if schedule_id != self.schedule_id:
            return
        self.async_write_ha_state()

    @callback
    async def async_timer_finished(self, schedule_id: str):
        if schedule_id != self.schedule_id:
            return
        if self.schedule is None:
            return

        try:
            await self._action_handler.async_queue_actions(
                self.schedule[const.ATTR_TIMESLOTS][0]
            )
            await self._track_execution_success()
        except Exception as err:
            await self._track_execution_failure(str(err))
            _LOGGER.exception(
                "Timer schedule %s action execution failed: %s",
                self.schedule_id,
                err,
            )

        self._state = STATUS_IDLE
        self.async_write_ha_state()
        self.coordinator.async_delete_schedule(self.schedule_id)
        await self.async_remove()

    @cached_property
    def state(self) -> str:
        if self.schedule is None:
            return STATUS_IDLE
        if not self.schedule[const.ATTR_ENABLED]:
            return STATUS_PAUSED
        remaining = self._remaining_delta()
        if remaining and remaining.total_seconds() > 0:
            return STATUS_ACTIVE
        return STATUS_IDLE

    def _remaining_delta(self):
        if self.schedule is None:
            return None
        if self.schedule.started_at is None or self.schedule.duration is None:
            return None
        started = dt_util.parse_datetime(self.schedule.started_at)
        if started is None:
            return None
        end = dt_util.as_local(started) + datetime.timedelta(
            seconds=self.schedule.duration
        )
        return end - dt_util.as_local(dt_util.utcnow())

    @cached_property
    def extra_state_attributes(self):
        duration = (
            datetime.timedelta(seconds=self.schedule.duration)
            if self.schedule and self.schedule.duration is not None
            else None
        )
        remaining = self._remaining_delta()
        finishes_at = None
        if self.schedule and self.schedule.started_at and self.schedule.duration:
            started = dt_util.parse_datetime(self.schedule.started_at)
            if started is not None:
                finishes_at = (
                    dt_util.as_local(started)
                    + datetime.timedelta(seconds=self.schedule.duration)
                ).isoformat()

        attrs = {
            TIMER_ATTR_DURATION: duration,
            TIMER_ATTR_REMAINING: remaining,
            TIMER_ATTR_FINISHES_AT: finishes_at,
            "entity_type": "timer",
        }
        return attrs

    @cached_property
    def name(self):
        return self.schedule.name if self.schedule else None

    @cached_property
    def unique_id(self):
        return f"{self.schedule_id}_timer"

    @callback
    def async_start(self, duration: datetime.timedelta | None = None) -> None:
        """Start or restart the timer."""
        self.hass.async_create_task(self._async_start(duration))

    async def _async_start(self, duration: datetime.timedelta | None = None) -> None:
        if self.schedule is None:
            return
        store = await async_get_registry(self.hass)
        duration_seconds = (
            int(duration.total_seconds())
            if duration is not None
            else self.schedule.duration
        )
        updates = {
            const.ATTR_STARTED_AT: dt_util.utcnow().isoformat(),
            const.ATTR_DURATION: duration_seconds,
            const.ATTR_ENABLED: True,
        }
        store.async_update_schedule(self.schedule_id, updates)

    @callback
    def async_pause(self) -> None:
        """Pause the timer."""
        self.hass.async_create_task(self._async_pause())

    async def _async_pause(self) -> None:
        if self.schedule is None:
            return
        store = await async_get_registry(self.hass)
        store.async_update_schedule(self.schedule_id, {const.ATTR_ENABLED: False})

    @callback
    def async_cancel(self) -> None:
        """Cancel and remove the timer."""
        self.hass.async_create_task(self._async_cancel())

    async def _async_cancel(self) -> None:
        self.coordinator.async_delete_schedule(self.schedule_id)
        await self.async_remove()

    @callback
    def async_get_entity_state(self):
        """fetch schedule data for websocket API"""
        data = {
            const.ATTR_SCHEDULE_ID: self.schedule_id,
            "entity_id": self.entity_id,
            "name": self.schedule.name if self.schedule else "",
            "entity_type": "timer",
            const.ATTR_TIMER_TYPE: const.TIMER_TYPE_TRANSIENT,
        }
        return data
