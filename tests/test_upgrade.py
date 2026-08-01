"""Tests for upgrade queue REST API."""

import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.db.database import get_session, init_db
from backend.db.models import UpgradeQueue


@pytest.fixture
def client():
    """Create TestClient and clean upgrade queue before each test."""
    init_db()
    with get_session() as session:
        session.query(UpgradeQueue).delete()
        session.commit()
    return TestClient(app)


def test_create_and_list_queue(client: TestClient):
    """Create items and list them in priority order."""
    r1 = client.post("/api/v1/upgrade/queue", json={
        "name": "Archer Tower", "target_level": 12, "resource_type": "gold"
    })
    assert r1.status_code == 200
    assert r1.json()["priority_order"] == 1

    r2 = client.post("/api/v1/upgrade/queue", json={
        "name": "Wizard Tower", "target_level": 10, "resource_type": "elixir"
    })
    assert r2.status_code == 200
    assert r2.json()["priority_order"] == 2

    items = client.get("/api/v1/upgrade/queue").json()
    assert len(items) == 2
    assert items[0]["name"] == "Archer Tower"
    assert items[1]["name"] == "Wizard Tower"


def test_update_item_status(client: TestClient):
    """Update an item's status."""
    r = client.post("/api/v1/upgrade/queue", json={
        "name": "Test Building", "target_level": 5, "resource_type": "gold"
    })
    item_id = r.json()["id"]

    r2 = client.patch(f"/api/v1/upgrade/queue/{item_id}/status?status=IN_PROGRESS&cost=800000")
    assert r2.status_code == 200

    updated = client.get("/api/v1/upgrade/queue").json()
    item = [i for i in updated if i["id"] == item_id][0]
    assert item["status"] == "IN_PROGRESS"
    assert item["cost"] == 800000
    assert item["started_at"] is not None


def test_delete_item(client: TestClient):
    """Delete an item."""
    r = client.post("/api/v1/upgrade/queue", json={
        "name": "To Delete", "target_level": 3, "resource_type": "elixir"
    })
    item_id = r.json()["id"]
    items_before = client.get("/api/v1/upgrade/queue").json()

    r2 = client.delete(f"/api/v1/upgrade/queue/{item_id}")
    assert r2.status_code == 200

    items_after = client.get("/api/v1/upgrade/queue").json()
    assert len(items_after) == len(items_before) - 1


def test_upgrade_status(client: TestClient):
    """Status endpoint returns counts."""
    r = client.get("/api/v1/upgrade/status")
    assert r.status_code == 200
    data = r.json()
    assert "pending" in data
    assert "in_progress" in data
    assert "completed" in data
    assert "total" in data
