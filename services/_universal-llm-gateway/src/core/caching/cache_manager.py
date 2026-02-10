"""
Cache Manager for Universal LLM Gateway Phase 2B
Handles intelligent caching of LLM responses for improved performance.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class CacheEntry:
    """Single cache entry"""

    key: str
    model_id: str
    prompt_hash: str
    response: dict[str, Any]
    created_at: float
    last_accessed: float
    access_count: int
    tokens_saved: int
    cache_hit: bool = True


@dataclass
class CacheStats:
    """Cache statistics"""

    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    tokens_saved: int = 0
    storage_mb: float = 0.0
    hit_ratio: float = 0.0
    entries_count: int = 0


class CacheManager:
    """
    Intelligent cache manager for LLM responses.

    Features:
    - Multiple caching strategies (LRU, Semantic, TTL)
    - Model-aware caching
    - Token usage optimization
    - Cache warming and preloading
    - Statistics and monitoring
    """

    def __init__(
        self,
        max_cache_size: int = 10000,
        max_memory_mb: int = 512,
        default_ttl: int = 3600,  # 1 hour
        enable_semantic_caching: bool = True,
        similarity_threshold: float = 0.95,
    ):
        self.max_cache_size = max_cache_size
        self.max_memory_mb = max_memory_mb
        self.default_ttl = default_ttl
        self.enable_semantic_caching = enable_semantic_caching
        self.similarity_threshold = similarity_threshold

        # Cache storage
        self.cache: dict[str, CacheEntry] = {}
        self.model_caches: dict[str, dict[str, CacheEntry]] = {}

        # Access tracking for LRU
        self.access_order: list[str] = []

        # Statistics
        self.stats = CacheStats()

        # Background cleanup task
        self.cleanup_task = asyncio.create_task(self._cleanup_expired_entries())

        logger.info(
            f"CacheManager initialized: max_size={max_cache_size}, "
            f"max_memory={max_memory_mb}MB, ttl={default_ttl}s, "
            f"semantic_caching={enable_semantic_caching}"
        )

    def _generate_cache_key(
        self, model_id: str, messages: list[dict[str, str]], parameters: dict[str, Any]
    ) -> str:
        """
        Generate a unique cache key for a request.

        Args:
            model_id: Model identifier
            messages: Chat messages
            parameters: Generation parameters

        Returns:
            Unique cache key
        """
        # Create a normalized representation
        cache_data = {
            "model_id": model_id,
            "messages": messages,
            "parameters": {
                k: v
                for k, v in parameters.items()
                if k in ["temperature", "top_p", "max_tokens", "stop"]
            },
        }

        # Sort for consistency
        normalized = json.dumps(cache_data, sort_keys=True)
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _generate_prompt_hash(self, messages: list[dict[str, str]]) -> str:
        """Generate hash for prompt content only (for semantic similarity)"""
        content = " ".join(msg.get("content", "") for msg in messages)
        return hashlib.md5(content.encode()).hexdigest()

    async def get(
        self, model_id: str, messages: list[dict[str, str]], parameters: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Get cached response if available.

        Args:
            model_id: Model identifier
            messages: Chat messages
            parameters: Generation parameters

        Returns:
            Cached response or None
        """
        self.stats.total_requests += 1

        # Generate cache key
        cache_key = self._generate_cache_key(model_id, messages, parameters)

        # Check exact match first
        if cache_key in self.cache:
            entry = self.cache[cache_key]

            # Check if expired
            if self._is_expired(entry):
                await self._remove_entry(cache_key)
                self.stats.cache_misses += 1
                return None

            # Update access info
            entry.last_accessed = time.time()
            entry.access_count += 1

            # Update LRU order
            if cache_key in self.access_order:
                self.access_order.remove(cache_key)
            self.access_order.append(cache_key)

            self.stats.cache_hits += 1
            self.stats.tokens_saved += entry.tokens_saved

            logger.debug(f"Cache hit for model {model_id}, key: {cache_key[:8]}...")
            return entry.response

        # Check semantic similarity if enabled
        if self.enable_semantic_caching:
            similar_response = await self._find_similar_cached_response(
                model_id, messages, parameters
            )
            if similar_response:
                self.stats.cache_hits += 1
                return similar_response

        self.stats.cache_misses += 1
        self._update_hit_ratio()
        return None

    async def put(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        parameters: dict[str, Any],
        response: dict[str, Any],
    ):
        """
        Store response in cache.

        Args:
            model_id: Model identifier
            messages: Chat messages
            parameters: Generation parameters
            response: LLM response to cache
        """
        # Generate cache key
        cache_key = self._generate_cache_key(model_id, messages, parameters)

        # Calculate tokens saved
        usage = response.get("usage", {})
        tokens_saved = usage.get("completion_tokens", 0)

        # Create cache entry
        entry = CacheEntry(
            key=cache_key,
            model_id=model_id,
            prompt_hash=self._generate_prompt_hash(messages),
            response=response,
            created_at=time.time(),
            last_accessed=time.time(),
            access_count=0,
            tokens_saved=tokens_saved,
        )

        # Ensure we have space
        await self._ensure_cache_space()

        # Store entry
        self.cache[cache_key] = entry

        # Update model-specific cache
        if model_id not in self.model_caches:
            self.model_caches[model_id] = {}
        self.model_caches[model_id][cache_key] = entry

        # Update access order
        self.access_order.append(cache_key)

        # Update statistics
        self.stats.entries_count = len(self.cache)
        self._update_storage_stats()

        logger.debug(
            f"Cached response for model {model_id}, key: {cache_key[:8]}..., "
            f"tokens: {tokens_saved}"
        )

    async def _find_similar_cached_response(
        self, model_id: str, messages: list[dict[str, str]], parameters: dict[str, Any]
    ) -> dict[str, Any] | None:
        """
        Find semantically similar cached response.

        Args:
            model_id: Model identifier
            messages: Chat messages
            parameters: Generation parameters

        Returns:
            Similar cached response or None
        """
        if model_id not in self.model_caches:
            return None

        prompt_content = " ".join(msg.get("content", "") for msg in messages).lower()

        # Simple semantic similarity based on word overlap
        # In a production system, you might use embeddings or more sophisticated methods
        best_similarity = 0.0
        best_response = None

        for entry in self.model_caches[model_id].values():
            if self._is_expired(entry):
                continue

            # Check parameter compatibility
            if not self._are_parameters_similar(
                parameters, entry.response.get("parameters", {})
            ):
                continue

            # Calculate similarity (simplified)
            cached_content = " ".join(
                msg.get("content", "") for msg in entry.response.get("messages", [])
            ).lower()

            similarity = self._calculate_text_similarity(prompt_content, cached_content)

            if similarity > best_similarity and similarity >= self.similarity_threshold:
                best_similarity = similarity
                best_response = entry.response

        if best_response:
            logger.debug(
                f"Semantic cache hit for model {model_id}, similarity: {best_similarity:.2f}"
            )

        return best_response

    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score (0.0 to 1.0)
        """
        words1 = set(text1.split())
        words2 = set(text2.split())

        if not words1 and not words2:
            return 1.0
        if not words1 or not words2:
            return 0.0

        intersection = words1.intersection(words2)
        union = words1.union(words2)

        return len(intersection) / len(union)

    def _are_parameters_similar(
        self, params1: dict[str, Any], params2: dict[str, Any]
    ) -> bool:
        """Check if generation parameters are similar enough for cache reuse"""
        important_params = ["temperature", "top_p", "max_tokens"]

        for param in important_params:
            val1 = params1.get(param)
            val2 = params2.get(param)

            if val1 is None or val2 is None:
                continue

            # Allow small differences in temperature and top_p
            if param in ["temperature", "top_p"]:
                if abs(val1 - val2) > 0.1:
                    return False
            else:
                if val1 != val2:
                    return False

        return True

    def _is_expired(self, entry: CacheEntry) -> bool:
        """Check if cache entry is expired"""
        return time.time() - entry.created_at > self.default_ttl

    async def _ensure_cache_space(self):
        """Ensure there's space in the cache by removing old entries if needed"""
        # Check size limit
        if len(self.cache) >= self.max_cache_size:
            await self._evict_lru_entries(self.max_cache_size // 10)  # Remove 10%

        # Check memory limit (simplified)
        if self._estimate_memory_usage() > self.max_memory_mb:
            await self._evict_lru_entries(self.max_cache_size // 20)  # Remove 5%

    async def _evict_lru_entries(self, count: int):
        """Evict least recently used entries"""
        evicted = 0

        while evicted < count and self.access_order:
            oldest_key = self.access_order.pop(0)
            if oldest_key in self.cache:
                await self._remove_entry(oldest_key)
                evicted += 1

        logger.debug(f"Evicted {evicted} LRU cache entries")

    async def _remove_entry(self, cache_key: str):
        """Remove entry from cache"""
        if cache_key not in self.cache:
            return

        entry = self.cache[cache_key]

        # Remove from main cache
        del self.cache[cache_key]

        # Remove from model cache
        if entry.model_id in self.model_caches:
            self.model_caches[entry.model_id].pop(cache_key, None)

        # Remove from access order
        if cache_key in self.access_order:
            self.access_order.remove(cache_key)

        # Update stats
        self.stats.entries_count = len(self.cache)
        self._update_storage_stats()

    async def _cleanup_expired_entries(self):
        """Background task to clean up expired entries"""
        while True:
            try:
                current_time = time.time()
                expired_keys = [
                    key
                    for key, entry in self.cache.items()
                    if current_time - entry.created_at > self.default_ttl
                ]

                for key in expired_keys:
                    await self._remove_entry(key)

                if expired_keys:
                    logger.debug(
                        f"Cleaned up {len(expired_keys)} expired cache entries"
                    )

                # Sleep before next cleanup
                await asyncio.sleep(300)  # Cleanup every 5 minutes

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cache cleanup task: {e}")
                await asyncio.sleep(60)

    def _estimate_memory_usage(self) -> float:
        """Estimate cache memory usage in MB"""
        if not self.cache:
            return 0.0

        # Sample a few entries to estimate average size
        sample_size = min(10, len(self.cache))
        sample_entries = list(self.cache.values())[:sample_size]

        total_size = 0
        for entry in sample_entries:
            # Rough estimation of entry size
            entry_str = json.dumps(asdict(entry))
            total_size += len(entry_str.encode("utf-8"))

        avg_size = total_size / sample_size if sample_size > 0 else 0
        total_estimated = (avg_size * len(self.cache)) / (1024 * 1024)  # Convert to MB

        return total_estimated

    def _update_storage_stats(self):
        """Update storage statistics"""
        self.stats.storage_mb = self._estimate_memory_usage()

    def _update_hit_ratio(self):
        """Update cache hit ratio"""
        if self.stats.total_requests > 0:
            self.stats.hit_ratio = self.stats.cache_hits / self.stats.total_requests

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics"""
        self._update_hit_ratio()
        self._update_storage_stats()

        return {
            "total_requests": self.stats.total_requests,
            "cache_hits": self.stats.cache_hits,
            "cache_misses": self.stats.cache_misses,
            "hit_ratio_percent": self.stats.hit_ratio * 100,
            "tokens_saved": self.stats.tokens_saved,
            "entries_count": self.stats.entries_count,
            "storage_mb": self.stats.storage_mb,
            "max_cache_size": self.max_cache_size,
            "max_memory_mb": self.max_memory_mb,
            "utilization_percent": (
                self.stats.entries_count / self.max_cache_size * 100
            )
            if self.max_cache_size > 0
            else 0,
            "semantic_caching_enabled": self.enable_semantic_caching,
            "model_caches": {
                model_id: len(cache) for model_id, cache in self.model_caches.items()
            },
        }

    async def clear_model_cache(self, model_id: str):
        """Clear cache for a specific model"""
        if model_id not in self.model_caches:
            return

        keys_to_remove = list(self.model_caches[model_id].keys())
        for key in keys_to_remove:
            await self._remove_entry(key)

        logger.info(
            f"Cleared cache for model {model_id}, removed {len(keys_to_remove)} entries"
        )

    async def clear_all(self):
        """Clear all cache entries"""
        self.cache.clear()
        self.model_caches.clear()
        self.access_order.clear()

        # Reset stats
        self.stats = CacheStats()

        logger.info("Cleared all cache entries")

    async def shutdown(self):
        """Shutdown the cache manager"""
        logger.info("Shutting down cache manager")

        # Cancel cleanup task
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass

        # Clear all caches
        await self.clear_all()

        logger.info("Cache manager shutdown completed")
