import logging
import json
from typing import Any, Dict, List

from homeassistant.core import (
    HomeAssistant,
    callback,
    CoreState,
)
from homeassistant.const import (
    CONF_SERVICE,
    ATTR_SERVICE_DATA,
    CONF_SERVICE_DATA,
    CONF_DELAY,
    ATTR_ENTITY_ID,
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
    CONF_CONDITIONS,
    CONF_ATTRIBUTE,
    CONF_STATE,
    CONF_ACTION,
)
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_call_later,
)
from homeassistant.helpers.service import async_call_from_config
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers import (
    template,
    area_registry as ar,
    entity_registry as er,
)

from . import const
from .store import ScheduleEntry
from .domain_handlers import process_domain_handlers, avoid_redundant_action

_LOGGER = logging.getLogger(__name__)

ACTION_WAIT = "wait"
ACTION_WAIT_STATE_CHANGE = "wait_state_change"


def parse_service_call(data: dict):
    """turn action data into a service call"""

    service_call = {
        CONF_ACTION: (
            data[CONF_ACTION] if CONF_ACTION in data else data[CONF_SERVICE]
        ),  # map service->action for backwards compaibility
        CONF_SERVICE_DATA: data[ATTR_SERVICE_DATA] if ATTR_SERVICE_DATA in data else data.get(CONF_SERVICE_DATA, {}),
    }
    if ATTR_ENTITY_ID in data and data[ATTR_ENTITY_ID]:
        service_call[ATTR_ENTITY_ID] = data[ATTR_ENTITY_ID]

    if "area_id" in data and data["area_id"]:
        service_call["area_id"] = data["area_id"]

    return process_domain_handlers(service_call)


def entity_is_available(hass: HomeAssistant, entity, is_target_entity=False):
    """evaluate whether an entity is ready for targeting"""
    state = hass.states.get(entity)
    if state is None:
        return False
    elif state.state == STATE_UNAVAILABLE:
        return False
    elif state.state != STATE_UNKNOWN:
        return True
    elif is_target_entity:
        # only reject unknown state when scheduler is initializing
        coordinator = hass.data["scheduler"]["coordinator"]
        if coordinator.state == const.STATE_INIT:
            return False
        else:
            return True
    else:
        #  for condition entities the unknown state is not allowed
        return False


def action_is_available(hass: HomeAssistant, action: str):
    """evaluate whether a HA action is ready for targeting"""
    if action in [ACTION_WAIT, ACTION_WAIT_STATE_CHANGE]:
        return True
    domain = action.split(".").pop(0)
    domain_service = action.split(".").pop(1)
    return hass.services.has_service(domain, domain_service)


def validate_condition(hass: HomeAssistant, condition: dict, *args):
    """Validate a condition against the current state"""

    if not entity_is_available(hass, condition[ATTR_ENTITY_ID], True):
        return False

    state = hass.states.get(condition[ATTR_ENTITY_ID])

    required = condition[const.ATTR_VALUE]
    actual = state.state if state else None
    if len(args):
        actual = args[0]

    if condition[const.ATTR_MATCH_TYPE] in [
        const.MATCH_TYPE_BELOW,
        const.MATCH_TYPE_ABOVE,
    ] and isinstance(required, str):
        # parse condition as numeric if should be smaller or larger than X
        required = float(required)

    if isinstance(required, int):
        try:
            actual = int(float(actual)) # type: ignore
        except (ValueError, TypeError):
            return False
    elif isinstance(required, float):
        try:
            actual = float(actual) # type: ignore
        except (ValueError, TypeError):
            return False
    elif isinstance(required, str):
        actual = str(actual).lower()
        required = required.lower()

    if condition[const.ATTR_MATCH_TYPE] == const.MATCH_TYPE_EQUAL:
        result = actual == required
    elif condition[const.ATTR_MATCH_TYPE] == const.MATCH_TYPE_UNEQUAL:
        result = actual != required
    elif condition[const.ATTR_MATCH_TYPE] == const.MATCH_TYPE_BELOW:
        result = actual < required # type: ignore
    elif condition[const.ATTR_MATCH_TYPE] == const.MATCH_TYPE_ABOVE:
        result = actual > required # type: ignore
    else:
        result = False

    return result


def action_has_effect(action: dict, hass: HomeAssistant):
    """check if action has an effect on the entity"""
    return avoid_redundant_action(action, hass)


class ActionHandler:
    def __init__(self, hass: HomeAssistant, schedule_id: str):
        """init"""
        self.hass = hass
        self._queues = {}
        self._timer = None
        self.id = schedule_id
        self._logger = const.SchedulerLogger(_LOGGER, schedule_id)

        async_dispatcher_connect(
            self.hass, "action_queue_finished", self.async_cleanup_queues
        )

    async def async_queue_actions(
        self, data: Any, skip_initial_execution=False
    ):
        """add new actions to queue"""
        await self.async_empty_queue()

        # Determine if data is a TimeslotEntry or dict
        if hasattr(data, "conditions"):
            conditions = data.conditions
            actions_list = data.actions
            condition_type = data.condition_type
            track_conditions = data.track_conditions
        else:
            # Assume dict (backwards compatibility or manual trigger)
            conditions = data.get(CONF_CONDITIONS, [])
            actions_list = data.get(const.ATTR_ACTIONS, [])
            condition_type = data.get(const.ATTR_CONDITION_TYPE, const.CONDITION_TYPE_AND)
            track_conditions = data.get(const.ATTR_TRACK_CONDITIONS, False)

        def to_dict(obj):
            if hasattr(obj, "_asdict"):
                return obj._asdict()
            if hasattr(obj, "service"):
                return {"service": obj.service, "entity_id": obj.entity_id, "service_data": obj.service_data}
            return obj

        actions = [e for x in actions_list for e in parse_service_call(to_dict(x))]

        # create an ActionQueue object per targeted entity (such that the tasks are handled independently)
        for action in actions:
            entity = action[ATTR_ENTITY_ID] if ATTR_ENTITY_ID in action else "none"

            if entity not in self._queues:
                self._queues[entity] = ActionQueue(
                    self.hass, self.id, conditions, condition_type, track_conditions
                )

            self._queues[entity].add_action(action)

        for queue in self._queues.copy().values():
            await queue.async_start(skip_initial_execution)

    async def async_cleanup_queues(self, id: str | None = None):
        """remove all objects from queue which have no remaining tasks"""
        if id is not None and id != self.id or not len(self._queues.keys()):
            return

        # remove all items which are either finished executing
        # or have all their entities available (i.e. conditions have failed beforee)
        queue_items = list(self._queues.keys())
        for key in queue_items:
            if self._queues[key].is_finished() or (
                self._queues[key].is_available() and not self._queues[key].queue_busy
            ):
                await self._queues[key].async_clear()
                self._queues.pop(key)

        if not len(self._queues.keys()):
            self._logger.debug("Finished execution of tasks")

    async def async_empty_queue(self, **kwargs):
        """remove all objects from queue"""
        restore_time = kwargs.get("restore_time")

        async def async_clear_queue(_now=None):
            """clear queue"""
            if self._timer:
                self._timer()
                self._timer = None

            while len(self._queues.keys()):
                key = list(self._queues.keys())[0]
                await self._queues[key].async_clear()
                self._queues.pop(key)

        if restore_time:
            await self.async_cleanup_queues()
            if not len(self._queues):
                return

            self._logger.debug(
                "Waiting for unavailable entities to be restored for {} mins".format(
                    restore_time
                )
            )
            self._timer = async_call_later(
                self.hass, restore_time * 60, async_clear_queue
            )
        else:
            await async_clear_queue()


class ActionQueue:
    def __init__(
        self,
        hass: HomeAssistant,
        id: str,
        conditions: list,
        condition_type: str,
        track_conditions: bool,
    ):
        """create a new action queue"""
        self.hass = hass
        self.id = id
        self._logger = const.SchedulerLogger(_LOGGER, id)
        self._timer = None
        self._action_entities = []
        self._condition_entities = []
        self._listeners = []
        self._state_update_listener = None
        self._conditions = conditions
        self._condition_type = condition_type
        self._queue = []
        self.queue_busy = False
        self._track_conditions = track_conditions
        self._wait_for_available = True
        self._retries = 0
        self._max_retries = 3

        for condition in conditions:
            cond_dict = condition if isinstance(condition, dict) else {"entity_id": condition.entity_id}
            if (
                ATTR_ENTITY_ID in cond_dict
                and cond_dict[ATTR_ENTITY_ID] not in self._condition_entities
            ):
                self._condition_entities.append(cond_dict[ATTR_ENTITY_ID])

    def add_action(self, action: dict):
        """add an action to the queue"""
        if (
            ATTR_ENTITY_ID in action
            and action[ATTR_ENTITY_ID]
            and action[ATTR_ENTITY_ID] not in self._action_entities
        ):
            self._action_entities.append(action[ATTR_ENTITY_ID])

        self._queue.append(action)

    async def async_start(self, skip_initial_execution):
        """start execution of the actions in the queue"""

        @callback
        async def async_entity_changed(event):
            """check if actions can be processed"""
            entity = event.data["entity_id"]
            old_state = (
                event.data["old_state"].state if event.data["old_state"] else None
            )
            new_state = (
                event.data["new_state"].state if event.data["new_state"] else None
            )

            if old_state == new_state:
                # no change
                return

            if self.queue_busy:
                return

            if entity not in self._condition_entities and not self._wait_for_available:
                # only watch until entity becomes available in the action entities
                return

            if (
                entity in self._condition_entities
                and old_state
                and new_state
                and old_state not in [STATE_UNAVAILABLE, STATE_UNKNOWN]
                and new_state not in [STATE_UNAVAILABLE, STATE_UNKNOWN]
            ):
                conditions = list(
                    filter(lambda e: (e if isinstance(e, dict) else {"entity_id": e.entity_id})[ATTR_ENTITY_ID] == entity, self._conditions)
                )
                if all(
                    [
                        validate_condition(self.hass, item if isinstance(item, dict) else {"entity_id": item.entity_id, "value": item.value, "match_type": item.match_type}, old_state)
                        == validate_condition(self.hass, item if isinstance(item, dict) else {"entity_id": item.entity_id, "value": item.value, "match_type": item.match_type}, new_state)
                        for item in conditions
                    ]
                ):
                    # ignore if state change has no effect on condition rules
                    return

            self._logger.debug(
                "State of {} has changed, re-evaluating actions".format(entity)
            )
            await self.async_process_queue()

        watched_entities = list(set(self._condition_entities + self._action_entities))
        if len(watched_entities):
            self._listeners.append(
                async_track_state_change_event(
                    self.hass, watched_entities, async_entity_changed
                )
            )

        if not skip_initial_execution:
            await self.async_process_queue()

            # trigger the queue once when HA has restarted
            if self.hass.state != CoreState.running:
                self._listeners.append(
                    async_dispatcher_connect(
                        self.hass, const.EVENT_STARTED, self.async_process_queue
                    )
                )
        else:
            self._wait_for_available = False

    async def async_clear(self):
        """clear action queue object"""
        if self._timer:
            self._timer()
        self._timer = None

        while len(self._listeners):
            self._listeners.pop()()

        if self._state_update_listener:
            self._state_update_listener()
        self._state_update_listener = None

    def is_finished(self):
        """check whether all queue items are finished"""
        return len(self._queue) == 0

    def is_available(self):
        """check if all actions and entities involved in the task are available"""

        # check actions
        required_actions = [action[CONF_ACTION] for action in self._queue]
        failed_action = next(
            (x for x in required_actions if not action_is_available(self.hass, x)),
            None,
        )
        if failed_action:
            self._logger.debug(
                "Action {} is unavailable, scheduled task cannot be executed".format(
                    failed_action
                )
            )
            return False

        # check entities
        watched_entities = list(set(self._condition_entities + self._action_entities))
        failed_entity = next(
            (
                x
                for x in watched_entities
                if not entity_is_available(self.hass, x, x in self._action_entities)
            ),
            None,
        )
        if failed_entity:
            self._logger.debug(
                "Entity {} is unavailable, scheduled action cannot be executed".format(
                    failed_entity
                )
            )
            return False

        if self._wait_for_available:
            self._wait_for_available = False

        return True

    async def async_process_queue(self, task_idx=0):
        """walk through the list of tasks and execute the ones that are ready"""
        if self.queue_busy or not self.is_available():
            return

        self.queue_busy = True

        # verify conditions
        def check_cond(c):
            if isinstance(c, dict): return validate_condition(self.hass, c)
            return validate_condition(self.hass, {"entity_id": c.entity_id, "value": c.value, "match_type": c.match_type})

        conditions_passed = (
            (
                all(check_cond(item) for item in self._conditions)
                if self._condition_type == const.CONDITION_TYPE_AND
                else any(
                    check_cond(item) for item in self._conditions
                )
            )
            if len(self._conditions)
            else True
        )

        if not conditions_passed and len(self._queue):
            self._logger.debug("Conditions have failed, skipping execution of actions")
            if self._track_conditions:
                # postpone tasks
                self.queue_busy = False
                return

            else:
                # abort all items in queue
                while len(self._queue):
                    self._queue.pop()

        skip_task = False

        while task_idx < len(self._queue):
            task = self._queue[task_idx]

            if task[CONF_ACTION] in [ACTION_WAIT, ACTION_WAIT_STATE_CHANGE]:
                if skip_task:
                    task_idx = task_idx + 1
                    continue
                elif task[CONF_ACTION] == ACTION_WAIT_STATE_CHANGE:
                    state = self.hass.states.get(task[ATTR_ENTITY_ID])
                    if state is None:
                        self._logger.debug(
                            "Entity {} is not available, cannot wait for state change, skipping task".format(
                                task[ATTR_ENTITY_ID]
                            )
                        )
                        skip_task = True
                        task_idx = task_idx + 1
                        continue
                    if CONF_ATTRIBUTE in task[CONF_SERVICE_DATA]:
                        state = state.attributes.get(
                            task[CONF_SERVICE_DATA][CONF_ATTRIBUTE]
                        )
                    else:
                        state = state.state
                    if state == task[CONF_SERVICE_DATA][CONF_STATE]:
                        self._logger.debug(
                            "Entity {} is already set to {}, proceed with next task".format(
                                task[ATTR_ENTITY_ID],
                                state,
                            )
                        )
                        task_idx = task_idx + 1
                        continue

                @callback
                async def async_timer_finished(_now) -> None:
                    self._timer = None
                    if self._state_update_listener:
                        self._state_update_listener()
                    self._state_update_listener = None
                    self.queue_busy = False
                    await self.async_process_queue(task_idx + 1)

                self._timer = async_call_later(
                    self.hass,
                    task[CONF_SERVICE_DATA][CONF_DELAY],
                    async_timer_finished,
                )
                self._logger.debug(
                    "Postponing next task for {} seconds".format(
                        task[CONF_SERVICE_DATA][CONF_DELAY]
                    )
                )

                @callback
                async def async_entity_changed(event) -> None:
                    entity = event.data["entity_id"]
                    old_state = event.data["old_state"]
                    new_state = event.data["new_state"]

                    if CONF_ATTRIBUTE in task[CONF_SERVICE_DATA]:
                        old_state = old_state.attributes.get(
                            task[CONF_SERVICE_DATA][CONF_ATTRIBUTE]
                        )
                        new_state = new_state.attributes.get(
                            task[CONF_SERVICE_DATA][CONF_ATTRIBUTE]
                        )
                    else:
                        old_state = old_state.state
                        new_state = new_state.state
                    if old_state == new_state:
                        return
                    self._logger.debug(
                        "Entity {} was updated from {} to {}".format(
                            entity, old_state, new_state
                        )
                    )
                    if new_state == task[CONF_SERVICE_DATA][CONF_STATE]:
                        self._logger.debug("Stop postponing next task")
                        if self._timer:
                            self._timer()
                        self._timer = None
                        if self._state_update_listener is not None:
                            self._state_update_listener()
                        self._state_update_listener = None
                        self.queue_busy = False
                        await self.async_process_queue(task_idx + 1)

                if task[CONF_ACTION] == ACTION_WAIT_STATE_CHANGE:
                    self._state_update_listener = async_track_state_change_event(
                        self.hass, task[ATTR_ENTITY_ID], async_entity_changed
                    )
                return

            # Resolve Area Targeting
            if "area_id" in task:
                area_id = task["area_id"]
                area_entities = []
                ent_reg = er.async_get(self.hass)
                for entry in er.async_entries_for_area(ent_reg, area_id):
                    area_entities.append(entry.entity_id)

                for entity_id in area_entities:
                    sub_task = task.copy()
                    sub_task[ATTR_ENTITY_ID] = entity_id
                    del sub_task["area_id"]
                    await self._execute_action(sub_task, task_idx)

                task_idx += 1
                continue

            # Resolve Templates in Service Data
            if CONF_SERVICE_DATA in task:
                task[CONF_SERVICE_DATA] = template.render_complex(task[CONF_SERVICE_DATA], None)

            await self._execute_action(task, task_idx)
            task_idx = task_idx + 1

        self.queue_busy = False

        if not self._track_conditions or not len(self._conditions):
            while len(self._queue):
                self._queue.pop()

            async_dispatcher_send(self.hass, "action_queue_finished", self.id)
        else:
            self._logger.debug("Done for now, Waiting for conditions to change")

    async def _execute_action(self, task: dict, task_idx: int):
        """Execute a single action."""
        if ATTR_ENTITY_ID in task:
            self._logger.debug(
                "Executing action {} on entity {}".format(
                    task[CONF_ACTION], task[ATTR_ENTITY_ID]
                )
            )
        else:
            self._logger.debug("Executing action {}".format(task[CONF_ACTION]))

        skip_action = not action_has_effect(task, self.hass)
        if skip_action:
            self._logger.debug("Action has no effect, skipping")
        else:
            try:
                await async_call_from_config(
                    self.hass,
                    task,
                )
                self.hass.bus.async_fire("scheduler_action_succeeded", {
                    "schedule_id": self.id,
                    "entity_id": task.get(ATTR_ENTITY_ID),
                    "action": task.get(CONF_ACTION),
                    "task_idx": task_idx
                })
                # Update statistics
                async_dispatcher_send(self.hass, "scheduler_action_executed", self.id, True)
            except Exception as e:
                self._logger.error("Action {} failed with error: {}".format(task[CONF_ACTION], e))
                if self._retries < self._max_retries:
                    self._retries += 1
                    delay = 2 ** self._retries
                    self._logger.info("Retrying action in {} seconds (retry {}/{})".format(delay, self._retries, self._max_retries))

                    @callback
                    async def async_retry(_now):
                        await self._execute_action(task, task_idx)

                    async_call_later(self.hass, delay, async_retry)
                    return
                else:
                    self.hass.bus.async_fire("scheduler_action_failed", {
                        "schedule_id": self.id,
                        "entity_id": task.get(ATTR_ENTITY_ID),
                        "action": task.get(CONF_ACTION),
                        "error": str(e),
                        "task_idx": task_idx
                    })
                    # Update statistics
                    async_dispatcher_send(self.hass, "scheduler_action_executed", self.id, False, str(e))
