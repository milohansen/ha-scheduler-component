"""Pytest configuration for scheduler tests."""
import pytest
import sys
from pathlib import Path

# Add the custom_components directory to the path
sys.path.insert(0, str(Path(__file__).parent / "custom_components"))


@pytest.fixture
def hass():
    """Provide a mock Home Assistant instance."""
    from unittest.mock import Mock, AsyncMock
    
    mock_hass = Mock()
    mock_hass.data = {}
    mock_hass.states = Mock()
    mock_hass.services = Mock()
    mock_hass.bus = Mock()
    mock_hass.bus.async_fire = AsyncMock()
    
    return mock_hass
