"""Tests for DashScope AI vision client — response parsing and prompt building."""

import json
import pytest
from backend.vision.ai import DashScopeClient, _parse_response, _build_prompt


class TestParseResponse:
    def test_valid_single_building(self):
        text = json.dumps({
            "buildings": [
                {"name": "Archer Tower", "x": 450, "y": 320, "cost": 800000, "resource": "gold"}
            ]
        })
        result = _parse_response(text)
        assert len(result) == 1
        assert result[0]["name"] == "Archer Tower"
        assert result[0]["x"] == 450
        assert result[0]["y"] == 320
        assert result[0]["cost"] == 800000
        assert result[0]["resource"] == "gold"

    def test_valid_multiple_buildings(self):
        text = json.dumps({
            "buildings": [
                {"name": "Cannon", "x": 400, "y": 200, "cost": 500000, "resource": "gold"},
                {"name": "Wizard Tower", "x": 600, "y": 300, "cost": 1200000, "resource": "elixir"},
            ]
        })
        result = _parse_response(text)
        assert len(result) == 2
        assert result[0]["name"] == "Cannon"
        assert result[1]["name"] == "Wizard Tower"

    def test_invalid_json_returns_none(self):
        assert _parse_response("not json at all") is None
        assert _parse_response("") is None
        assert _parse_response(None) is None

    def test_empty_buildings_list(self):
        text = '{"buildings": []}'
        result = _parse_response(text)
        assert result == []

    def test_out_of_bounds_coords_filtered(self):
        text = json.dumps({
            "buildings": [
                {"name": "Valid", "x": 500, "y": 300, "cost": 0},
                {"name": "TooFarX", "x": 9999, "y": 300, "cost": 0},
                {"name": "TooFarY", "x": 500, "y": 9999, "cost": 0},
                {"name": "Negative", "x": -10, "y": 300, "cost": 0},
            ]
        })
        result = _parse_response(text)
        assert len(result) == 1
        assert result[0]["name"] == "Valid"

    def test_markdown_wrapped_json(self):
        text = '```json\n{"buildings": [{"name": "Wall", "x": 100, "y": 100, "cost": 50000, "resource": "gold"}]}\n```'
        result = _parse_response(text)
        assert len(result) == 1
        assert result[0]["name"] == "Wall"

    def test_missing_buildings_key(self):
        assert _parse_response('{"other": "data"}') is None

    def test_buildings_not_a_list(self):
        assert _parse_response('{"buildings": "not a list"}') is None

    def test_building_missing_name(self):
        text = json.dumps({
            "buildings": [{"x": 500, "y": 300, "cost": 0}]
        })
        result = _parse_response(text)
        assert result == []  # filtered out

    def test_building_missing_coords(self):
        text = json.dumps({
            "buildings": [{"name": "No Coords", "cost": 0}]
        })
        result = _parse_response(text)
        assert result == []

    def test_coordinates_converted_to_int(self):
        text = json.dumps({
            "buildings": [{"name": "FloatTown", "x": 450.7, "y": 320.1, "cost": 0}]
        })
        result = _parse_response(text)
        assert result[0]["x"] == 450
        assert result[0]["y"] == 320

    def test_default_resource_gold(self):
        text = json.dumps({
            "buildings": [{"name": "Test", "x": 100, "y": 100, "cost": 0, "resource": "unknown"}]
        })
        result = _parse_response(text)
        assert result[0]["resource"] == "gold"

    def test_negative_cost_filtered(self):
        text = json.dumps({
            "buildings": [
                {"name": "BadCost", "x": 500, "y": 300, "cost": -100},
                {"name": "Good", "x": 400, "y": 200, "cost": 5000},
            ]
        })
        result = _parse_response(text)
        assert len(result) == 1
        assert result[0]["name"] == "Good"

    def test_capped_at_five(self):
        buildings = [{"name": f"B{i}", "x": 100, "y": 100 + i * 30, "cost": 0} for i in range(10)]
        text = json.dumps({"buildings": buildings})
        result = _parse_response(text)
        assert len(result) == 5


class TestBuildPrompt:
    def test_prompt_contains_required_keywords(self):
        prompt = _build_prompt()
        assert "1280x720" in prompt
        assert "Upgrade" in prompt
        assert "pixel coordinates" in prompt.lower()
        assert "buildings" in prompt
        assert "JSON" in prompt
        assert "0-1279" in prompt
        assert "0-719" in prompt


class TestDashScopeClient:
    def test_client_available_with_key(self):
        client = DashScopeClient("sk-test")
        assert client.available is True

    def test_client_not_available_without_key(self):
        client = DashScopeClient()
        assert client.available is False

    def test_client_not_available_with_none(self):
        client = DashScopeClient(None)
        assert client.available is False
