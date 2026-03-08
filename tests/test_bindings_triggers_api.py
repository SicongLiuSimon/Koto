#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Comprehensive CRUD API tests for skill bindings and trigger registry.

Covers:
  1. Skill bindings bootstrap (POST /api/skills/bindings/bootstrap)
  2. Skill bindings list     (GET  /api/skills/bindings)
  3. Custom intent binding   (POST /api/skills/<id>/bindings/intent)
  4. Toggle binding          (POST /api/skills/bindings/<id>/toggle)
  5. Delete binding          (DELETE /api/skills/bindings/<id>)
  6. Trigger templates list  (GET  /api/jobs/triggers/templates)
  7. Trigger bootstrap       (POST /api/jobs/triggers/bootstrap)
  8. Trigger list            (GET  /api/jobs/triggers)
  9. Toggle trigger          (PATCH /api/jobs/triggers/<id>)
 10. Create custom trigger   (POST /api/jobs/triggers)
 11. Delete trigger          (DELETE /api/jobs/triggers/<id>)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from flask import Flask


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_app() -> Flask:
    root = _root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    app = Flask(__name__)

    from app.api.skill_routes import skill_bp
    from app.api.job_routes import job_bp

    app.register_blueprint(skill_bp)
    app.register_blueprint(job_bp)
    return app


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _check(resp, ok_status=(200, 201)):
    body = resp.get_data(as_text=True)
    assert resp.status_code in ok_status, f"HTTP {resp.status_code}: {body[:300]}"
    return resp.get_json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_bindings_bootstrap(client):
    """Bootstrap seeds all 9 recommended intent bindings."""
    data = _check(client.post("/api/skills/bindings/bootstrap", json={"force": True}))
    assert data["success"], data
    created = data.get("created", [])
    assert len(created) >= 9, f"Expected ≥9, got {len(created)}: {created}"
    print(f"  bootstrap created: {created}")


def test_bindings_list_after_bootstrap(client):
    """List endpoint returns all seeded bindings."""
    # ensure bootstrapped
    client.post("/api/skills/bindings/bootstrap", json={"force": False})

    data = _check(client.get("/api/skills/bindings?binding_type=intent"))
    assert data["success"], data
    count = data["count"]
    assert count >= 9, f"Expected ≥9, got {count}"
    print(f"  bindings count: {count}")
    # Every binding should have required fields
    for b in data["bindings"]:
        assert "binding_id" in b
        assert "skill_id" in b
        assert "intent_patterns" in b
        assert "enabled" in b


def test_create_custom_intent_binding(client):
    """Create a new intent binding for 'concise_mode' with custom patterns."""
    resp = client.post(
        "/api/skills/concise_mode/bindings/intent",
        json={"patterns": ["请简短回答", "一行内", "极简"], "auto_disable_after_turns": 1},
    )
    data = _check(resp, ok_status=(201,))
    assert data["success"], data
    b = data["binding"]
    assert b["skill_id"] == "concise_mode"
    assert "请简短回答" in b["intent_patterns"]
    print(f"  created binding: {b['binding_id']} patterns={b['intent_patterns']}")
    return b["binding_id"]


def test_toggle_binding(client, binding_id):
    """Disable then re-enable a binding."""
    # disable
    data = _check(client.post(
        f"/api/skills/bindings/{binding_id}/toggle",
        json={"enabled": False},
    ))
    assert data["success"], data
    assert data["binding"]["enabled"] is False

    # re-enable
    data = _check(client.post(
        f"/api/skills/bindings/{binding_id}/toggle",
        json={"enabled": True},
    ))
    assert data["binding"]["enabled"] is True
    print(f"  toggle binding OK: {binding_id}")


def test_delete_binding(client, binding_id):
    """Delete a binding by ID."""
    data = _check(client.delete(f"/api/skills/bindings/{binding_id}"))
    assert data["success"], data
    assert data["deleted"] == binding_id

    # Confirm it's gone
    list_data = _check(client.get("/api/skills/bindings"))
    ids = [b["binding_id"] for b in list_data["bindings"]]
    assert binding_id not in ids, "Binding still present after delete"
    print(f"  deleted binding: {binding_id}")


def test_trigger_templates(client):
    """GET /api/jobs/triggers/templates returns ≥3 templates."""
    data = _check(client.get("/api/jobs/triggers/templates"))
    assert data["ok"], data
    assert data["total"] >= 3, f"Expected ≥3, got {data['total']}"
    for t in data["data"]:
        assert "name" in t
        assert "trigger_type" in t
    print(f"  templates: {[t['name'] for t in data['data']]}")


def test_triggers_bootstrap(client):
    """Bootstrap seeds 3 recommended triggers."""
    data = _check(client.post("/api/jobs/triggers/bootstrap", json={"force": True}))
    assert data["ok"], data
    created = data["data"].get("created", [])
    assert len(created) >= 3, f"Expected ≥3, got {len(created)}: {created}"
    print(f"  trigger bootstrap created: {created}")


def test_triggers_list_after_bootstrap(client):
    """List endpoint returns all seeded triggers."""
    client.post("/api/jobs/triggers/bootstrap", json={"force": False})

    data = _check(client.get("/api/jobs/triggers"))
    assert data["ok"], data
    assert data["total"] >= 3, f"Expected ≥3, got {data['total']}"
    for t in data["data"]:
        assert "trigger_id" in t
        assert "name" in t
        assert "trigger_type" in t
        assert "enabled" in t
    print(f"  triggers: {[(t['name'], t['trigger_type']) for t in data['data']]}")


def test_toggle_trigger(client):
    """PATCH /api/jobs/triggers/<id> enables/disables a trigger."""
    # Get first trigger
    list_data = _check(client.get("/api/jobs/triggers"))
    triggers = list_data["data"]
    assert triggers, "No triggers to toggle"
    t = triggers[0]
    tid = t["trigger_id"]
    orig = t["enabled"]

    # Toggle off
    data = _check(client.patch(f"/api/jobs/triggers/{tid}", json={"enabled": not orig}))
    assert data["ok"], data
    assert data["data"]["enabled"] == (not orig)

    # Toggle back
    data = _check(client.patch(f"/api/jobs/triggers/{tid}", json={"enabled": orig}))
    assert data["data"]["enabled"] == orig
    print(f"  toggle trigger OK: {tid}")


def test_create_and_delete_custom_trigger(client):
    """Create a webhook trigger and then delete it."""
    resp = client.post("/api/jobs/triggers", json={
        "name": "测试 Webhook 触发器",
        "trigger_type": "webhook",
        "job_type": "agent_query",
        "job_payload": {"query": "ping"},
        "enabled": False,
    })
    data = _check(resp, ok_status=(201,))
    assert data["ok"], data
    tid = data["data"]["trigger_id"]
    assert data["data"]["trigger_type"] == "webhook"
    print(f"  created trigger: {tid}")

    # Delete it
    del_data = _check(client.delete(f"/api/jobs/triggers/{tid}"))
    assert del_data["ok"], del_data
    print(f"  deleted trigger: {tid}")

    # Confirm gone
    list_data = _check(client.get("/api/jobs/triggers"))
    ids = [t["trigger_id"] for t in list_data["data"]]
    assert tid not in ids, "Trigger still present after delete"


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> int:
    tmp = tempfile.mkdtemp(prefix="koto_api_test_")
    try:
        os.environ["KOTO_DB_DIR"] = tmp
        app = build_app()
        client = app.test_client()

        print("=" * 60)
        print("Bindings & Triggers API Test Suite")
        print("=" * 60)

        passed = 0
        failed = 0

        def run(fn, *args):
            nonlocal passed, failed
            name = fn.__name__
            try:
                result = fn(client, *args)
                print(f"  ✅ {name}")
                passed += 1
                return result
            except Exception as e:
                print(f"  ❌ {name}: {e}")
                failed += 1
                return None

        # Binding tests (ordered — some depend on prior state)
        run(test_bindings_bootstrap)
        run(test_bindings_list_after_bootstrap)
        bid = run(test_create_custom_intent_binding)
        if bid:
            run(test_toggle_binding, bid)
            run(test_delete_binding, bid)

        # Trigger tests
        run(test_trigger_templates)
        run(test_triggers_bootstrap)
        run(test_triggers_list_after_bootstrap)
        run(test_toggle_trigger)
        run(test_create_and_delete_custom_trigger)

        print("-" * 60)
        print(f"Results: {passed} passed, {failed} failed")
        if failed == 0:
            print("\nAll API tests passed. ✅")
        else:
            print(f"\n{failed} test(s) FAILED. ❌")
        return 0 if failed == 0 else 1

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
