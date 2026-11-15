"""Scratch: capture imprint route 422 envelopes (baseline diagnosis only)."""
from __future__ import annotations

import json
import sys

from fastapi.testclient import TestClient

# Reuse pytest conftest fixtures via pytest's fixture request pattern.
import pytest


@pytest.fixture
def _client(cortex_client: TestClient) -> TestClient:
    return cortex_client


def test_capture_bodies(cortex_client: TestClient, migrated_conn) -> None:
  migrated_conn.execute(
      "INSERT INTO entities (id, type, name) VALUES ('todo:ship', 'todo', 'Ship')"
  )
  migrated_conn.execute(
      "INSERT INTO entities (id, type, name) VALUES ('matter:estate-2024', 'matter', 'Estate 2024')"
  )
  migrated_conn.commit()
  patch = {
      "@context": "cortex.life/v1",
      "@graph": [{"@id": "todo:new-item", "@type": "todo", "name": "New item"}],
  }
  pr = cortex_client.post("/graph/imprint/propose", json={"patch": patch})
  cr = cortex_client.post(
      "/graph/imprint/commit", json={"proposal_id": pr.json().get("proposal_id")}
  )
  rr = cortex_client.post("/graph/imprint/remember", json={"patch": patch})
  out = {
      "propose_status": pr.status_code,
      "propose_body": pr.json(),
      "commit_status": cr.status_code,
      "commit_body": cr.json(),
      "remember_status": rr.status_code,
      "remember_body": rr.json(),
  }
  path = __file__.replace("capture_422_bodies.py", "422-envelopes.json")
  with open(path, "w", encoding="utf-8") as fh:
      json.dump(out, fh, indent=2)
  print(json.dumps(out, indent=2))
  assert False, "intentional fail to print bodies in log"


if __name__ == "__main__":
  raise SystemExit(pytest.main([__file__, "-s", "--tb=no", "-q"]))