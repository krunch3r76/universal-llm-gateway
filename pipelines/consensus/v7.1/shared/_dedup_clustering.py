"""
Clustering operations for deduplication.

Uses complete-linkage hierarchical clustering to ensure ALL pairs
within a cluster exceed the similarity threshold.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from ._dedup_polarity import NegationPatternDetector, split_cluster_by_polarity


def cluster_similar(
    statements: list[str],
    embeddings: np.ndarray,
    threshold: float,
    *,
    polarity_aware: bool = True,
) -> tuple[list[set[int]], dict[str, Any]]:
    """
    Cluster similar statements using complete-linkage hierarchical clustering.

    Complete linkage ensures ALL pairs within a cluster exceed the threshold,
    preventing transitivity collapse where A~B and B~C incorrectly merges A~C.

    When polarity_aware=True (default), clusters are refined to split
    semantically similar statements that make opposing claims.

    Args:
        statements: List of statement texts
        embeddings: Normalized embedding vectors
        threshold: Minimum cosine similarity for clustering
        polarity_aware: Split clusters with polarity conflicts (default True)

    Returns:
        Tuple of (clusters, stats) where clusters is list of index sets
    """
    n = len(statements)

    if n == 0:
        return [], _empty_stats()
    if n == 1:
        return [{0}], _empty_stats()

    sim_matrix = embeddings @ embeddings.T

    similarities = []
    for i in range(n):
        for j in range(i + 1, n):
            similarities.append(float(sim_matrix[i, j]))

    dist_matrix = 1.0 - sim_matrix
    np.fill_diagonal(dist_matrix, 0.0)
    dist_matrix = np.maximum(dist_matrix, 0.0)

    condensed = squareform(dist_matrix, checks=False)
    z_matrix = linkage(condensed, method="complete")

    distance_threshold = 1.0 - threshold
    labels = fcluster(z_matrix, t=distance_threshold, criterion="distance")

    clusters: list[set[int]] = []
    label_to_cluster: dict[int, set[int]] = {}
    for idx, label in enumerate(labels):
        if label not in label_to_cluster:
            label_to_cluster[label] = set()
            clusters.append(label_to_cluster[label])
        label_to_cluster[label].add(idx)

    above = [s for s in similarities if s >= threshold]
    stats = {
        "pairs_compared": len(similarities),
        "pairs_above_threshold": len(above),
        "similarity_min": round(min(similarities), 3) if similarities else 0,
        "similarity_max": round(max(similarities), 3) if similarities else 0,
        "similarity_mean": round(sum(similarities) / len(similarities), 3)
        if similarities
        else 0,
        "linkage_method": "complete",
    }

    if polarity_aware:
        clusters, polarity_stats = _refine_clusters_by_polarity(clusters, statements)
        stats["polarity_splits"] = polarity_stats["splits"]
        stats["polarity_aware"] = True
    else:
        stats["polarity_aware"] = False

    return clusters, stats


def _empty_stats() -> dict[str, Any]:
    """Return empty stats for edge cases."""
    return {
        "pairs_compared": 0,
        "pairs_above_threshold": 0,
        "similarity_min": 0,
        "similarity_max": 0,
        "similarity_mean": 0,
        "linkage_method": "complete",
    }


def _refine_clusters_by_polarity(
    clusters: list[set[int]],
    statements: list[str],
) -> tuple[list[set[int]], dict[str, Any]]:
    """
    Refine clusters by splitting polarity conflicts.

    Invariant: forall resulting cluster C: no (i, j) in C with polarity_conflict(i, j)
    """
    detector = NegationPatternDetector()
    refined = []
    total_splits = 0

    for cluster in clusters:
        subclusters = split_cluster_by_polarity(cluster, statements, detector)
        refined.extend(subclusters)
        if len(subclusters) > 1:
            total_splits += 1

    return refined, {"splits": total_splits}
