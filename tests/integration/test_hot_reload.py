"""Integration tests for hot-reload functionality."""
import asyncio
import tempfile
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_shared_watcher_detects_file_change():
    """Test shared HotReloadWatcher detects file changes and passes file path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "test.yaml"
        config_file.write_text("key: value1\n")
        
        changes_detected: list[str] = []
        
        async def on_change(file_path: str):
            changes_detected.append(file_path)
        
        from universal_hot_reload import HotReloadWatcher
        
        watcher = HotReloadWatcher(
            name="test",
            watch_path=config_file.parent,  # Watch directory
            on_change=on_change,
            debounce_ms=100,  # Fast debounce for testing
            recursive=False,
            patterns=[".yaml"],
        )
        
        assert await watcher.start(), "Watcher failed to start"
        
        try:
            # Wait for watcher to be ready
            await asyncio.sleep(0.2)
            
            # Modify file
            config_file.write_text("key: value2\n")
            
            # Wait for debounce + callback
            await asyncio.sleep(0.4)
            
            assert len(changes_detected) == 1, f"Expected 1 change, got {len(changes_detected)}"
            assert str(config_file) in changes_detected[0], "File path not in callback"
            
        finally:
            await watcher.stop()
        
        print("✅ Shared watcher integration test passed")


@pytest.mark.asyncio
async def test_watcher_debouncing():
    """Test that rapid changes are debounced."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "test.yaml"
        config_file.write_text("key: value1\n")
        
        callback_count = 0
        
        async def on_change(file_path: str):
            nonlocal callback_count
            callback_count += 1
        
        from universal_hot_reload import HotReloadWatcher
        
        watcher = HotReloadWatcher(
            name="test-debounce",
            watch_path=config_file.parent,
            on_change=on_change,
            debounce_ms=300,  # Longer debounce for more reliable test
            patterns=[".yaml"],
        )
        
        assert await watcher.start()
        
        try:
            # Wait for watcher to be ready
            await asyncio.sleep(0.2)
            
            # Make rapid changes in a tight loop (no delays)
            # This should be detected as a single batch by watchfiles
            for i in range(5):
                config_file.write_text(f"key: value{i}\n")
            
            # Wait for watchfiles to detect changes + debounce to complete
            await asyncio.sleep(0.8)
            
            # Should have few callbacks (changes batched and debounced)
            # Accept 1-2 callbacks as watchfiles may batch differently
            assert callback_count <= 2, f"Expected 1-2 callbacks (debounced), got {callback_count}"
            
        finally:
            await watcher.stop()
        
        print("✅ Debouncing test passed")


@pytest.mark.asyncio  
async def test_watcher_pattern_filtering():
    """Test that only matching patterns trigger callbacks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yaml_file = Path(tmpdir) / "config.yaml"
        txt_file = Path(tmpdir) / "notes.txt"
        yaml_file.write_text("yaml: true\n")
        txt_file.write_text("text content\n")
        
        changes_detected: list[str] = []
        
        async def on_change(file_path: str):
            changes_detected.append(file_path)
        
        from universal_hot_reload import HotReloadWatcher
        
        watcher = HotReloadWatcher(
            name="test-patterns",
            watch_path=tmpdir,
            on_change=on_change,
            debounce_ms=100,
            patterns=[".yaml"],  # Only YAML
        )
        
        assert await watcher.start()
        
        try:
            await asyncio.sleep(0.2)
            
            # Modify txt file (should be ignored)
            txt_file.write_text("updated text\n")
            await asyncio.sleep(0.3)
            
            # Modify yaml file (should trigger)
            yaml_file.write_text("yaml: updated\n")
            await asyncio.sleep(0.3)
            
            assert len(changes_detected) == 1, f"Expected 1 change (yaml only), got {len(changes_detected)}"
            assert "config.yaml" in changes_detected[0]
            
        finally:
            await watcher.stop()
        
        print("✅ Pattern filtering test passed")


@pytest.mark.asyncio
async def test_watcher_graceful_stop():
    """Test that watcher stops gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_file = Path(tmpdir) / "test.yaml"
        config_file.write_text("key: value\n")
        
        async def on_change(file_path: str):
            pass
        
        from universal_hot_reload import HotReloadWatcher
        
        watcher = HotReloadWatcher(
            name="test-stop",
            watch_path=tmpdir,
            on_change=on_change,
            debounce_ms=100,
        )
        
        assert await watcher.start()
        assert watcher.get_status()["enabled"]
        
        await watcher.stop()
        
        status = watcher.get_status()
        assert not status["enabled"]
        assert not status["watching"]
        
        print("✅ Graceful stop test passed")


if __name__ == "__main__":
    print("Running hot-reload integration tests...\n")
    
    try:
        asyncio.run(test_shared_watcher_detects_file_change())
        asyncio.run(test_watcher_debouncing())
        asyncio.run(test_watcher_pattern_filtering())
        asyncio.run(test_watcher_graceful_stop())
        print("\n✅ All runtime integration tests passed")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        exit(1)
