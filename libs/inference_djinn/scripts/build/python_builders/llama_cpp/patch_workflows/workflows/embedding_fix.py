"""
Patch workflow for batch embedding fix.

Applies to llama-cpp-python v0.3.x where batch embeddings fail.

The issue: embed() method's multi-sequence batching loses embeddings
when intermediate batch decoding occurs.

The fix: Add safety check to process texts one-by-one when batch would
overflow, then merge results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import PatchDefinition, PatchWorkflow

if TYPE_CHECKING:
    from ..registry import PatchRegistry


# The problematic code section in llama.py embed() method
# This is the loop that accumulates sequences into batches
OLD_EMBED_ACCUMULATE = """        # accumulate batches and encode
        for text in inputs:
            tokens = self.tokenize(text.encode("utf-8"))
            if truncate:
                tokens = tokens[:n_batch]

            n_tokens = len(tokens)
            total_tokens += n_tokens

            # check for overrun
            if n_tokens > n_batch:
                raise ValueError(
                    f"Requested tokens ({n_tokens}) exceed batch size of {n_batch}"
                )

            # time to eval batch
            if t_batch + n_tokens > n_batch:
                decode_batch(s_batch)
                s_batch = []
                t_batch = 0
                p_batch = 0

            # add to batch
            self._batch.add_sequence(tokens, p_batch, logits_all)

            # update batch stats
            s_batch.append(n_tokens)
            t_batch += n_tokens
            p_batch += 1

        # hanlde last batch
        decode_batch(s_batch)"""


# Fixed version that handles batch overflow by processing per-text
NEW_EMBED_ACCUMULATE = """        # accumulate batches and encode
        # FIX: Check if any text would cause mid-batch decode (lossy)
        # If so, process each text individually to avoid data loss
        all_token_counts = []
        would_overflow = False
        running_count = 0

        for text in inputs:
            tokens = self.tokenize(text.encode("utf-8"))
            if truncate:
                tokens = tokens[:n_batch]
            n_tok = len(tokens)
            all_token_counts.append(n_tok)
            if running_count + n_tok > n_batch:
                would_overflow = True
            running_count += n_tok
            if running_count > n_batch:
                running_count = n_tok  # Reset after overflow

        if would_overflow and len(inputs) > 1:
            # Process texts individually to avoid batch overflow data loss
            for text in inputs:
                tokens = self.tokenize(text.encode("utf-8"))
                if truncate:
                    tokens = tokens[:n_batch]
                n_tokens = len(tokens)
                total_tokens += n_tokens

                if n_tokens > n_batch:
                    raise ValueError(
                        f"Requested tokens ({n_tokens}) exceed batch size of {n_batch}"
                    )

                self._batch.add_sequence(tokens, 0, logits_all)
                decode_batch([n_tokens])
        else:
            # Original logic for single text or batch that fits
            for text in inputs:
                tokens = self.tokenize(text.encode("utf-8"))
                if truncate:
                    tokens = tokens[:n_batch]

                n_tokens = len(tokens)
                total_tokens += n_tokens

                # check for overrun
                if n_tokens > n_batch:
                    raise ValueError(
                        f"Requested tokens ({n_tokens}) exceed batch size of {n_batch}"
                    )

                # time to eval batch
                if t_batch + n_tokens > n_batch:
                    decode_batch(s_batch)
                    s_batch = []
                    t_batch = 0
                    p_batch = 0

                # add to batch
                self._batch.add_sequence(tokens, p_batch, logits_all)

                # update batch stats
                s_batch.append(n_tokens)
                t_batch += n_tokens
                p_batch += 1

            # handle last batch
            decode_batch(s_batch)"""


EMBEDDING_BATCH_FIX = PatchDefinition(
    file_path="llama_cpp/llama.py",
    old_pattern=OLD_EMBED_ACCUMULATE,
    new_template=NEW_EMBED_ACCUMULATE,
    description="Fix batch embedding data loss on overflow",
    optional=False,
)


# Workflow covering v0.3.x versions
WORKFLOW = PatchWorkflow(
    name="embedding-fix-v0.3",
    version_pattern=r"^0\.3\.\d+",
    patches=[EMBEDDING_BATCH_FIX],
    verified_working=True,  # Mark as verified after testing
    notes=(
        "Fixes batch embedding failure when total tokens exceed n_batch. "
        "Detects overflow condition and processes texts individually."
    ),
)


def register(registry: PatchRegistry) -> None:
    """Register embedding fix workflow."""
    registry.register(WORKFLOW)
