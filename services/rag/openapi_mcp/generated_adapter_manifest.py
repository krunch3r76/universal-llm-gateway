"""Generated MCP adapter manifest — do not edit by hand.

Regenerate:
  python scripts/openapi_mcp_codegen.py --write --service rag
  python scripts/openapi_mcp_codegen.py --check --service rag
"""

from __future__ import annotations

OPENAPI_SHA256 = "dff92c469e36c4d244bd0c22b01a6672f489491c4d7d583355a9b98ff91fef52"
FACADE_TOOL = "rag"
SERVED_OPS: dict[str, dict[str, str]] = {
    "coverage": {
        "method": "GET",
        "path": "/coverage",
        "operation_id": "get_coverage_coverage_get",
    },
    "delete_directory": {
        "method": "DELETE",
        "path": "/directory",
        "operation_id": "delete_directory_directory_delete",
    },
    "delete_source": {
        "method": "DELETE",
        "path": "/source",
        "operation_id": "delete_source_source_delete",
    },
    "orphaned_articles": {
        "method": "GET",
        "path": "/orphaned_articles",
        "operation_id": "get_orphaned_articles_orphaned_articles_get",
    },
    "refresh_hints": {
        "method": "POST",
        "path": "/refresh_corpus_hints",
        "operation_id": "refresh_corpus_hints_refresh_corpus_hints_post",
    },
    "upsert_article": {
        "method": "POST",
        "path": "/article",
        "operation_id": "upsert_article_article_post",
    },
}
NON_BINDING_PATH_FINGERPRINTS: dict[str, str] = {
    "@components": "43fb941deb902294d25a0fdf2c11abd17fb3fd062de792c3e60f9b1c68b61c49",
    "@info": "77e496792f0915708096b3bac40c1ab86b45641b4ff6231aa452bf8f653d6852",
    "DELETE /indexing_failures/{source}": "e30c51d4c872392e7142ecc9a1286956eaefc0cabfa7302cea0078cc488c2be7",
    "GET /articles": "07c287482f97680d989f70f87c9a6bbfe69e29a845742091e1ec29d82a5e410d",
    "GET /extraction/failed": "9abb140da01379e9dd59aaa1a1f0125fdf58ba75dcc6ff8783bdae9c88f24155",
    "GET /extraction/queue": "461f98848b530660625995e6961ae0a3e45b70b0b7d37b919676f5a0c5a376be",
    "GET /extraction_export": "0015593d6ee73d6ba87e62f269d11bac5bb2931ce8bc2b7e5928c9cf9c82c803",
    "GET /health": "9bbeaf9dbe0c09e70c49b0c75a3ce2b8960df0ca3fbb2937b0b1ce0e049a1896",
    "GET /indexing/status": "4f05ee9b2084a44dbdf54736882f9957b351a0a701d3705fc6609511e1cf4507",
    "GET /indexing_failures": "41a8954eaaeb321cef1141011675a235d5fcfa9541de69f30ddfc7bc31378628",
    "GET /scopes": "7dc907c53b25cc5ba101a22d812c4225571ef85d0ea6a65113fd410c9795f3c9",
    "GET /source": "db468b078c98c2f09372c6390b538d0862a482c4c19c947d14d56fd9ee3968ee",
    "GET /source-status": "f01de87cfb111ff68b8471c4b2a2f344387b473b64182fbce9365159cb66d41b",
    "GET /sources": "e74e526814957b2b9bd92c2b44651d1ffffe0658477430352830cefe63d943e4",
    "GET /stats": "09b4edff84e00f31246c02f6f3726537849301423007a2d72471c7bcd13a3331",
    "GET /watch/status": "f529ec2c15ca078a4c459b0e75a99382ae73523899e3f1916eaf682d1d9ebe05",
    "POST /chunks_by_index": "73c7547631f912aeaa9645116b6b90a6ba5e9a0610eebb0403b170d2e5429261",
    "POST /clear_directory": "2ea5ef0c442855eced7e7b925ee7cac8005c178497ca5385c88cdcec2a04c115",
    "POST /embed_batch": "a006a4f7ee08f105e0035537fa7df4f511247f63cab17760d95ac58d58ec63d0",
    "POST /index": "13a4dfd0e6fea6dda9b54cd4ac633cd83b433c8448de82d6348ec903f335addb",
    "POST /index_directory": "f74820c852e0cab38c80b7dc198f21b0abac51454cb7bdd595e1de78ecd64e1d",
    "POST /indexing_failures/{source}/retry": "8c678cf32d8174b6b4f7a313227cbd5dcb91649e0b8d816dfdb3cd185cce5f9c",
    "POST /reindex": "1b36d91a11cd5d0577bd3f8b44b7f7635b542d72a541d0d4f322d05565c0ccf0",
    "POST /reindex_directory": "66139fe11cbe11be26fa78cc70423c63153f3e70377b794890567fbe87b4196b",
    "POST /scopes": "561449b69e4cc9319319c645e62ae31411fd8bceaea11012de85db4d6902178a",
    "POST /search": "f677d27fc9ab0517737a5f9a5db9a10d02b4b7a4ce920bf052456ff06747ec9f",
}
