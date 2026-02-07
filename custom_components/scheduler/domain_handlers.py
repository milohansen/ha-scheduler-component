"""Domain-specific action handlers for the scheduler component."""

import logging
from typing import Protocol

from homeassistant.core import HomeAssistant
from homeassistant.const import (
    CONF_ACTION,
    ATTR_ENTITY_ID,
    CONF_SERVICE_DATA,
    CONF_DELAY,
    CONF_STATE,
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

_LOGGER = logging.getLogger(__name__)

ACTION_WAIT_STATE_CHANGE = "wait_state_change"


class DomainHandler(Protocol):
    """Protocol for domain-specific action handlers."""

    def can_handle(self, action: dict) -> bool:
        """Return True if this handler can process the action."""
        ...

    def process(self, action: dict, hass: HomeAssistant) -> list[dict]:
        """Process the action and return a list of service calls."""
        ...


class ClimateDomainHandler:
    """Handler for climate domain actions.

    Handles the special case where climate integrations don't support
    setting hvac_mode and temperature together. Splits the service call
    into multiple steps with state change waiting.
    """

    def can_handle(self, action: dict) -> bool:
        """Check if this is a climate.set_temperature call with hvac_mode."""
        return (
            action.get(CONF_ACTION) == f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}"
            and ATTR_HVAC_MODE in action.get(CONF_SERVICE_DATA, {})
            and ATTR_ENTITY_ID in action
        )

    def process(self, action: dict, hass: HomeAssistant) -> list[dict]:
        """Split climate service call into mode change, wait, and temperature set.

        This fixes climate integrations which:
        - Don't support setting hvac_mode and temperature together
        - Have long processing time requiring delay between calls
        - Lose setpoint after switching hvac_mode
        """
        service_calls = [
            {
                CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_HVAC_MODE}",
                ATTR_ENTITY_ID: action[ATTR_ENTITY_ID],
                CONF_SERVICE_DATA: {
                    ATTR_HVAC_MODE: action[CONF_SERVICE_DATA][ATTR_HVAC_MODE]
                },
            }
        ]

        # Only add wait and temperature set if temperature parameters are present
        if (
            ATTR_CURRENT_TEMPERATURE in action[CONF_SERVICE_DATA]
            or ATTR_TARGET_TEMP_LOW in action[CONF_SERVICE_DATA]
            or ATTR_TARGET_TEMP_HIGH in action[CONF_SERVICE_DATA]
        ):
            service_calls.extend(
                [
                    {
                        CONF_ACTION: ACTION_WAIT_STATE_CHANGE,
                        ATTR_ENTITY_ID: action[ATTR_ENTITY_ID],
                        CONF_SERVICE_DATA: {
                            CONF_DELAY: 50,
                            CONF_STATE: action[CONF_SERVICE_DATA][ATTR_HVAC_MODE],
                        },
                    },
                    {
                        CONF_ACTION: f"{CLIMATE_DOMAIN}.{SERVICE_SET_TEMPERATURE}",
                        ATTR_ENTITY_ID: action[ATTR_ENTITY_ID],
                        CONF_SERVICE_DATA: {
                            k: v
                            for k, v in action[CONF_SERVICE_DATA].items()
                            if k != ATTR_HVAC_MODE
                        },
                    },
                ]
            )

        _LOGGER.debug(
            "Climate handler split action for %s into %d service calls",
            action[ATTR_ENTITY_ID],
            len(service_calls),
        )
        return service_calls


class DefaultDomainHandler:
    """Default handler for actions that don't need special processing."""

    def can_handle(self, action: dict) -> bool:
        """Can handle any action."""
        return True

    def process(self, action: dict, hass: HomeAssistant) -> list[dict]:
        """Return action as-is in a list."""
        return [action]


class DomainHandlerRegistry:
    """Registry for domain-specific action handlers."""

    def __init__(self) -> None:
        """Initialize the registry."""
        self._handlers: list[DomainHandler] = []
        self._default_handler = DefaultDomainHandler()

        # Register built-in handlers
        self.register(ClimateDomainHandler())

    def register(self, handler: DomainHandler) -> None:
        """Register a domain handler.

        Handlers are checked in registration order, so register more
        specific handlers before generic ones.
        """
        self._handlers.append(handler)
        _LOGGER.debug("Registered domain handler: %s", handler.__class__.__name__)

    def process_action(self, action: dict, hass: HomeAssistant) -> list[dict]:
        """Process an action through registered handlers.

        Returns a list of service calls to execute. Most actions return
        a single-item list, but some handlers may split actions into
        multiple service calls.
        """
        for handler in self._handlers:
            if handler.can_handle(action):
                return handler.process(action, hass)

        # Fall back to default handler
        return self._default_handler.process(action, hass)


# Global registry instance
_registry: DomainHandlerRegistry | None = None


def get_domain_handler_registry() -> DomainHandlerRegistry:
    """Get the global domain handler registry."""
    global _registry
    if _registry is None:
        _registry = DomainHandlerRegistry()
    return _registry
