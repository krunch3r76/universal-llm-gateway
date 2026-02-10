#!/usr/bin/env python3
"""
Inspect GGUF file to check if context length metadata is present.

Usage:
    python scripts/inspect_gguf_context.py /path/to/model.gguf
"""

import sys
from pathlib import Path


def inspect_gguf_context(file_path: str) -> None:
    """Inspect GGUF file for context length metadata."""
    path = Path(file_path)
    
    if not path.exists():
        print(f"❌ File not found: {file_path}")
        return
    
    if not path.is_file():
        print(f"❌ Not a file: {file_path}")
        return
    
    try:
        import gguf
    except ImportError:
        print("❌ gguf library not available. Install with: pip install gguf")
        return
    
    try:
        reader = gguf.GGUFReader(str(path))
        fields = reader.fields
        
        print(f"📁 Inspecting: {file_path}")
        print(f"📊 Total fields: {len(fields)}")
        print()
        
        # Check architecture
        arch_field = fields.get("general.architecture")
        arch = None
        if arch_field and arch_field.data:
            arch_value = arch_field.parts[arch_field.data[0]]
            if hasattr(arch_value, "tobytes"):
                arch = arch_value.tobytes().decode("utf-8").rstrip("\x00").strip()
            elif isinstance(arch_value, str):
                arch = arch_value
            else:
                arch = str(arch_value)
            print(f"🏗️  Architecture: {arch}")
        else:
            print("⚠️  Architecture not found")
        
        print()
        print("🔍 Checking for context length fields...")
        
        # Check architecture-specific field
        if arch:
            arch_context_field = f"{arch}.context_length"
            if arch_context_field in fields:
                field = fields[arch_context_field]
                if field.data:
                    value = field.parts[field.data[0]]
                    if isinstance(value, list | tuple) and len(value) > 0:
                        ctx_len = int(value[0])
                    elif hasattr(value, "item"):
                        ctx_len = int(value.item())
                    else:
                        ctx_len = int(value)
                    print(f"✅ Found: {arch_context_field} = {ctx_len}")
                    return
                else:
                    print(f"⚠️  Field exists but has no data: {arch_context_field}")
            else:
                print(f"❌ Not found: {arch_context_field}")
        
        # Check common field names
        context_field_names = [
            "llama.context_length",
            "context_length",
            "n_ctx_train",
            "max_position_embeddings",
        ]
        
        found = False
        for field_name in context_field_names:
            if field_name in fields:
                field = fields[field_name]
                if field.data:
                    value = field.parts[field.data[0]]
                    if isinstance(value, list | tuple) and len(value) > 0:
                        ctx_len = int(value[0])
                    elif hasattr(value, "item"):
                        ctx_len = int(value.item())
                    else:
                        ctx_len = int(value)
                    print(f"✅ Found: {field_name} = {ctx_len}")
                    found = True
                    break
                else:
                    print(f"⚠️  Field exists but has no data: {field_name}")
            else:
                print(f"❌ Not found: {field_name}")
        
        if not found:
            print()
            print("❌ No context length field found in GGUF metadata")
            print()
            print("📋 Available fields (first 50):")
            for i, field_name in enumerate(sorted(fields.keys())[:50]):
                print(f"   - {field_name}")
            if len(fields) > 50:
                print(f"   ... and {len(fields) - 50} more fields")
        
    except Exception as e:
        print(f"❌ Error reading GGUF file: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_gguf_context.py /path/to/model.gguf")
        sys.exit(1)
    
    inspect_gguf_context(sys.argv[1])




