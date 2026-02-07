import pytest
from custom_components.scheduler.domain_handlers import process_domain_handlers
from homeassistant.components.climate.const import (
    SERVICE_SET_TEMPERATURE,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import (
    CONF_ACTION,
    CONF_SERVICE_DATA,
    ATTR_ENTITY_ID,
)

def test_climate_handler_expansion():
    """Test that setting temperature with HVAC mode expands to multiple calls."""
    service_call = {
        CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}",
        ATTR_ENTITY_ID: "climate.test",
        CONF_SERVICE_DATA: {
            ATTR_HVAC_MODE: "heat",
            "temperature": 22,
        }
    }

    result = process_domain_handlers(service_call)

    assert len(result) == 3
    assert result[0][CONF_ACTION] == f"{CLIMATE_DOMAIN}.set_hvac_mode"
    assert result[1][CONF_ACTION] == "wait_state_change"
    assert result[2][CONF_ACTION] == f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}"
    assert ATTR_HVAC_MODE not in result[2][CONF_SERVICE_DATA]

def test_generic_handler_pass_through():
    """Test that unknown handlers pass through unchanged."""
    service_call = {
        CONF_ACTION: "light.turn_on",
        ATTR_ENTITY_ID: "light.test",
        CONF_SERVICE_DATA: {"brightness": 255}
    }

    result = process_domain_handlers(service_call)

    assert len(result) == 1
    assert result[0] == service_call
