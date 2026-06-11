"""Canonical compound entity id normalization at create intake."""

from __future__ import annotations

from fastapi import HTTPException, status


def canonicalize_entity_id(raw_id: str, entity_type: str) -> str:
    """Return ``type:slug`` form, canonicalizing bare slugs or rejecting mismatches."""
    prefix = f"{entity_type}:"
    if raw_id.startswith(prefix):
        if not raw_id[len(prefix) :]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Entity id {raw_id!r} has an empty slug after the type prefix.",
            )
        return raw_id
    if ":" in raw_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Entity id {raw_id!r} carries a type-prefix that does not match "
                f"type={entity_type!r}. Pass id='{entity_type}:<slug>' (compound) or "
                f"a bare slug (a bare slug is canonicalized to '{entity_type}:<slug>')."
            ),
        )
    return prefix + raw_id
