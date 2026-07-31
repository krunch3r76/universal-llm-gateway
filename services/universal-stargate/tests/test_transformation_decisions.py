"""
Test transformation decisions based on input_schema with detailed logging verification.

Tests that:
1. Models with input_schema="messages" skip transformation
2. Models with input_schema="prompt" undergo transformation  
3. Proper logging occurs for transformation decisions
4. Helper functions work correctly with edge cases
"""

import pytest
import logging
from unittest.mock import MagicMock, AsyncMock, patch
from gateway_client import ModelMetadata
from systems.proxy.core.nonstreaming import RequestPreparer, RequestContext
from systems.proxy.utils.model_metadata_helpers import (
    extract_input_schema,
    should_transform_to_prompt,
    is_cpu_model,
    is_gpu_model,
    get_resource_requirements,
    get_context_length,
    is_model_enabled
)


@pytest.fixture
def mock_dependencies():
    """Create mock dependencies for RequestPreparer"""
    gateway_manager = AsyncMock()
    chat_template_stargate = MagicMock()
    token_manager = MagicMock()
    
    # Mock chat template stargate methods
    chat_template_stargate.apply_transformation_filters.return_value = [
        {"role": "user", "content": "Test message"}
    ]
    
    return {
        'gateway_manager': gateway_manager,
        'chat_template_stargate': chat_template_stargate, 
        'token_manager': token_manager
    }


@pytest.fixture
def messages_model_metadata():
    """ModelMetadata for a model that expects messages format"""
    return ModelMetadata(
        id='messages-model',
        model_type='gguf',
        input_schema='messages',  # Should NOT transform
        parameter_defaults={},
        supported_parameters=[],
        middleware_config={},
        enabled=True,
        loader_type='llama_cpp_gpu',
        path='/models/messages-model.gguf',
        context_length=8192,
        ram_usage=1000,
        vram_usage=4000
    )


@pytest.fixture
def prompt_model_metadata():
    """ModelMetadata for a model that expects prompt format"""
    return ModelMetadata(
        id='prompt-model',
        model_type='gguf',
        input_schema='prompt',  # Should transform
        parameter_defaults={},
        supported_parameters=[],
        middleware_config={},
        enabled=True,
        loader_type='llama_cpp_cpu',
        path='/models/prompt-model.gguf',
        context_length=4096,
        ram_usage=2000,
        vram_usage=0
    )


class TestHelperFunctions:
    """Test the pure helper functions"""
    
    def test_extract_input_schema(self, messages_model_metadata, prompt_model_metadata):
        """Test input_schema extraction with various inputs"""
        assert extract_input_schema(messages_model_metadata) == 'messages'
        assert extract_input_schema(prompt_model_metadata) == 'prompt'
        assert extract_input_schema(None) == 'prompt'  # Safe fallback
        
        # Test with empty input_schema
        empty_metadata = ModelMetadata(
            id='empty', model_type='gguf', input_schema='',
            parameter_defaults={}, supported_parameters=[], middleware_config={},
            enabled=True, loader_type='llama_cpp', path='/models/empty.ggu'
        )
        assert extract_input_schema(empty_metadata) == 'prompt'  # Fallback
    
    def test_should_transform_to_prompt(self, messages_model_metadata,
        prompt_model_metadata):
        """Test transformation decision logic"""
        # Don't transform messages-format models
        assert should_transform_to_prompt(messages_model_metadata) is False
        assert should_transform_to_prompt(prompt_model_metadata) is True
        assert should_transform_to_prompt(None) is True  # Safe fallback
    
    def test_cpu_gpu_detection(self, messages_model_metadata, prompt_model_metadata):
        """Test CPU/GPU model detection"""
        # messages_model has loader_type='llama_cpp_gpu' -> is GPU
        assert is_gpu_model(messages_model_metadata) == True
        assert is_cpu_model(messages_model_metadata) == False
        
        # prompt_model has loader_type='llama_cpp_cpu' -> is CPU  
        assert is_cpu_model(prompt_model_metadata) == True
        assert is_gpu_model(prompt_model_metadata) == False
        
        # None defaults to CPU
        assert is_cpu_model(None) == True
        assert is_gpu_model(None) == False
    
    def test_resource_requirements(self, messages_model_metadata,
        prompt_model_metadata):
        """Test resource requirement extraction"""
        gpu_resources = get_resource_requirements(messages_model_metadata)
        assert gpu_resources == {'ram_mb': 1000, 'vram_mb': 4000}
        
        cpu_resources = get_resource_requirements(prompt_model_metadata)  
        assert cpu_resources == {'ram_mb': 2000, 'vram_mb': 0}
        
        none_resources = get_resource_requirements(None)
        assert none_resources == {'ram_mb': 0, 'vram_mb': 0}
    
    def test_context_length_extraction(self, messages_model_metadata,
        prompt_model_metadata):
        """Test context length extraction"""
        assert get_context_length(messages_model_metadata) == 8192
        assert get_context_length(prompt_model_metadata) == 4096
        assert get_context_length(None) is None
        
    def test_model_enabled_check(self, messages_model_metadata):
        """Test model enabled status checking"""
        assert is_model_enabled(messages_model_metadata) == True
        assert is_model_enabled(None) == False
        
        # Test with disabled model
        disabled_metadata = ModelMetadata(
            id='disabled', model_type='gguf', input_schema='messages',
            parameter_defaults={}, supported_parameters=[], middleware_config={},
            enabled=False, loader_type='llama_cpp', path='/models/disabled.ggu'
        )
        assert is_model_enabled(disabled_metadata) == False


class TestTransformationDecisions:
    """Test transformation decisions with logging verification"""
    
    @pytest.mark.asyncio
    async def test_messages_model_skips_transformation(self, mock_dependencies,
        messages_model_metadata, caplog):
        """Test that messages models skip transformation with proper logging"""
        # Setup
        preparer = RequestPreparer(
            gateway_manager=mock_dependencies['gateway_manager'],
            chat_template_stargate=mock_dependencies['chat_template_stargate'],
            token_manager=mock_dependencies['token_manager'],
            token_management_enabled=False
        )
        
        # Mock gateway manager to return messages model
        mock_dependencies["gateway_manager"].get_model_configuration.return_value = (
            messages_model_metadata
        )
        
        # Create request context
        from src.schemas.chat_completion import ChatCompletionRequest, ChatMessage
        chat_request = ChatCompletionRequest(
            model='messages-model',
            messages=[ChatMessage(role='user', content='Test message')]
        )
        
        # Capture logs at INFO level
        with caplog.at_level(logging.INFO):
            context = RequestContext(
                request_id='test-123',
                start_time=0.0,
                selected_model='messages-model',
                original_request={'model': 'messages-model'},
                raw_client_fields={},
                user_params={},
                middleware_actions=[],
                chat_request=chat_request
            )
            
            await preparer._prepare_normal_mode(context)
        
        # Verify transformation decision
        assert should_transform_to_prompt(context.model_metadata) == False
        assert "pass_through_messages_format_with_filters" in context.middleware_actions
        
        # Verify logging
        log_messages = [record.message for record in caplog.records if record.levelno
            == logging.INFO]
        assert any("expects messages format" in msg for msg in log_messages)
        assert any("input_schema: 'messages'" in msg for msg in log_messages)
        assert any("should_transform_to_prompt: False" in msg for msg in log_messages)
    
    @pytest.mark.asyncio  
    async def test_prompt_model_undergoes_transformation(self, mock_dependencies,
        prompt_model_metadata, caplog):
        """Test that prompt models undergo transformation with proper logging"""
        # Setup
        preparer = RequestPreparer(
            gateway_manager=mock_dependencies['gateway_manager'],
            chat_template_stargate=mock_dependencies['chat_template_stargate'],
            token_manager=mock_dependencies['token_manager'],
            token_management_enabled=False
        )
        
        # Mock gateway manager to return prompt model
        mock_dependencies["gateway_manager"].get_model_configuration.return_value = (
            prompt_model_metadata
        )

        # Mock transformer
        with patch.object(preparer.transformer, "transform_to_prompt") as mock_transform:
            mock_transform.return_value = ([], {"prompt_content": "Test prompt"})
            
            # Create request context
            from src.schemas.chat_completion import ChatCompletionRequest, ChatMessage
            chat_request = ChatCompletionRequest(
                model='prompt-model',
                messages=[ChatMessage(role='user', content='Test message')]
            )
            
            # Capture logs at INFO level
            with caplog.at_level(logging.INFO):
                context = RequestContext(
                    request_id='test-456',
                    start_time=0.0,
                    selected_model='prompt-model',
                    original_request={'model': 'prompt-model'},
                    raw_client_fields={},
                    user_params={},
                    middleware_actions=[],
                    chat_request=chat_request
                )
                
                await preparer._prepare_normal_mode(context)
        
        # Verify transformation occurred
        assert should_transform_to_prompt(context.model_metadata) == True
        mock_transform.assert_called_once()
        
        # Verify logging
        log_messages = [record.message for record in caplog.records if record.levelno
            == logging.INFO]
        assert any("expects prompt format" in msg for msg in log_messages)
        assert any("input_schema: 'prompt'" in msg for msg in log_messages)
        assert any("should_transform_to_prompt: True" in msg for msg in log_messages)
        assert any("transforming to prompt string" in msg for msg in log_messages)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
