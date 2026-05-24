"""Default extraction profile loader + prompt hashing.

The pinned profile (``cortex://configs/document-extraction-v1.yaml``) defines
the canonical model, dpi, prompt, and extraction_type for ``extract_document``.
A profile is loaded once per process lifetime; service restart is required
after a profile bump (v1 → v2 path change) — mirrors the spec's contract
that "profile bumps are explicit policy changes."

``hash_prompt`` lives here because it is the canonical hashing primitive
for prompt text feeding both ``DefaultProfile.as_args_dict_for_hashing``
and the ``effective_prompt_hash`` field that ``extract_document`` passes
into frontmatter.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md, §"Pinned
default profile".
"""

from __future__ import annotations

import functools
import hashlib
from dataclasses import dataclass
from typing import Any, Final

import yaml

from ._file_helpers import FILES_ROOT

# Default-profile location on the cortex files mount; spec §"Pinned default
# profile". Phase-b artifact.
_DEFAULT_PROFILE_RELATIVE_PATH: Final[str] = "configs/document-extraction-v1.yaml"


def hash_prompt(prompt: str) -> str:
    """SHA-256 of the prompt text — feeds frontmatter and args-hash input."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DefaultProfile:
    """Parsed view of the pinned default extraction profile.

    Frozen so the load is unambiguously read-only and so the args-hash
    key-identity comparison happens against a stable dict.

    Attributes:
        profile: Profile identifier (e.g. ``"document-extraction-v1"``).
        model: Default extraction model id.
        dpi: Default render resolution for PDF→image conversion.
        prompt: Default OCR prompt text (raw; hashed at use-time).
        extraction_type: Profile-level extraction-type tag for frontmatter.
    """

    profile: str
    model: str
    dpi: int
    prompt: str
    extraction_type: str

    def as_args_dict_for_hashing(self) -> dict[str, Any]:
        """Convert to the dict shape ``compute_args_hash`` expects.

        Replaces the raw ``prompt`` string with its SHA-256 ``prompt_hash``
        so the comparison key matches the args dict (which carries
        ``prompt_hash`` after the handler pre-hashes the caller's prompt).
        """
        return {
            "model": self.model,
            "dpi": self.dpi,
            "prompt_hash": hash_prompt(self.prompt),
            "extraction_type": self.extraction_type,
        }


@functools.lru_cache(maxsize=1)
def load_default_profile() -> DefaultProfile:
    """Load and cache the pinned default extraction profile.

    Process-lifetime cache. Service restart required after a profile bump
    (v1 → v2 path change) — mirrors the spec's "profile bumps are explicit
    policy changes" contract from §"Pinned default profile".
    """
    profile_path = FILES_ROOT / _DEFAULT_PROFILE_RELATIVE_PATH
    if not profile_path.is_file():
        raise FileNotFoundError(
            f"Default extraction profile not found at {profile_path!s}. "
            f"Expected at cortex://{_DEFAULT_PROFILE_RELATIVE_PATH}.",
        )
    with profile_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(
            f"Default profile root must be a mapping, got {type(data).__name__}.",
        )
    return DefaultProfile(
        profile=data["profile"],
        model=data["model"],
        dpi=data["dpi"],
        prompt=data["prompt"],
        extraction_type=data["extraction_type"],
    )
