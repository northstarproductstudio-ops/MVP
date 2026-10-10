"""Import (MVP-01), proposed mapping (MVP-04), saved mapping (MVP-09) and file-type fixes."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from api.deps import CurrentUser, pool, store
from core.db import tenant_transaction
from core.domain.adapters import REGISTRY
from core.domain.variables import catalogue, categories
from core.models.imports import MappingSave
from core.storage import upload_key

router = APIRouter(tags=["imports"])
MAX_FILES = 10
ALLOWED = (".csv", ".xlsx", ".xlsm", ".xls")
CHUNK = 1024 * 1024


@router.post("/sessions/{session_id}/imports")
async def upload(session_id: str, user: CurrentUser, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(400, f"Add between 1 and {MAX_FILES} files")
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT 1 FROM core.chat_session WHERE id=%s", (session_id,))
        if not await cur.fetchone():
            raise HTTPException(404, "chat not found")
        await cur.execute("SELECT count(*) AS n FROM raw.dataset WHERE session_id=%s AND status NOT IN "
                          "('failed','duplicate')", (session_id,))
        if (await cur.fetchone())["n"] + len(files) > MAX_FILES:
            raise HTTPException(400, f"A chat holds at most {MAX_FILES} files")
    out = []
    for f in files:
        name = f.filename or "upload"
        if not name.lower().endswith(ALLOWED):
            out.append({"file_name": name, "status": "failed", "error": "Only CSV and XLSX files are supported"})
            continue
        # hash first (streamed from the spooled upload), then stream to Storage
        h = hashlib.sha256()
        size = 0
        while chunk := await f.read(CHUNK):
            h.update(chunk)
            size += len(chunk)
        sha = h.hexdigest()
        async with tenant_transaction(pool(), user.company_id) as cur:
            await cur.execute("SELECT id::text, file_name FROM raw.dataset WHERE content_sha256=%s AND status NOT IN "
                              "('failed','duplicate')", (sha,))
            dup = await cur.fetchone()
        if dup:
            out.append({"file_name": name, "status": "duplicate", "dataset_id": dup["id"],
                        "error": f"Same content as {dup['file_name']}; not imported twice"})
            continue
        key = upload_key(user.company_id, session_id, sha, name)
        await f.seek(0)

        def chunks(fh=f.file):  # sync generator over the spooled temp file
            while True:
                b = fh.read(CHUNK)
                if not b:
                    return
                yield b

        import asyncio

        try:
            await asyncio.to_thread(store().put_stream, key, chunks())
        except Exception as exc:  # noqa: BLE001
            if "write-once" not in str(exc):
                raise HTTPException(502, "Could not store the file; please retry") from None
        async with tenant_transaction(pool(), user.company_id) as cur:
            await cur.execute(
                """INSERT INTO raw.dataset (company_id, session_id, file_name, file_size, content_sha256, storage_key)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id::text""",
                (user.company_id, session_id, name, size, sha, key))
            did = (await cur.fetchone())["id"]
            await cur.execute("INSERT INTO meta.job_queue (company_id, session_id, dataset_id, kind) "
                              "VALUES (%s,%s,%s,'import')", (user.company_id, session_id, did))
        out.append({"file_name": name, "status": "uploaded", "dataset_id": did, "size": size})
    return {"files": out}


@router.get("/imports/{dataset_id}")
async def import_progress(dataset_id: str, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("""SELECT id::text, file_name, file_size, status, file_type, type_score, type_source,
                             rows_read, warnings, progress, error, proposal IS NOT NULL AS has_proposal
                             FROM raw.dataset WHERE id=%s""", (dataset_id,))
        d = await cur.fetchone()
    if not d:
        raise HTTPException(404, "file not found")
    d = dict(d)
    d["type_name"] = REGISTRY[d["file_type"]].name if d["file_type"] in REGISTRY else d["file_type"]
    return d


@router.get("/imports/{dataset_id}/mapping")
async def get_mapping(dataset_id: str, user: CurrentUser) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT id::text, file_name, status, file_type, proposal, rows_read FROM raw.dataset "
                          "WHERE id=%s", (dataset_id,))
        d = await cur.fetchone()
        await cur.execute("SELECT name, category, data_type, tier, level FROM core.variable ORDER BY tier, category, name")
        company_vars = [dict(r) for r in await cur.fetchall()]
    if not d:
        raise HTTPException(404, "file not found")
    if not d["proposal"]:
        raise HTTPException(409, "The file is still being read")
    vars_ = {v.name: {"name": v.name, "category": v.category, "data_type": v.data_type, "tier": v.tier,
                      "level": v.level} for v in catalogue().values()}
    for v in company_vars:
        vars_.setdefault(v["name"], v)
    return {"dataset_id": d["id"], "file_name": d["file_name"], "status": d["status"], "rows_read": d["rows_read"],
            "proposal": d["proposal"], "variables": list(vars_.values()),
            "categories": sorted(set(categories()) | {v["category"] for v in company_vars}),
            "file_types": [{"key": k, "name": a.name} for k, a in REGISTRY.items()]}


class TypeFix(BaseModel):
    file_type: str
    custom_name: str | None = None


@router.patch("/imports/{dataset_id}/type")
async def set_type(dataset_id: str, body: TypeFix, user: CurrentUser) -> dict[str, Any]:
    if body.file_type not in REGISTRY:
        raise HTTPException(400, "unknown file type")
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("UPDATE raw.dataset SET file_type=%s, type_source='user', status='uploaded', proposal=NULL "
                          "WHERE id=%s RETURNING session_id::text", (body.file_type, dataset_id))
        r = await cur.fetchone()
        if not r:
            raise HTTPException(404, "file not found")
        if body.file_type == "custom" and body.custom_name:
            await cur.execute("INSERT INTO core.file_type (company_id, key, name, is_custom) VALUES (%s,%s,%s,true) "
                              "ON CONFLICT DO NOTHING", (user.company_id, f"custom:{body.custom_name}",
                                                          body.custom_name))
        await cur.execute("INSERT INTO meta.job_queue (company_id, session_id, dataset_id, kind) VALUES "
                          "(%s,%s,%s,'import')", (user.company_id, r["session_id"], dataset_id))
    return {"dataset_id": dataset_id, "file_type": body.file_type, "status": "uploaded"}


@router.put("/imports/{dataset_id}/mapping")
async def save_mapping(dataset_id: str, body: MappingSave, user: CurrentUser) -> dict[str, Any]:
    cat = catalogue()
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("SELECT id::text, session_id::text, file_type, proposal, warnings FROM raw.dataset "
                          "WHERE id=%s FOR UPDATE", (dataset_id,))
        d = await cur.fetchone()
        if not d or not d["proposal"]:
            raise HTTPException(409, "No proposed mapping to save")
        proposal = {(f["sheet"], f["source_field"]): f for f in d["proposal"]["fields"]}
        used: dict[tuple[str, str], str] = {}
        for f in body.fields:
            if f.variable and f.variable in cat and cat[f.variable].tier == 1 and f.state != "not_stored":
                k = (f.sheet, f.variable)
                if k in used:
                    raise HTTPException(400, f"Two fields map to {f.variable}: {used[k]} and {f.source_field}")
                used[k] = f.source_field
        await cur.execute("SELECT coalesce(max(version), 0) + 1 AS v FROM core.field_mapping_version WHERE file_type=%s",
                          (d["file_type"],))
        version = (await cur.fetchone())["v"]
        await cur.execute("INSERT INTO core.field_mapping_version (company_id, file_type, version, created_by, note) "
                          "VALUES (%s,%s,%s,%s,%s) RETURNING id::text",
                          (user.company_id, d["file_type"], version, user.user_id, body.note))
        vid = (await cur.fetchone())["id"]
        stored, not_stored, custom, new_cats = [], [], 0, set()
        known_cats = set(categories())
        for f in body.fields:
            prop = proposal.get((f.sheet, f.source_field), {})
            if f.state == "needs_decision":
                raise HTTPException(400, f"{f.source_field} still needs a decision")
            if f.variable and f.state != "not_stored":
                state = "custom" if f.state == "custom" else "confirmed"
                custom += state == "custom"
                if f.variable not in cat:
                    category = f.category or (f.new_variable.category if f.new_variable else "Other")
                    await cur.execute(
                        """INSERT INTO core.variable (company_id, name, category, data_type, tier, level, origin)
                           VALUES (%s,%s,%s,%s,2,'none',%s) ON CONFLICT DO NOTHING""",
                        (user.company_id, f.variable, category,
                         (f.new_variable.data_type if f.new_variable else prop.get("inferred_type")) or "text",
                         "user" if f.state == "custom" else "agent"))
                    if category not in known_cats:
                        new_cats.add(category)
                stored.append((f, state))
            else:
                not_stored.append(f.source_field)
            await cur.execute(
                """INSERT INTO core.field_mapping (company_id, version_id, source_sheet, source_field, variable, category,
                   state, confidence, sample_values) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (user.company_id, vid, f.sheet, f.source_field, f.variable if f.state != "not_stored" else None,
                 f.category, "not_stored" if f.state == "not_stored" or not f.variable else
                 ("custom" if f.state == "custom" else "confirmed"), prop.get("confidence"),
                 Jsonb(prop.get("sample_values") or [])))
        await cur.execute("UPDATE raw.dataset SET status='mapped', mapping_version_id=%s WHERE id=%s", (vid, dataset_id))
        await cur.execute("INSERT INTO meta.job_queue (company_id, session_id, dataset_id, kind) "
                          "VALUES (%s,%s,%s,'materialize')", (user.company_id, d["session_id"], dataset_id))
        await cur.execute("SELECT match_method, count(*) AS n FROM core.sku_crosswalk GROUP BY 1")
        cw = {r["match_method"]: r["n"] for r in await cur.fetchall()}
    by_cat: dict[str, list[str]] = defaultdict(list)
    for f, _ in stored:
        c = cat[f.variable].category if f.variable in cat else (f.category or "Other")
        by_cat[c].append(f.variable)
    return {"dataset_id": dataset_id, "mapping_version": version, "variables_stored": len(stored),
            "categories": len(by_cat), "new_categories": sorted(new_cats), "custom_mappings": custom,
            "fields_not_stored": not_stored,
            "available": [{"category": k, "variables": sorted(set(v))} for k, v in sorted(by_cat.items())],
            "warnings": d["warnings"] or [], "crosswalk": cw}
