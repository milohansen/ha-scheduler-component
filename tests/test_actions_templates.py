"""Tests for template rendering and area expansion."""

from custom_components.scheduler import const
from custom_components.scheduler.actions import (
    _expand_area_actions,
    _render_service_data,
)


def test_render_service_data_simple(hass):
    data = {"temperature": "{{ 1 + 1 }}", "mode": "heat"}
    result = _render_service_data(data, hass)
    assert isinstance(result, dict)
    assert result["temperature"] == "2"
    assert result["mode"] == "heat"


def test_render_service_data_nested(hass):
    data = {"list": ["{{ 2 * 3 }}"], "nested": {"v": "{{ 5 }}"}}
    result = _render_service_data(data, hass)
    assert isinstance(result, dict)
    assert result["list"][0] == "6"
    assert result["nested"]["v"] == "5" # type: ignore


def test_expand_area_actions(hass, monkeypatch):
    class _AreaReg:
        def __init__(self):
            self.areas = {"area1": object()}

    class _Entry:
        def __init__(self, entity_id, area_id):
            self.entity_id = entity_id
            self.area_id = area_id

    class _EntityReg:
        def __init__(self):
            self.entities = {
                "light.one": _Entry("light.one", "area1"),
                "light.two": _Entry("light.two", "area1"),
            }

    monkeypatch.setattr(
        "custom_components.scheduler.actions.ar.async_get",
        lambda hass: _AreaReg(),
    )
    monkeypatch.setattr(
        "custom_components.scheduler.actions.er.async_get",
        lambda hass: _EntityReg(),
    )

    actions = [
        {const.ATTR_AREA_ID: "area1", "service": "light.turn_on", "service_data": {}}
    ]
    expanded = _expand_area_actions(hass, actions)
    assert len(expanded) == 2
    assert all(a.get(const.ATTR_AREA_ID) is None for a in expanded)
    assert {a["entity_id"] for a in expanded} == {"light.one", "light.two"}
