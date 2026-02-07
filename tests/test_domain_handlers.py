"""Tests for domain_handlers module."""
import pytest
from homeassistant.const import (
    CONF_ACTION,
    ATTR_ENTITY_ID,
    CONF_SERVICE_DATA,
)
from homeassistant.components.climate.const import (
    SERVICE_SET_TEMPERATURE,
    SERVICE_SET_HVAC_MODE,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    DOMAIN as CLIMATE_DOMAIN,
)

from custom_components.scheduler.domain_handlers import (
    ClimateDomainHandler,
    DefaultDomainHandler,
    DomainHandlerRegistry,
    get_domain_handler_registry,
)


class TestClimateDomainHandler:
    """Test the climate domain handler."""

    def test_can_handle_climate_set_temperature_with_hvac_mode(self):
        """Test handler recognizes climate.set_temperature with hvac_mode."""
        handler = ClimateDomainHandler()
        
        action = {
            CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}",
            ATTR_ENTITY_ID: "climate.living_room",
            CONF_SERVICE_DATA: {
                ATTR_HVAC_MODE: "heat",
                ATTR_CURRENT_TEMPERATURE: 20,
            }
        }
        
        assert handler.can_handle(action) is True

    def test_cannot_handle_climate_without_hvac_mode(self):
        """Test handler rejects climate.set_temperature without hvac_mode."""
        handler = ClimateDomainHandler()
        
        action = {
            CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}",
            ATTR_ENTITY_ID: "climate.living_room",
            CONF_SERVICE_DATA: {
                ATTR_CURRENT_TEMPERATURE: 20,
            }
        }
        
        assert handler.can_handle(action) is False

    def test_cannot_handle_climate_without_entity_id(self):
        """Test handler rejects action without entity_id."""
        handler = ClimateDomainHandler()
        
        action = {
            CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}",
            CONF_SERVICE_DATA: {
                ATTR_HVAC_MODE: "heat",
                ATTR_CURRENT_TEMPERATURE: 20,
            }
        }
        
        assert handler.can_handle(action) is False

    def test_process_splits_into_mode_wait_temperature(self, hass):
        """Test handler splits action into set_hvac_mode, wait, set_temperature."""
        handler = ClimateDomainHandler()
        
        action = {
            CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}",
            ATTR_ENTITY_ID: "climate.living_room",
            CONF_SERVICE_DATA: {
                ATTR_HVAC_MODE: "heat",
                ATTR_CURRENT_TEMPERATURE: 20,
            }
        }
        
        result = handler.process(action, hass)
        
        # Should return 3 service calls
        assert len(result) == 3
        
        # First: set hvac mode
        assert result[0][CONF_ACTION] == f"{CLIMATE_DOMAIN}.{SERVICE_SET_HVAC_MODE}"
        assert result[0][CONF_SERVICE_DATA][ATTR_HVAC_MODE] == "heat"
        
        # Second: wait for state change
        assert result[1][CONF_ACTION] == "wait_state_change"
        
        # Third: set temperature (without hvac_mode)
        assert result[2][CONF_ACTION] == f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}"
        assert ATTR_HVAC_MODE not in result[2][CONF_SERVICE_DATA]
        assert result[2][CONF_SERVICE_DATA][ATTR_CURRENT_TEMPERATURE] == 20

    def test_process_mode_only_no_temperature(self, hass):
        """Test handler with only mode change, no temperature."""
        handler = ClimateDomainHandler()
        
        action = {
            CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}",
            ATTR_ENTITY_ID: "climate.living_room",
            CONF_SERVICE_DATA: {
                ATTR_HVAC_MODE: "off",
            }
        }
        
        result = handler.process(action, hass)
        
        # Should return only 1 service call (no temperature to set)
        assert len(result) == 1
        assert result[0][CONF_ACTION] == f"{CLIMATE_DOMAIN}.{SERVICE_SET_HVAC_MODE}"


class TestDefaultDomainHandler:
    """Test the default domain handler."""

    def test_can_handle_anything(self):
        """Test default handler accepts any action."""
        handler = DefaultDomainHandler()
        
        action = {CONF_ACTION: "light.turn_on"}
        assert handler.can_handle(action) is True
        
        action = {CONF_ACTION: "switch.toggle"}
        assert handler.can_handle(action) is True

    def test_process_returns_action_unchanged(self, hass):
        """Test default handler returns action as-is in a list."""
        handler = DefaultDomainHandler()
        
        action = {
            CONF_ACTION: "light.turn_on",
            ATTR_ENTITY_ID: "light.kitchen",
            CONF_SERVICE_DATA: {"brightness": 255}
        }
        
        result = handler.process(action, hass)
        
        assert len(result) == 1
        assert result[0] == action


class TestDomainHandlerRegistry:
    """Test the domain handler registry."""

    def test_registry_initialization(self):
        """Test registry initializes with climate handler."""
        registry = DomainHandlerRegistry()
        
        # Should have at least the climate handler registered
        assert len(registry._handlers) >= 1

    def test_process_action_uses_climate_handler(self, hass):
        """Test registry routes climate actions to climate handler."""
        registry = DomainHandlerRegistry()
        
        action = {
            CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}",
            ATTR_ENTITY_ID: "climate.living_room",
            CONF_SERVICE_DATA: {
                ATTR_HVAC_MODE: "heat",
                ATTR_CURRENT_TEMPERATURE: 20,
            }
        }
        
        result = registry.process_action(action, hass)
        
        # Should be processed by climate handler (3 calls)
        assert len(result) == 3

    def test_process_action_uses_default_handler(self, hass):
        """Test registry routes non-special actions to default handler."""
        registry = DomainHandlerRegistry()
        
        action = {
            CONF_ACTION: "light.turn_on",
            ATTR_ENTITY_ID: "light.kitchen",
        }
        
        result = registry.process_action(action, hass)
        
        # Should be processed by default handler (1 call)
        assert len(result) == 1
        assert result[0] == action

    def test_get_global_registry(self):
        """Test global registry singleton."""
        registry1 = get_domain_handler_registry()
        registry2 = get_domain_handler_registry()
        
        # Should return the same instance
        assert registry1 is registry2


# Fixtures
@pytest.fixture
def hass():
    """Mock Home Assistant instance."""
    class MockHass:
        pass
    return MockHass()
