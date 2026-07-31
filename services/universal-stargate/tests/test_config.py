import pytest
import yaml
import os
from unittest.mock import patch, mock_open
from systems.proxy.resource_management import (
    ResourceManagementConfig,
    ResourceManagementConfigError,
    GatewayConfig,
    GatewayConfigManager,
)
from fixtures import get_default_resource_management_config, get_test_gateway_config
from pathlib import Path


class TestTypedResourceManagementConfig:
    """Test typed resource management configuration with validation"""
    
    def test_valid_config_creation(self):
        """Test creating valid ResourceManagementConfig"""
        config = ResourceManagementConfig(
            max_concurrent_model_loads=2,
            model_loading_slot_acquisition_timeout=0.5,
            reservation_timeout=600,
            reservation_cleanup_interval=60,
            enable_reservation_monitoring=False
        )
        
        assert config.max_concurrent_model_loads == 2
        assert config.model_loading_slot_acquisition_timeout == 0.5
        assert config.reservation_timeout == 600
        assert config.reservation_cleanup_interval == 60
        assert config.enable_reservation_monitoring is False
        
    def test_config_validation_errors(self):
        """Test that invalid configs raise proper validation errors"""
        
        # Test invalid max concurrent loads
        with pytest.raises(ResourceManagementConfigError,
            match="max_concurrent_model_loads must be >= 1"):
            ResourceManagementConfig(
                max_concurrent_model_loads=0,
                model_loading_slot_acquisition_timeout=0.25,
                reservation_timeout=300,
                reservation_cleanup_interval=30
            )
            
        # Test invalid timeout range
        with pytest.raises(ResourceManagementConfigError,
            match="model_loading_slot_acquisition_timeout must be between"):
            ResourceManagementConfig(
                max_concurrent_model_loads=1,
                model_loading_slot_acquisition_timeout=100.0,  # Too high
                reservation_timeout=300,
                reservation_cleanup_interval=30
            )
            
        # Test cleanup interval >= reservation timeout
        with pytest.raises(ResourceManagementConfigError, match="reservation_cleanup_interval.*must be less than reservation_timeout"):
            ResourceManagementConfig(
                max_concurrent_model_loads=1,
                model_loading_slot_acquisition_timeout=0.25,
                reservation_timeout=300,
                reservation_cleanup_interval=400  # Greater than reservation timeout
            )
    
    def test_from_dict_valid(self):
        """Test creating config from dictionary"""
        config_dict = {
            "max_concurrent_model_loads": 3,
            "model_loading_slot_acquisition_timeout": 1.0,
            "reservation_timeout": 900,
            "reservation_cleanup_interval": 120,
            "enable_reservation_monitoring": True
        }
        
        config = ResourceManagementConfig.from_dict(config_dict)
        
        assert config.max_concurrent_model_loads == 3
        assert config.model_loading_slot_acquisition_timeout == 1.0
        assert config.reservation_timeout == 900
        assert config.reservation_cleanup_interval == 120
        assert config.enable_reservation_monitoring is True
        
    def test_from_dict_missing_fields(self):
        """Test that missing required fields raise errors"""
        incomplete_dict = {
            "max_concurrent_model_loads": 1,
            # Missing other required fields
        }
        
        with pytest.raises(
            ResourceManagementConfigError, match="Required field.*missing"
        ):
            ResourceManagementConfig.from_dict(incomplete_dict)
            
    def test_from_dict_wrong_types(self):
        """Test that wrong types raise errors"""
        wrong_type_dict = {
            "max_concurrent_model_loads": "not_an_int",  # Should be int
            "model_loading_slot_acquisition_timeout": 0.25,
            "reservation_timeout": 300,
            "reservation_cleanup_interval": 30
        }
        
        with pytest.raises(ResourceManagementConfigError, match="must be of type"):
            ResourceManagementConfig.from_dict(wrong_type_dict)
            
    def test_default_config(self):
        """Test default configuration is valid"""
        default_config = get_default_resource_management_config()
        
        # Should be valid
        assert isinstance(default_config, ResourceManagementConfig)
        assert default_config.max_concurrent_model_loads == 1
        assert default_config.model_loading_slot_acquisition_timeout == 0.25
        assert default_config.reservation_timeout == 300
        assert default_config.reservation_cleanup_interval == 30
        assert default_config.enable_reservation_monitoring is True
        

class TestGatewayConfig:
    """Test typed gateway configuration with mandatory resource management"""
    
    def test_valid_gateway_config_creation(self):
        """Test creating valid GatewayConfig with resource management"""
        rm_config = get_default_resource_management_config()
        
        gateway_config = GatewayConfig(
            url="http://localhost:9998",
            name="test-gateway",
            timeout=30.0,
            api_key="test-key",
            resource_management=rm_config
        )
        
        assert gateway_config.url == "http://localhost:9998"
        assert gateway_config.name == "test-gateway" 
        assert gateway_config.timeout == 30.0
        assert gateway_config.api_key == "test-key"
        assert isinstance(gateway_config.resource_management, ResourceManagementConfig)
        
    def test_gateway_config_from_dict_with_resource_management(self):
        """Test creating GatewayConfig from dictionary with resource management"""
        gateway_dict = {
            "url": "http://localhost:9998",
            "name": "test-gateway",
            "timeout": 30.0,
            "api_key": "test-key",
            "resource_management": {
                "max_concurrent_model_loads": 2,
                "model_loading_slot_acquisition_timeout": 0.5,
                "reservation_timeout": 600,
                "reservation_cleanup_interval": 60,
                "enable_reservation_monitoring": True
            }
        }
        
        config = GatewayConfig.from_dict(gateway_dict)
        
        assert config.url == "http://localhost:9998"
        assert config.name == "test-gateway"
        assert config.resource_management.max_concurrent_model_loads == 2
        assert config.resource_management.model_loading_slot_acquisition_timeout == 0.5
        
    def test_gateway_config_requires_resource_management(self):
        """Test gateway config requires resource_management (BREAKING CHANGE)."""
        gateway_dict_without_rm = {
            "url": "http://localhost:9998",
            "name": "legacy-gateway",
            "timeout": 30.0,
            "api_key": "test-key"
            # No resource_management section - should now fail
        }
        
        with pytest.raises(
            ResourceManagementConfigError,
            match="requires resource_management configuration",
        ):
            GatewayConfig.from_dict(gateway_dict_without_rm)
            
    def test_gateway_config_validation_errors(self):
        """Test gateway config validation errors"""
        
        # Empty URL should fail
        with pytest.raises(
            ResourceManagementConfigError, match="Gateway URL cannot be empty"
        ):
            GatewayConfig(
                url="",
                name="test",
                resource_management=get_default_resource_management_config()
            )
            
        # Empty name should fail
        with pytest.raises(
            ResourceManagementConfigError, match="Gateway name cannot be empty"
        ):
            GatewayConfig(
                url="http://localhost:9998",
                name="",
                resource_management=get_default_resource_management_config()
            )
            
        # Invalid timeout should fail
        with pytest.raises(
            ResourceManagementConfigError, match="Gateway timeout must be > 0"
        ):
            GatewayConfig(
                url="http://localhost:9998",
                name="test",
                timeout=0,
                resource_management=get_default_resource_management_config()
            )


class TestConfigFileLoading:
    """Test loading and validating configuration files"""
    
    def test_load_valid_config_file(self):
        """Test loading valid gateway configuration from file"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    timeout: 30.0
    api_key: test-key
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
  - url: http://remote:9998
    name: gateway-2
    resource_management:
      max_concurrent_model_loads: 2
      model_loading_slot_acquisition_timeout: 1.0
      reservation_timeout: 600
      reservation_cleanup_interval: 60
      enable_reservation_monitoring: false
""")):
            configs = load_gateway_configs(Path("config/gateways.yaml"))
            
        assert len(configs) == 2
        
        # Test first gateway
        gw1 = configs["gateway-1"]
        assert gw1.url == "http://localhost:9998"
        assert gw1.name == "gateway-1"
        assert gw1.timeout == 30.0
        assert gw1.api_key == "test-key"
        assert gw1.resource_management.max_concurrent_model_loads == 1
        assert gw1.resource_management.model_loading_slot_acquisition_timeout == 0.25
        
        # Test second gateway
        gw2 = configs["gateway-2"]
        assert gw2.url == "http://remote:9998"
        assert gw2.name == "gateway-2"
        assert gw2.resource_management.max_concurrent_model_loads == 2
        assert gw2.resource_management.model_loading_slot_acquisition_timeout == 1.0
        assert gw2.resource_management.enable_reservation_monitoring is False
        
    def test_load_config_missing_resource_management(self):
        """Test loading config without resource management fails (BREAKING CHANGE)."""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: legacy-gateway
    timeout: 30.0
    # Missing resource_management section
""")):
            with pytest.raises(
                ResourceManagementConfigError,
                match="requires resource_management configuration",
            ):
                load_gateway_configs(Path("config/gateways.yaml"))
                
    def test_load_config_invalid_resource_management(self):
        """Test that loading config with invalid resource management fails"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: bad-gateway
    resource_management:
      max_concurrent_model_loads: -1  # Invalid value
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
""")):
            with pytest.raises(ResourceManagementConfigError,
                match="max_concurrent_model_loads must be >= 1"):
                load_gateway_configs(Path("config/gateways.yaml"))
                
    def test_load_config_duplicate_names(self):
        """Test that duplicate gateway names are rejected"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: duplicate-name
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
  - url: http://remote:9998
    name: duplicate-name  # Same name as first gateway
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
""")):
            with pytest.raises(
                ResourceManagementConfigError, match="Duplicate gateway name"
            ):
                load_gateway_configs(Path("config/gateways.yaml"))
                
    def test_load_config_empty_gateways(self):
        """Test that empty gateways list is rejected"""
        with patch('builtins.open', mock_open(read_data="""
gateways: []
""")):
            with pytest.raises(
                ResourceManagementConfigError,
                match="At least one gateway must be configured",
            ):
                load_gateway_configs(Path("config/gateways.yaml"))
                
    def test_load_config_file_not_found(self):
        """Test handling of missing configuration file"""
        with pytest.raises(FileNotFoundError):
            load_gateway_configs(Path("nonexistent.yaml"))
            
    def test_load_config_invalid_yaml(self):
        """Test handling of invalid YAML"""
        with patch('builtins.open', mock_open(read_data="invalid: yaml: content: [")):
            with pytest.raises(
                ResourceManagementConfigError,
                match="Failed to parse gateway configuration YAML",
            ):
                load_gateway_configs(Path("config/gateways.yaml"))


class TestConfigReload:
    """Test zero-downtime configuration reloading (HIGH-RISK)"""
    
    def test_reload_specific_gateway(self):
        """Test reloading configuration for a specific gateway"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 2
      model_loading_slot_acquisition_timeout: 0.5
      reservation_timeout: 600
      reservation_cleanup_interval: 60
      enable_reservation_monitoring: true
  - url: http://remote:9998
    name: gateway-2
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: false
""")):
            # Reload specific gateway
            gateway_config = reload_gateway_config(Path("config/gateways.yaml"),
                "gateway-1")
            
        assert gateway_config.name == "gateway-1"
        assert gateway_config.url == "http://localhost:9998"
        assert gateway_config.resource_management.max_concurrent_model_loads == 2
        assert gateway_config.resource_management.model_loading_slot_acquisition_timeout == 0.5
        
    def test_reload_nonexistent_gateway(self):
        """Test reloading configuration for nonexistent gateway"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
""")):
            with pytest.raises(
                ResourceManagementConfigError, match="Gateway 'nonexistent' not found"
            ):
                reload_gateway_config(Path("config/gateways.yaml"), "nonexistent")


class TestValidationSafety:
    """Test validation safety checks (HIGH-RISK)"""
    
    def test_cleanup_interval_safety_check(self):
        """Test that cleanup interval must be < 50% of reservation timeout for safety"""
        with pytest.raises(
            ResourceManagementConfigError,
            match="should be < 50% of reservation timeout",
        ):
            ResourceManagementConfig(
                max_concurrent_model_loads=1,
                model_loading_slot_acquisition_timeout=0.25,
                reservation_timeout=300,  # 5 minutes
                reservation_cleanup_interval=200,  # > 50% of timeout (unsafe)
                enable_reservation_monitoring=True
            )
            
    def test_structured_error_messages(self):
        """Test that error messages include field and gateway context"""
        try:
            ResourceManagementConfig(
                max_concurrent_model_loads=0,  # Invalid
                model_loading_slot_acquisition_timeout=0.25,
                reservation_timeout=300,
                reservation_cleanup_interval=30
            )
        except ResourceManagementConfigError as e:
            assert e.field_name == "max_concurrent_model_loads"
            assert "field 'max_concurrent_model_loads'" in str(e)
            
    def test_gateway_context_in_errors(self):
        """Test that gateway validation errors include gateway name"""
        gateway_dict = {
            "url": "http://localhost:9998",
            "name": "test-gateway",
            "resource_management": {
                "max_concurrent_model_loads": -1,  # Invalid
                "model_loading_slot_acquisition_timeout": 0.25,
                "reservation_timeout": 300,
                "reservation_cleanup_interval": 30
            }
        }
        
        try:
            GatewayConfig.from_dict(gateway_dict)
        except ResourceManagementConfigError as e:
            assert e.gateway_name == "test-gateway"
            assert "gateway 'test-gateway'" in str(e)


class TestGatewayConfigManager:
    """Test event-driven configuration manager (HIGH-RISK)"""
    
    @pytest.mark.asyncio
    async def test_config_manager_initialization(self):
        """Test config manager initialization and loading"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 2
      model_loading_slot_acquisition_timeout: 0.5
      reservation_timeout: 600
      reservation_cleanup_interval: 60
      enable_reservation_monitoring: true
""")):
            manager = GatewayConfigManager(Path("config/gateways.yaml"))
            await manager.initialize()
            
            # Test getting specific gateway config
            config = await manager.get_gateway_config("gateway-1")
            assert config.name == "gateway-1"
            assert config.resource_management.max_concurrent_model_loads == 2
            
            # Test getting all configs
            all_configs = await manager.get_all_gateway_configs()
            assert len(all_configs) == 1
            assert "gateway-1" in all_configs
            
    @pytest.mark.asyncio
    async def test_config_manager_reload_with_notification(self):
        """Test config reload with subscriber notification"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
            manager = GatewayConfigManager(Path("config/gateways.yaml"))
            await manager.initialize()
            
            # Subscribe to updates
            notifications = []
            async def callback(gateway_name: str, config: GatewayConfig):
                notifications.append((gateway_name,
                    config.resource_management.max_concurrent_model_loads))
                
            await manager.subscribe_async(callback)
            
            # Mock updated config file
            with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 3  # Changed value
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
                # Reload configuration
                updated_config = await manager.reload_gateway_config("gateway-1")
                
                # Verify config was updated
                assert (
                    updated_config.resource_management.max_concurrent_model_loads
                    == 3
                )
                
                # Verify subscriber was notified
                assert len(notifications) == 1
                assert notifications[0] == ("gateway-1", 3)
                
    @pytest.mark.asyncio
    async def test_concurrent_config_access(self):
        """Test concurrent access to configuration manager"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
            manager = GatewayConfigManager(Path("config/gateways.yaml"))
            await manager.initialize()
            
            # Create multiple concurrent tasks accessing configuration
            async def get_config_task():
                return await manager.get_gateway_config("gateway-1")
                
            async def get_all_configs_task():
                return await manager.get_all_gateway_configs()
                
            # Run tasks concurrently
            import asyncio
            results = await asyncio.gather(
                get_config_task(),
                get_config_task(),
                get_all_configs_task(),
                get_config_task(),
                return_exceptions=True
            )
            
            # All tasks should succeed
            assert len(results) == 4
            for result in results:
                assert not isinstance(result, Exception)
                
            # Verify all single configs are identical
            single_configs = [r for r in results if isinstance(r, GatewayConfig)]
            assert len(single_configs) == 3
            for config in single_configs:
                assert config.name == "gateway-1"
                assert config.resource_management.max_concurrent_model_loads == 1
                
    @pytest.mark.asyncio
    async def test_config_reload_failure_isolation(self):
        """Test that invalid reload doesn't corrupt existing config"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
            manager = GatewayConfigManager(Path("config/gateways.yaml"))
            await manager.initialize()
            
            # Get initial config
            initial_config = await manager.get_gateway_config("gateway-1")
            assert initial_config.resource_management.max_concurrent_model_loads == 1
            
            # Try to reload with invalid config
            with patch('builtins.open', mock_open(read_data="invalid: yaml: [")):
                with pytest.raises(ResourceManagementConfigError):
                    await manager.reload_gateway_config("gateway-1")
                    
            # Verify original config is still intact
            current_config = await manager.get_gateway_config("gateway-1")
            assert current_config.resource_management.max_concurrent_model_loads == 1
            assert current_config == initial_config
            
    @pytest.mark.asyncio
    async def test_concurrent_config_reloads(self):
        """Test safety of concurrent configuration reloads (HIGH-RISK)"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
            manager = GatewayConfigManager(Path("config/gateways.yaml"))
            await manager.initialize()
            
            # Simulate concurrent reload requests
            async def reload_task():
                return await manager.reload_gateway_config("gateway-1")
                
            # Run 10 concurrent reloads
            results = await asyncio.gather(
                *[reload_task() for _ in range(10)],
                return_exceptions=True
            )
            
            # All reloads should succeed
            for result in results:
                assert not isinstance(result, Exception)
                assert result.name == "gateway-1"
                
            # Final config should be consistent
            final_config = await manager.get_gateway_config("gateway-1")
            assert final_config.resource_management.max_concurrent_model_loads == 1
            
    @pytest.mark.asyncio
    async def test_subscriber_failure_handling(self):
        """Test that subscriber failures don't break notification system (HIGH-RISK)"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
            manager = GatewayConfigManager(Path("config/gateways.yaml"))
            await manager.initialize()
            
            # Subscribe callbacks: one failing, one succeeding
            successful_notifications = []
            
            async def failing_callback(gateway_name: str, config):
                raise RuntimeError("Test failure")
                
            async def successful_callback(gateway_name: str, config):
                successful_notifications.append((gateway_name,
                    config.resource_management.max_concurrent_model_loads))
                
            await manager.subscribe_async(failing_callback)
            await manager.subscribe_async(successful_callback)
            
            # Trigger reload with updated config
            with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 2
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
                # Reload should succeed despite failing callback
                updated_config = await manager.reload_gateway_config("gateway-1")
                assert (
                    updated_config.resource_management.max_concurrent_model_loads
                    == 2
                )
                
                # Successful callback should have been notified
                assert len(successful_notifications) == 1
                assert successful_notifications[0] == ("gateway-1", 2)
                
    @pytest.mark.asyncio
    async def test_unsubscribe_functionality(self):
        """Test unsubscribe removes callback from notifications"""
        with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 1
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
            manager = GatewayConfigManager(Path("config/gateways.yaml"))
            await manager.initialize()
            
            # Subscribe and then unsubscribe
            notifications = []
            async def callback(gateway_name: str, config):
                notifications.append(gateway_name)
                
            await manager.subscribe_async(callback)
            await manager.unsubscribe(callback)
            
            # Trigger reload
            with patch('builtins.open', mock_open(read_data="""
gateways:
  - url: http://localhost:9998
    name: gateway-1
    resource_management:
      max_concurrent_model_loads: 2
      model_loading_slot_acquisition_timeout: 0.25
      reservation_timeout: 300
      reservation_cleanup_interval: 30
      enable_reservation_monitoring: true
""")):
                await manager.reload_gateway_config("gateway-1")
                
                # Callback should not have been notified
                assert len(notifications) == 0
