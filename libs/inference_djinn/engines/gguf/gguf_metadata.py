import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class GGUFMetadataLite:
    # ─── Core File Metadata ───
    version: int
    tensor_count: int
    kv_count: int

    # ─── Model Identity ───
    name: str
    architecture: str
    file_type: str
    quantization_version: int

    # ─── Model Architecture Details ───
    context_length: int
    embedding_length: int
    block_count: int
    feed_forward_length: int

    rope_dimension_count: int
    rope_freq_base: float

    head_count: int
    head_count_kv: int
    layer_norm_rms_epsilon: float

    # ─── Tokenizer Metadata (Summary Only) ───
    tokenizer_model: str
    bos_token_id: int
    eos_token_id: int
    padding_token_id: int | None = None
    add_bos_token: bool = True
    add_eos_token: bool = False
    chat_template: str | None = None

    tokenizer_token_count: int | None = None
    tokenizer_merge_count: int | None = None
    tokenizer_checksums: dict[str, str] | None = field(default_factory=dict)

    # ─── Export Helper ───
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with JSON-serializable values."""
        result = asdict(self)
        return self._convert_numpy_types(result)

    def _convert_numpy_types(self, obj: Any) -> Any:
        """Convert numpy types to native Python types for JSON serialization."""
        import numpy as np

        if isinstance(obj, dict):
            return {k: self._convert_numpy_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_numpy_types(item) for item in obj]
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.bool_):
            return bool(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        else:
            return obj

    @classmethod
    def _get_list_length(cls, reader: Any, key: str) -> int:
        """Get the length of a list field from the GGUF reader."""
        try:
            field = reader.get_field(key) if hasattr(reader, "get_field") else None
            if field is not None and hasattr(field, "parts") and len(field.parts) > 0:
                # The actual value is in the last part
                value_part = field.parts[-1]

                # Handle numpy memmap arrays
                if hasattr(value_part, "__len__"):
                    return len(value_part)
                else:
                    return 1 if value_part is not None else 0
            return 0
        except Exception:
            return 0

    @classmethod
    def from_gguf(cls, reader: Any) -> "GGUFMetadataLite":
        """Create GGUFMetadataLite from GGUF reader object."""

        def get(key, default=None):
            try:
                field = reader.get_field(key) if hasattr(reader, "get_field") else None
                if (
                    field is not None
                    and hasattr(field, "parts")
                    and len(field.parts) > 0
                ):
                    # The actual value is in the last part
                    value_part = field.parts[-1]

                    # Check if this is a string field based on the key name patterns
                    is_string_field = any(
                        pattern in key.lower()
                        for pattern in [
                            "name",
                            "model",
                            "architecture",
                            "template",
                            "chat",
                        ]
                    )

                    # Handle numpy memmap arrays
                    if hasattr(value_part, "tobytes"):
                        # For string fields, decode bytes to string
                        if is_string_field or (
                            hasattr(field, "types")
                            and field.types
                            and str(field.types[0]) == "GGUFValueType.STRING"
                        ):
                            decoded = value_part.tobytes().decode(
                                "utf-8", errors="ignore"
                            )
                            # Remove null terminators and whitespace
                            return (
                                decoded.rstrip("\x00").strip() if decoded else default
                            )
                        # For numeric fields, return the first value
                        else:
                            value = value_part[0] if len(value_part) > 0 else default
                            # Handle enum types (like GGMLQuantizationType)
                            if hasattr(value, "name"):
                                return value.name
                            return value
                    elif hasattr(value_part, "decode"):
                        # Handle bytes objects
                        decoded = value_part.decode("utf-8", errors="ignore")
                        return decoded.rstrip("\x00").strip() if decoded else default
                    elif hasattr(value_part, "__len__") and len(value_part) > 0:
                        value = value_part[0]
                        # Handle enum types (like GGMLQuantizationType)
                        if hasattr(value, "name"):
                            return value.name
                        return value
                    else:
                        # Handle enum types (like GGMLQuantizationType)
                        if hasattr(value_part, "name"):
                            return value_part.name
                        return value_part
                return default
            except Exception as e:
                get_logger(__name__).debug(f"Error getting field {key}: {e}")
                return default

        def sha256_bytes(key):
            try:
                field = reader.get_field(key) if hasattr(reader, "get_field") else None
                if (
                    field is not None
                    and hasattr(field, "parts")
                    and len(field.parts) > 0
                ):
                    # The actual value is in the last part
                    value_part = field.parts[-1]

                    # Handle numpy memmap arrays
                    if hasattr(value_part, "tobytes"):
                        return hashlib.sha256(value_part.tobytes()).hexdigest()
                    elif isinstance(value_part, (bytes, bytearray)):
                        return hashlib.sha256(value_part).hexdigest()
                    elif isinstance(value_part, list) and len(value_part) > 0:
                        # Handle list of bytes
                        if isinstance(value_part[0], (int, bytes)):
                            byte_data = (
                                bytes(value_part)
                                if isinstance(value_part[0], int)
                                else value_part[0]
                            )
                            return hashlib.sha256(byte_data).hexdigest()
                return None
            except Exception:
                return None

        # Build checksums dict, filtering out None values
        checksums = {}
        tokens_hash = sha256_bytes("tokenizer.ggml.tokens")
        merges_hash = sha256_bytes("tokenizer.ggml.merges")

        if tokens_hash is not None:
            checksums["tokens_sha256"] = tokens_hash
        if merges_hash is not None:
            checksums["merges_sha256"] = merges_hash

        # Extract header information safely
        try:
            version = get("GGUF.version", 0)
            tensor_count = get("GGUF.tensor_count", 0)
            kv_count = get("GGUF.kv_count", 0)
        except Exception:
            version = tensor_count = kv_count = 0

        # Get architecture to determine correct key prefix
        architecture = get("general.architecture", "unknown")
        arch_prefix = architecture.lower() if architecture != "unknown" else "llama"

        # Try architecture-specific prefix first, fall back to llama
        def get_arch(key_suffix, default=0):
            """Get value using architecture prefix, fall back to llama prefix."""
            value = get(f"{arch_prefix}.{key_suffix}", None)
            if value is None or value == 0:
                value = get(f"llama.{key_suffix}", default)
            return value

        return cls(
            version=version,
            tensor_count=tensor_count,
            kv_count=kv_count,
            name=get("general.name", "unknown"),
            architecture=architecture,
            file_type=get("general.file_type", "unknown"),
            quantization_version=get("general.quantization_version", -1),
            context_length=get_arch("context_length", 0),
            embedding_length=get_arch("embedding_length", 0),
            block_count=get_arch("block_count", 0),
            feed_forward_length=get_arch("feed_forward_length", 0),
            rope_dimension_count=get_arch("rope.dimension_count", 0),
            rope_freq_base=get_arch("rope.freq_base", 10000.0),
            head_count=get_arch("attention.head_count", 0),
            head_count_kv=get_arch("attention.head_count_kv", 0),
            layer_norm_rms_epsilon=get_arch("attention.layer_norm_rms_epsilon", 1e-5),
            tokenizer_model=get("tokenizer.ggml.model", "unknown"),
            bos_token_id=get("tokenizer.ggml.bos_token_id", 1),
            eos_token_id=get("tokenizer.ggml.eos_token_id", 2),
            padding_token_id=get("tokenizer.ggml.padding_token_id"),
            add_bos_token=get("tokenizer.ggml.add_bos_token", True),
            add_eos_token=get("tokenizer.ggml.add_eos_token", False),
            chat_template=get("tokenizer.chat_template"),
            tokenizer_token_count=cls._get_list_length(reader, "tokenizer.ggml.tokens"),
            tokenizer_merge_count=cls._get_list_length(reader, "tokenizer.ggml.merges"),
            tokenizer_checksums=checksums,
        )
