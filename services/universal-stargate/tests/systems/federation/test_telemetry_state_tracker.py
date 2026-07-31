"""Unit tests for TelemetryStateTracker."""

import pytest
from datetime import datetime

from systems.federation.remote.telemetry.state_tracker import (
    TelemetryStateTracker,
    TRACKED_FIELDS,
)


class TestTelemetryStateTracker:
    """Tests for TelemetryStateTracker delta computation."""
    
    def test_initial_delta_includes_all_fields(self):
        """First delta should include all non-empty fields."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        initial_state = {
            "loaded_models": ["model-a", "model-b"],
            "busy_models": ["model-a"],
            "active_requests": 2,
            "vram_free_mb": 8000,
            "ram_free_mb": 16000,
        }
        
        tracker.update(initial_state)
        delta = tracker.get_delta()
        
        # First delta should have all fields + sequence_number
        assert delta["sequence_number"] == 1
        assert delta["loaded_models"] == ["model-a", "model-b"]
        assert delta["busy_models"] == ["model-a"]
        assert delta["active_requests"] == 2
        assert delta["vram_free_mb"] == 8000
        assert delta["ram_free_mb"] == 16000
    
    def test_empty_delta_only_sequence_number(self):
        """Empty delta should only contain sequence_number."""
        tracker = TelemetryStateTracker(node_id="test-node")

        # Set initial state
        tracker.update({"loaded_models": [], "busy_models": []})
        tracker.get_delta()  # Consume first delta

        # No state change
        tracker.update({"loaded_models": [], "busy_models": []})
        delta2 = tracker.get_delta()

        assert delta2["sequence_number"] == 2
        assert len(delta2) == 1  # Only sequence_number
        assert "loaded_models" not in delta2
        assert "busy_models" not in delta2
    
    def test_sequence_numbers_increment_monotonically(self):
        """Sequence numbers should increment on every get_delta()."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        for i in range(1, 11):
            tracker.update({"active_requests": i})
            delta = tracker.get_delta()
            assert delta["sequence_number"] == i
    
    def test_sequence_increments_even_on_empty_delta(self):
        """Sequence number should increment even if no changes."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        tracker.update({"active_requests": 1})
        delta1 = tracker.get_delta()
        assert delta1["sequence_number"] == 1
        
        # No change
        tracker.update({"active_requests": 1})
        delta2 = tracker.get_delta()
        assert delta2["sequence_number"] == 2
        assert "active_requests" not in delta2  # No change
    
    def test_only_changed_fields_in_delta(self):
        """Only changed fields should appear in delta."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        # Initial state
        tracker.update({
            "active_requests": 1,
            "vram_free_mb": 8000,
            "ram_free_mb": 16000,
        })
        tracker.get_delta()  # Consume first delta
        
        # Change only active_requests
        tracker.update({
            "active_requests": 2,
            "vram_free_mb": 8000,  # Unchanged
            "ram_free_mb": 16000,  # Unchanged
        })
        delta = tracker.get_delta()
        
        assert "active_requests" in delta
        assert delta["active_requests"] == 2
        assert "vram_free_mb" not in delta
        assert "ram_free_mb" not in delta
    
    def test_list_delta_added_removed_format(self):
        """Small list changes should use added/removed format."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        # Initial state
        tracker.update({"loaded_models": ["model-a", "model-b"]})
        tracker.get_delta()  # Consume first delta
        
        # Add one, remove one
        tracker.update({"loaded_models": ["model-b", "model-c"]})
        delta = tracker.get_delta()
        
        assert "loaded_models" in delta
        assert isinstance(delta["loaded_models"], dict)
        assert set(delta["loaded_models"]["added"]) == {"model-c"}
        assert set(delta["loaded_models"]["removed"]) == {"model-a"}
    
    def test_list_delta_full_list_format(self):
        """Large list changes should use full list format."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        # Initial state: 10 models
        initial = [f"model-{i}" for i in range(10)]
        tracker.update({"loaded_models": initial})
        tracker.get_delta()  # Consume first delta
        
        # Replace all 10 models (delta size = 20, full size = 10)
        new_models = [f"new-model-{i}" for i in range(10)]
        tracker.update({"loaded_models": new_models})
        delta = tracker.get_delta()
        
        # Should use full list format (not added/removed)
        assert "loaded_models" in delta
        assert isinstance(delta["loaded_models"], list)
        assert set(delta["loaded_models"]) == set(new_models)
    
    def test_critical_events_included_in_delta(self):
        """Critical events should be included in next delta."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        # Add critical event
        tracker.add_critical_event("MODEL_LOADED", {"model_id": "model-a"})
        
        delta = tracker.get_delta()
        
        assert "critical_events" in delta
        assert len(delta["critical_events"]) == 1
        assert delta["critical_events"][0]["event"] == "MODEL_LOADED"
        assert delta["critical_events"][0]["model_id"] == "model-a"
        assert "timestamp" in delta["critical_events"][0]
    
    def test_critical_events_cleared_after_get_delta(self):
        """Critical events should be cleared after get_delta()."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        tracker.add_critical_event("MODEL_LOADED", {"model_id": "model-a"})
        delta1 = tracker.get_delta()
        assert "critical_events" in delta1
        
        # Next delta should not have critical events
        delta2 = tracker.get_delta()
        assert "critical_events" not in delta2
    
    def test_empty_to_empty_list_produces_no_delta(self):
        """Empty list to empty list should produce no delta."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        tracker.update({"loaded_models": []})
        tracker.get_delta()  # Consume first delta
        
        tracker.update({"loaded_models": []})
        delta = tracker.get_delta()
        
        assert "loaded_models" not in delta
        assert len(delta) == 1  # Only sequence_number
    
    def test_full_snapshot_includes_all_state(self):
        """get_full_snapshot() should return complete state."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        state = {
            "loaded_models": ["model-a"],
            "busy_models": [],
            "active_requests": 1,
            "vram_free_mb": 8000,
            "ram_free_mb": 16000,
        }
        
        tracker.update(state)
        snapshot = tracker.get_full_snapshot()
        
        assert snapshot["loaded_models"] == ["model-a"]
        assert snapshot["busy_models"] == []
        assert snapshot["active_requests"] == 1
        assert snapshot["vram_free_mb"] == 8000
        assert snapshot["ram_free_mb"] == 16000
        assert "sequence_number" in snapshot
    
    def test_full_snapshot_merges_previous_state(self):
        """get_full_snapshot merges fields from previous state."""
        tracker = TelemetryStateTracker(node_id="test-node")

        # Set initial state with field
        tracker.update({"active_requests": 1, "vram_free_mb": 8000})
        tracker.get_delta()  # Moves to previous_state

        # Update with partial state (missing vram_free_mb)
        tracker.update({"active_requests": 2})

        snapshot = tracker.get_full_snapshot()
        assert snapshot["active_requests"] == 2  # From current
        assert snapshot["vram_free_mb"] == 8000  # From previous
    
    def test_list_delta_threshold_boundary(self):
        """Test list delta at exactly 50% change threshold."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        # Initial state: 4 models
        tracker.update({"loaded_models": ["a", "b", "c", "d"]})
        tracker.get_delta()  # Consume first delta
        
        # Replace 2: delta_size=4 (2 added + 2 removed), full_size=4
        # 4 < 4 * 0.5 → False, should use full list
        tracker.update({"loaded_models": ["c", "d", "e", "f"]})
        delta = tracker.get_delta()
        
        assert isinstance(delta["loaded_models"], list)
        assert set(delta["loaded_models"]) == {"c", "d", "e", "f"}
        
        # Reset with 5 models
        tracker.update({"loaded_models": ["a", "b", "c", "d", "e"]})
        tracker.get_delta()
        
        # Replace 2: delta_size=4, full_size=5
        # 4 < 5 * 0.5 (2.5) → True, should use added/removed
        tracker.update({"loaded_models": ["c", "d", "e", "f", "g"]})
        delta = tracker.get_delta()
        
        assert isinstance(delta["loaded_models"], dict)
        assert set(delta["loaded_models"]["added"]) == {"f", "g"}
        assert set(delta["loaded_models"]["removed"]) == {"a", "b"}
    
    def test_node_id_property(self):
        """node_id property should return initialization value."""
        tracker = TelemetryStateTracker(node_id="my-node-123")
        assert tracker.node_id == "my-node-123"
    
    def test_timestamp_format(self):
        """timestamp should be ISO 8601 with Z suffix."""
        tracker = TelemetryStateTracker(node_id="test")
        ts = tracker.timestamp
        assert ts.endswith("Z")
        assert "T" in ts
        # Verify parseable
        datetime.fromisoformat(ts.rstrip("Z"))
    
    def test_untracked_fields_ignored(self):
        """Fields not in TRACKED_FIELDS should not appear in delta."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        tracker.update({
            "unknown_field": 123,
            "another_unknown": "value",
            "active_requests": 1,
        })
        delta = tracker.get_delta()
        
        assert "active_requests" in delta
        assert "unknown_field" not in delta
        assert "another_unknown" not in delta
    
    def test_multiple_critical_events_in_one_delta(self):
        """Multiple critical events should all be included."""
        tracker = TelemetryStateTracker(node_id="test-node")
        
        tracker.add_critical_event("MODEL_LOADED", {"model_id": "model-a"})
        tracker.add_critical_event("MODEL_LOADED", {"model_id": "model-b"})
        tracker.add_critical_event("MODEL_UNLOADED", {"model_id": "model-c"})
        
        delta = tracker.get_delta()
        
        assert "critical_events" in delta
        assert len(delta["critical_events"]) == 3
        assert delta["critical_events"][0]["event"] == "MODEL_LOADED"
        assert delta["critical_events"][1]["event"] == "MODEL_LOADED"
        assert delta["critical_events"][2]["event"] == "MODEL_UNLOADED"
