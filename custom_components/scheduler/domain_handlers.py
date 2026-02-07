from typing import List, Dict, Any, Callable, Optional
from homeassistant.core import HomeAssistant
from homeassistant.const import (
    CONF_SERVICE_DATA,
    CONF_DELAY,
    ATTR_ENTITY_ID,
    CONF_STATE,
    CONF_ACTION,
    ATTR_TEMPERATURE,
)
from homeassistant.components.climate.const import (
    SERVICE_SET_TEMPERATURE,
    SERVICE_SET_HVAC_MODE,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_HIGH,
    DOMAIN as CLIMATE_DOMAIN,
)

ACTION_WAIT_STATE_CHANGE = "wait_state_change"

DomainHandler = Callable[[Dict[str, Any]], List[Dict[str, Any]]]
EffectHandler = Callable[[Dict[str, Any], HomeAssistant], bool]

_HANDLERS: Dict[str, DomainHandler] = {}
_EFFECT_HANDLERS: Dict[str, EffectHandler] = {}


def register_handler(domain: str, service: str):
    def decorator(func: DomainHandler):
        _HANDLERS[f"{domain}.{service}"] = func
        return func
    return decorator


def register_effect_handler(domain: str, service: str):
    def decorator(func: EffectHandler):
        _EFFECT_HANDLERS[f"{domain}.{service}"] = func
        return func
    return decorator


@register_handler(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
def handle_climate_set_temperature(service_call: Dict[str, Any]) -> List[Dict[str, Any]]:
    if (
        ATTR_HVAC_MODE in service_call[CONF_SERVICE_DATA]
        and ATTR_ENTITY_ID in service_call
    ):
        _service_call = [
            {
                CONF_ACTION: "{}.{}".format(CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE),
                ATTR_ENTITY_ID: service_call[ATTR_ENTITY_ID],
                CONF_SERVICE_DATA: {
                    ATTR_HVAC_MODE: service_call[CONF_SERVICE_DATA][ATTR_HVAC_MODE]
                },
            }
        ]
        if (
            ATTR_TEMPERATURE in service_call[CONF_SERVICE_DATA]
            or ATTR_CURRENT_TEMPERATURE in service_call[CONF_SERVICE_DATA]
            or ATTR_TARGET_TEMP_LOW in service_call[CONF_SERVICE_DATA]
            or ATTR_TARGET_TEMP_HIGH in service_call[CONF_SERVICE_DATA]
        ):
            _service_call.extend(
                [
                    {
                        CONF_ACTION: ACTION_WAIT_STATE_CHANGE,
                        ATTR_ENTITY_ID: service_call[ATTR_ENTITY_ID],
                        CONF_SERVICE_DATA: {
                            CONF_DELAY: 50,
                            CONF_STATE: service_call[CONF_SERVICE_DATA][ATTR_HVAC_MODE],
                        },
                    },
                    {
                        CONF_ACTION: "{}.{}".format(
                            CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE
                        ),
                        ATTR_ENTITY_ID: service_call[ATTR_ENTITY_ID],
                        CONF_SERVICE_DATA: {
                            x: service_call[CONF_SERVICE_DATA][x]
                            for x in service_call[CONF_SERVICE_DATA]
                            if x != ATTR_HVAC_MODE
                        },
                    },
                ]
            )
        return _service_call
    return [service_call]


@register_effect_handler(CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE)
@register_effect_handler(CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE)
def climate_action_has_effect(action: Dict[str, Any], hass: HomeAssistant) -> bool:
    state = hass.states.get(action[ATTR_ENTITY_ID])
    if not state:
        return True

    current_state = state.state
    if (
        ATTR_HVAC_MODE in action[CONF_SERVICE_DATA]
        and action[CONF_SERVICE_DATA][ATTR_HVAC_MODE] != current_state
    ):
        return True
    if ATTR_TEMPERATURE in action[CONF_SERVICE_DATA] and float(
        state.attributes.get(ATTR_TEMPERATURE, 0) or 0
    ) != float(action[CONF_SERVICE_DATA].get(ATTR_TEMPERATURE)):
        return True
    if ATTR_CURRENT_TEMPERATURE in action[CONF_SERVICE_DATA] and float(
        state.attributes.get(ATTR_CURRENT_TEMPERATURE, 0) or 0
    ) != float(action[CONF_SERVICE_DATA].get(ATTR_CURRENT_TEMPERATURE)):
        return True
    if ATTR_TARGET_TEMP_LOW in action[CONF_SERVICE_DATA] and float(
        state.attributes.get(ATTR_TARGET_TEMP_LOW, 0) or 0
    ) != float(action[CONF_SERVICE_DATA].get(ATTR_TARGET_TEMP_LOW)):
        return True
    if ATTR_TARGET_TEMP_HIGH in action[CONF_SERVICE_DATA] and float(
        state.attributes.get(ATTR_TARGET_TEMP_HIGH, 0) or 0
    ) != float(action[CONF_SERVICE_DATA].get(ATTR_TARGET_TEMP_HIGH)):
        return True
    return False


def process_domain_handlers(service_call: Dict[str, Any]) -> List[Dict[str, Any]]:
    action = service_call.get(CONF_ACTION)
    if action in _HANDLERS:
        return _HANDLERS[action](service_call)
    return [service_call]


def avoid_redundant_action(action: Dict[str, Any], hass: HomeAssistant) -> bool:
    """Check if action has an effect on the entity"""
    if ATTR_ENTITY_ID not in action:
        return True

    action_name = action.get(CONF_ACTION)
    if action_name in _EFFECT_HANDLERS:
        return _EFFECT_HANDLERS[action_name](action, hass)

    return True
