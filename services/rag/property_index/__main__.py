"""CLI entry point for ``python -m services.rag.property_index --rescope``.

Operator maintenance tool: loads the RAG config, starts a standalone
PropertyIndex, re-resolves every property row's scope via
``config.get_scope_for_path`` (``rescope_all``), then prints the updated count
and per-scope row totals before stopping the index. Any other invocation
prints usage and exits with status 1.
"""

if __name__ == "__main__":
    import sys

    if "--rescope" in sys.argv:
        from services.rag.config import load_config
        from services.rag.property_index import PropertyIndex

        config = load_config()
        idx = PropertyIndex()
        import asyncio

        asyncio.run(idx.start())
        try:
            total, updated = idx.rescope_all(config.get_scope_for_path)
            print(f"Rescoped {updated}/{total} property entries")
            conn = idx._ensure_conn()
            for row in conn.execute(
                "SELECT scope, COUNT(*) FROM properties GROUP BY scope ORDER BY scope"
            ).fetchall():
                print(f"  {row[0]}: {row[1]}")
        finally:
            asyncio.run(idx.stop())
    else:
        print("Usage: python -m services.rag.property_index --rescope")
        sys.exit(1)
