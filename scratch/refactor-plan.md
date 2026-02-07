# Scheduler Component Refactoring Plan

This plan breaks down the major improvements into 6 stages, each deliverable independently with minimal breaking changes. Each stage builds upon the previous, allowing for iterative testing and validation.

---

## Plan: Multi-stage Component Modernization

Refactor the scheduler component to support transient timers as first-class entities, decouple domain-specific logic, improve error handling and observability, and integrate with Home Assistant's calendar and timer platforms. The work is organized into 6 progressive stages to maintain stability and allow incremental deployment.

### Stage 1: Foundation & Architecture Prep (Non-Breaking)

1. **Extract climate logic** from [actions.py](custom_components/scheduler/actions.py) into new [domain_handlers.py](custom_components/scheduler/domain_handlers.py) with registry pattern for pluggable domain-specific action processors
2. **Add schema version 4** migration path in [store.py](custom_components/scheduler/store.py) with `created_at`, `updated_at`, and `timer_type` fields (default: "calendar" for existing schedules)
3. **Create error tracking** infrastructure: add `last_error`, `last_run`, `execution_count`, `failure_count` fields to `ScheduleEntry` dataclass
4. **Implement structured logging** wrapper in [const.py](custom_components/scheduler/const.py) with schedule_id context tagging
5. **Add entity attribute** `error_info` to [switch.py](custom_components/scheduler/switch.py) `ScheduleEntity` for exposing failures to UI and automations
6. **Write unit tests** for storage migration and domain handler registry

### Stage 2: Enhanced Error Handling & Observability

1. **Implement retry logic** in [actions.py](custom_components/scheduler/actions.py) `ActionQueue`: exponential backoff for transient failures (max 3 retries, configurable via options)
2. **Add validation service** `scheduler.validate` to check entity existence, service availability, and schema correctness before schedule creation
3. **Fire events** `scheduler_action_failed` and `scheduler_action_succeeded` with detailed context (schedule_id, entity_id, error, timeslot_index)
4. **Create binary sensor** `binary_sensor.scheduler_health` tracking overall component status (on = all schedules healthy, off = errors present)
5. **Implement notification system** via persistent notifications for critical failures (optional, configurable in options)
6. **Add diagnostic attributes** to entities: next 5 upcoming triggers, condition evaluation history, action queue status

### Stage 3: Transient Timer Support (Breaking for New Feature)

1. **Add timer type field** to schema: `CONF_TIMER_TYPE` with values `calendar` (default, current behavior) or `transient` (new countdown mode)
2. **Create countdown strategy** in [timer.py](custom_components/scheduler/timer.py): new `CountdownTimerHandler` class calculating `trigger_time = created_at + duration` without recurrence
3. **Implement auto-cleanup** in [switch.py](custom_components/scheduler/switch.py): transient schedules remove themselves from entity registry and storage after execution
4. **Add service** `scheduler.run_in` accepting `duration` (time_period), `actions` (list), and optional `name`, creating transient schedule internally
5. **Optimize storage** for transient timers: add `persistent` flag (default: true for calendar, false for transient < 5 min) to skip disk writes for volatile timers
6. **Update WebSocket API** to filter transient schedules from default list view (add `include_transient` parameter)

### Stage 4: Native Timer Entity Platform

1. **Register timer platform** in [\_\_init\_\_.py](custom_components/scheduler/__init__.py) alongside switch platform, routing based on `timer_type` field
2. **Implement** `SchedulerTimerEntity` in new [timer_entity.py](custom_components/scheduler/timer_entity.py) extending `RestoreTimerEntity` with pause/resume support
3. **Add services** `scheduler.pause_timer`, `scheduler.resume_timer`, `scheduler.adjust_timer` for dynamic countdown control
4. **Migrate switch logic** from [switch.py](custom_components/scheduler/switch.py): move shared timer/action handling to base class in new [base_entity.py](custom_components/scheduler/base_entity.py)
5. **Update UI integration** in [websockets.py](custom_components/scheduler/websockets.py): return entity type with schedule data for frontend rendering
6. **Handle platform coexistence**: existing calendar schedules remain as switches, new transient timers spawn as timer entities

### Stage 5: Calendar Integration

1. **Add calendar entity support** to `ScheduleEntry`: new `calendar_entities` field (list of entity_ids) and `calendar_match_pattern` (regex for event titles)
2. **Implement calendar monitor** in new [calendar_helper.py](custom_components/scheduler/calendar_helper.py): query calendar entities for events, cache results, track state changes
3. **Extend timer calculation** in [timer.py](custom_components/scheduler/timer.py): if `calendar_entities` present, compute next trigger from calendar events instead of weekdays
4. **Add calendar condition** type to `ConditionEntry`: validate schedule only executes during matching calendar events
5. **Create calendar entity** `calendar.scheduler_timeline` exposing all upcoming schedule triggers as calendar events for native dashboard visualization
6. **Update config flow** in [config_flow.py](custom_components/scheduler/config_flow.py): add calendar selector UI for easier configuration

### Stage 6: Advanced Features & Polish

1. **Implement template support** in action `service_data`: parse Jinja2 templates at execution time for dynamic values (e.g., temperature based on outdoor sensor)
2. **Add area-based targeting**: accept `area_id` in actions, resolve to all entities in area via `area_registry` at execution time
3. **Create condition templates**: support template-based conditions (e.g., `{{ states('sensor.solar_power') > 1000 }}`)
4. **Implement action prioritization**: add `priority` field to actions, process high-priority queues first during concurrent execution
5. **Add execution history storage**: persist last 100 executions per schedule with timestamps, success/failure, and error details
6. **Create diagnostics download**: implement `async_get_config_entry_diagnostics` for debugging support with anonymized schedule data

### Further Considerations

1. **Breaking changes:** Stage 3+ introduce new entities and services—should migration be automatic or opt-in via UI toggle? Recommend automatic with announcement in release notes.
2. **Storage performance:** With transient timers, consider moving to SQLite for execution history (Stage 6) instead of JSON—evaluate disk I/O impact first.
3. **Backward compatibility:** Should climate domain handler (Stage 1) preserve exact legacy behavior, or can timing/delay be adjusted? Suggest keeping legacy behavior with opt-in flag for new handler.
4. **Testing strategy:** Each stage requires integration tests with mock HA core—recommend test-driven development, writing tests before implementation.
5. **Migration tooling:** Stage 4 changes entity IDs (switch → timer)—should old entity_ids be preserved as aliases? Recommend entity registry migration utility.
6. **Documentation:** Each stage needs user-facing docs and developer API documentation—consider auto-generating from docstrings and schemas.
