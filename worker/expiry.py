"""Nightly expiry of removed raw files (Gap 12).

Runs in the worker, which connects as the trusted role (the API's app_api role is held to
one company by RLS, as it should be). Deletes the raw upload of every dataset removed more
than ``data.removed_retention_days`` ago, unless a run's snapshot still references it or
another live dataset of the company has the same bytes. Parquet snapshots are never
touched, so finished runs keep their inputs. Idempotent: rows get ``raw_deleted_at``.
"""

from __future__ import annotations

from typing import Any

import psycopg

from core.logging import get_logger

log = get_logger(__name__)

CANDIDATES = """
SELECT d.id::text, d.company_id::text, d.storage_key FROM raw.dataset d
WHERE d.status = 'removed' AND d.raw_deleted_at IS NULL
  AND d.removed_at < now() - make_interval(days => %s)
  AND NOT EXISTS (SELECT 1 FROM meta.workflow_run r JOIN meta.workspace_snapshot s ON s.id = r.snapshot_id
                  WHERE d.id = ANY(s.dataset_ids))
  AND NOT EXISTS (SELECT 1 FROM raw.dataset o WHERE o.content_sha256 = d.content_sha256
                  AND o.company_id = d.company_id AND o.id <> d.id
                  AND o.status NOT IN ('removed', 'failed', 'duplicate'))
ORDER BY d.removed_at LIMIT 500"""


def expire_removed(conn: psycopg.Connection, store: Any, days: int) -> dict[str, int]:
    rows = conn.execute(CANDIDATES, (int(days),)).fetchall()
    deleted = 0
    for r in rows:
        r = dict(r) if not isinstance(r, dict) else r
        try:
            store.delete_key(r["storage_key"])
        except Exception:  # noqa: BLE001 - retried on the next nightly run
            log.warning("could not delete removed raw file", extra={"dataset_id": r["id"]})
            continue
        with conn.transaction():
            conn.execute("UPDATE raw.dataset SET raw_deleted_at = now() WHERE id = %s AND company_id = %s",
                         (r["id"], r["company_id"]))
        deleted += 1
    return {"candidates": len(rows), "deleted": deleted}
