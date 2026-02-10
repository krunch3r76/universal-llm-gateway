#!/usr/bin/env python3
"""Measure RAM usage for CTranslate2 models."""

import os
import sys
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    print("ERROR: psutil not installed. Install with: pip install psutil")
    sys.exit(1)

try:
    import ctranslate2
except ImportError:
    print("ERROR: ctranslate2 not installed. Install with: pip install ctranslate2")
    sys.exit(1)


def measure_model_ram(model_path: str) -> int:
    """Measure RAM usage (MB) for a CTranslate2 model."""
    process = psutil.Process(os.getpid())
    
    # Baseline RAM before loading
    baseline = process.memory_info().rss / (1024 * 1024)
    
    print(f"Loading model: {model_path}")
    print(f"Baseline RAM: {baseline:.0f} MB")
    
    # Load model
    translator = ctranslate2.Translator(model_path, device="cpu", compute_type="int8")
    
    # Measure after loading
    loaded = process.memory_info().rss / (1024 * 1024)
    model_ram = loaded - baseline
    
    print(f"RAM after load: {loaded:.0f} MB")
    print(f"Model RAM usage: {model_ram:.0f} MB")
    
    # Run a test translation to ensure model is fully initialized
    # CTranslate2 expects tokenized input (list of lists of tokens)
    print("\nRunning test translation...")
    try:
        # Try to use tokenizer if available, otherwise use simple tokenization
        source_tokens = [["Hello", "world"]]
        results = translator.translate_batch(source_tokens)
        print(f"Test translation successful (output length: {len(results)})")
    except Exception as e:
        print(f"Test translation skipped (not critical for measurement): {e}")
    
    # Final measurement after potential translation
    final = process.memory_info().rss / (1024 * 1024)
    final_ram = final - baseline
    
    print(f"\nFinal RAM usage: {final_ram:.0f} MB")
    
    # Round up to nearest 50MB for safety margin
    rounded = int((final_ram // 50 + 1) * 50)
    print(f"Recommended catalog value: {rounded} MB (rounded up)")
    
    return rounded


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: measure_ct2_ram.py <model_path>")
        print("\nExample:")
        print("  python measure_ct2_ram.py /mnt/torus/models/ctranslate/opus-mt-pl-en")
        sys.exit(1)
    
    model_path = sys.argv[1]
    if not Path(model_path).exists():
        print(f"ERROR: Model path does not exist: {model_path}")
        sys.exit(1)
    
    ram_mb = measure_model_ram(model_path)
    print(f"\n✅ Catalog entry should use: ram_mb: {ram_mb}")