#!/usr/bin/env python3
"""
Integration test for vision model support.

Usage:
    python scripts/test_vision_model.py /path/to/qwen2.5-vl.gguf /path/to/mmproj.gguf
    python scripts/test_vision_model.py /path/to/qwen2.5-vl.gguf  # Auto-detect mmproj
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def test_vision_model(model_path: str, clip_path: str | None = None) -> bool:
    """Test loading and running inference with a vision model."""
    from inference_djinn.engines.gguf import GGUFEngine
    from inference_djinn.scripts.config_generators.gguf.vision_detector import (
        detect_vision_architecture,
        find_mmproj_file,
    )

    # Detect vision architecture
    vision_arch = detect_vision_architecture(model_path)
    if not vision_arch:
        logger.error(f"❌ Not a recognized vision model: {model_path}")
        return False

    logger.info(f"✅ Detected vision architecture: {vision_arch}")

    # Find mmproj file if not provided
    if not clip_path:
        clip_path = find_mmproj_file(model_path)
        if not clip_path:
            logger.error("❌ Could not find mmproj file. Please provide path.")
            return False

    logger.info(f"✅ Using CLIP model: {clip_path}")

    # Load engine with vision config
    logger.info("\n🔄 Loading vision model...")
    engine = GGUFEngine(
        model_path,
        vision_architecture=vision_arch,
        clip_model_path=clip_path,
        n_ctx=8192,
        n_gpu_layers=-1,
    )

    await engine.load()

    # Verify vision support
    if not engine.supports_vision:
        logger.error("❌ Engine does not report vision support after loading")
        await engine.unload()
        return False

    logger.info(f"✅ Vision model loaded: {engine.get_vision_info()}")

    # Test 1: Text-only message (should still work)
    logger.info("\n📝 Test 1: Text-only message...")
    response = await engine.generate(
        {
            "messages": [{"role": "user", "content": "Hello, what can you do?"}],
            "max_tokens": 50,
        }
    )
    text_response = response["choices"][0]["message"]["content"]
    logger.info(f"✅ Text response: {text_response[:100]}...")

    # Test 2: Multi-modal message with test image
    logger.info("\n🖼️ Test 2: Multi-modal message...")

    # Create a simple 1x1 red pixel PNG for testing
    test_image_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="

    response = await engine.generate(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{test_image_b64}"
                            },
                        },
                        {"type": "text", "text": "What color is this image?"},
                    ],
                }
            ],
            "max_tokens": 50,
        }
    )
    vision_response = response["choices"][0]["message"]["content"]
    logger.info(f"✅ Vision response: {vision_response[:100]}...")

    # Test 3: Token counting with images
    logger.info("\n🔢 Test 3: Token counting with images...")
    token_result = await engine.count_tokens_for_messages(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{test_image_b64}"},
                    },
                    {"type": "text", "text": "Describe this image in detail."},
                ],
            }
        ]
    )
    logger.info(
        f"✅ Token count: {token_result.tokens} (method: {token_result.method})"
    )

    # Verify token count includes image tokens
    if "vision" not in token_result.method:
        logger.warning("⚠️ Token counting method does not indicate vision processing")

    # Cleanup
    await engine.unload()
    logger.info("\n✅ All vision tests passed!")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Test vision model support")
    parser.add_argument("model_path", help="Path to vision model GGUF file")
    parser.add_argument(
        "clip_path",
        nargs="?",
        help="Path to mmproj/CLIP file (auto-detected if not provided)",
    )
    args = parser.parse_args()

    if not Path(args.model_path).exists():
        logger.error(f"Model file not found: {args.model_path}")
        sys.exit(1)

    success = asyncio.run(test_vision_model(args.model_path, args.clip_path))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
