To redesign the core logic and make transient schedules (timers/one-offs) "first-class citizens," we need to move away from treating them as "short-lived recurring schedules" and implement a dedicated architecture for them.

This involves changes across the data model, storage strategy, and entity representation. Here is a proposal for redesigning the core logic:

### 1. New Data Model: "Relative" vs. "Absolute" Schedules

Currently, `store.py` and `const.py` force every schedule to fit into a `timeslot` model (start time / stop time). To support transient timers, we need a distinct schema that defines **execution relative to a trigger** rather than a clock time.

* **Refactoring `const.py**`: Introduce a `schedule_type` field.
* `type: calendar` (Current behavior: absolute timestamps, recurring).
* `type: countdown` (New behavior: relative duration, non-recurring).


* **Schema Change**: For `countdown` types, replace `timeslots` with a simpler `timer_config`:
```python
TIMER_SCHEMA = vol.Schema({
    vol.Required("duration"): cv.time_period, # e.g., 00:10:00
    vol.Optional("started_at"): cv.datetime,  # timestamp of creation
    vol.Required("actions"): [...]
})

```



### 2. Update Timer Logic (`timer.py`) for Fixed Targets

The current `TimerHandler` in `timer.py` aggressively calculates the *next* occurrence based on the current time and complex recurrence rules (workdays, sun).

* **Redesign:** Split `TimerHandler` into two strategies:
1. `RecurrenceStrategy`: The existing logic for daily/weekly schedules.
2. `CountdownStrategy`: A simplified logic for transient timers.
* **Calculation:** `trigger_time = started_at + duration`.
* **No Recalculation:** Unlike recurring schedules, this creates a single, immutable target timestamp. It does not need to check `workday_sensor` or sun position updates.




* **Restart Handling:** On HA restart, the logic simply checks: `if (started_at + duration) > now`.
* If `True`: Resume `async_track_point_in_time` for the remaining delta.
* If `False`: Immediate execution (missed event handling).





### 3. Entity Platform: Expose as `timer` domain

Currently, everything is a `switch`. This is unintuitive for a countdown.

* **New Platform (`timer.py` - HA entity):** Register a `timer` platform in `__init__.py` alongside `switch`.
* **Behavior:**
* When a transient schedule is created, it spawns a `timer.scheduler_[id]` entity instead of a switch.
* **Native Features:** This gives users standard `timer.pause`, `timer.start`, and `timer.cancel` services, and a native UI progress bar.


* **Action Execution:** When the `timer` entity finishes, it calls the `ActionHandler` directly.

### 4. Optimized "Transient" Storage (`store.py`)

Writing every 10-minute timer to `scheduler.storage` (JSON) might cause excessive disk I/O if used heavily.

* **Hybrid Storage Strategy:**
* **Persistent:** Long-running timers (e.g., > 1 hour) are written to `store.py` to survive restarts.
* **Volatile:** Short timers (e.g., < 5 minutes) could be kept in memory only to reduce wear, optionally with a flag `restore: true/false`.


* **Garbage Collection:**
* Modify `switch.py` (or the new timer entity) to implement a `self-destruct` callback that ensures the entity is removed from the registry immediately after execution, preventing the "zombie entity" buildup that currently requires `repeat_type: single` logic.



### 5. New Service Interface: `scheduler.run_in`

To make this usable for voice assistants and scripts, we need a dedicated service that abstracts the complexity of creating a schedule.

**Proposed Service YAML:**

```yaml
scheduler.run_in:
  description: "Run a sequence of actions after a specific duration."
  fields:
    duration:
      description: "Time to wait (e.g., 00:10:00)"
      example: "00:10:00"
    actions:
      description: "List of service calls to execute"
    name:
      description: "Optional label for the timer"

```

**Implementation in `__init__.py**`:
This service would internally call `coordinator.async_create_schedule` with the new `type: countdown` metadata, bypassing the need for the user to calculate `start_date` or manipulate `timeslots`.

### Summary of Core Redesign

| Feature | Current Logic | Proposed Redesign |
| --- | --- | --- |
| **Logic** | "Find next slot" (Scanning) | "Target Time" (Fixed) |
| **Storage** | Heavy JSON struct (`timeslots`) | Lightweight struct (`duration`, `start_ts`) |
| **Entity** | `switch` (On/Off) | `timer` (Active/Paused/Idle) |
| **Creation** | `scheduler.add` (Manual calculation) | `scheduler.run_in` (Duration based) |