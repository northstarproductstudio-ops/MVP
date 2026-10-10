"""SKU crosswalk review: read, download the XLSX draft, upload corrections or confirm rows."""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from api.deps import CurrentUser, pool
from core.db import tenant_transaction

router = APIRouter(tags=["crosswalk"])
DRAFT_COLS = ["platform", "platform_item_id", "platform_name", "proposed_sku_code", "score", "match_method",
              "candidate_2", "candidate_3", "confirm"]


@router.get("/crosswalk")
async def get_crosswalk(user: CurrentUser, status: str | None = None) -> dict[str, Any]:
    async with tenant_transaction(pool(), user.company_id) as cur:
        await cur.execute("""SELECT platform, platform_item_id, platform_name, sku_code, match_method,
                             score::float AS score, candidates, confirmed_at FROM core.sku_crosswalk
                             ORDER BY match_method, platform, platform_name""")
        rows = [dict(r) for r in await cur.fetchall()]
    if status == "review":
        rows = [r for r in rows if r["match_method"] in ("review", "unmatched")]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["match_method"]] = counts.get(r["match_method"], 0) + 1
    return {"counts": counts, "rows": rows}


@router.get("/crosswalk/draft.xlsx")
async def draft(user: CurrentUser) -> Response:
    data = await get_crosswalk(user)
    out = []
    for r in data["rows"]:
        cands = r["candidates"] or []
        if isinstance(cands, str):
            cands = json.loads(cands)
        out.append({"platform": r["platform"], "platform_item_id": r["platform_item_id"],
                    "platform_name": r["platform_name"], "proposed_sku_code": r["sku_code"],
                    "score": r["score"], "match_method": r["match_method"],
                    "candidate_2": cands[1]["sku_code"] if len(cands) > 1 else None,
                    "candidate_3": cands[2]["sku_code"] if len(cands) > 2 else None,
                    "confirm": "yes" if r["confirmed_at"] or r["match_method"] in ("ean", "manual") else ""})
    buf = io.BytesIO()
    pd.DataFrame(out, columns=DRAFT_COLS).to_excel(buf, index=False, sheet_name="Crosswalk")
    return Response(buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="sku-crosswalk-draft.xlsx"'})


class Confirm(BaseModel):
    platform: str
    platform_item_id: str
    sku_code: str | None


@router.post("/crosswalk")
async def upload(request: Request, user: CurrentUser, file: UploadFile | None = File(default=None)) -> dict[str, Any]:
    """Either an XLSX with the draft columns (rows with confirm = yes are applied), or a JSON
    list of {platform, platform_item_id, sku_code} confirmations."""
    if file is not None:
        df = pd.read_excel(io.BytesIO(await file.read()))
        missing = {"platform", "platform_item_id", "proposed_sku_code", "confirm"} - set(df.columns)
        if missing:
            raise HTTPException(400, f"missing columns: {sorted(missing)}")
        df = df[df["confirm"].astype(str).str.strip().str.lower().isin(["yes", "y", "true", "1"])]
        items = [Confirm(platform=r.platform, platform_item_id=str(r.platform_item_id),
                         sku_code=None if pd.isna(r.proposed_sku_code) else str(r.proposed_sku_code))
                 for r in df.itertuples()]
    else:
        body = await request.json()
        items = [Confirm.model_validate(x) for x in (body if isinstance(body, list) else [body])]
    async with tenant_transaction(pool(), user.company_id) as cur:
        known = set()
        await cur.execute("SELECT DISTINCT sku_code FROM core.sellin_line UNION SELECT sku_code FROM core.sku_master")
        known = {r["sku_code"] for r in await cur.fetchall()}
        bad = [i.sku_code for i in items if i.sku_code and i.sku_code not in known]
        if bad:
            raise HTTPException(400, f"Unknown SKU codes: {sorted(set(bad))[:10]}")
        for i in items:
            await cur.execute(
                """INSERT INTO core.sku_crosswalk (company_id, platform, platform_item_id, sku_code, match_method,
                   score, confirmed_by, confirmed_at) VALUES (%s,%s,%s,%s,'manual',1,%s,now())
                   ON CONFLICT (company_id, platform, platform_item_id) DO UPDATE SET sku_code=excluded.sku_code,
                   match_method=CASE WHEN excluded.sku_code IS NULL THEN 'unmatched' ELSE 'manual' END,
                   confirmed_by=excluded.confirmed_by, confirmed_at=excluded.confirmed_at""",
                (user.company_id, i.platform, i.platform_item_id, i.sku_code, user.user_id))
        # snapshots must pick up the new maps: clear current snapshot of the company's chats
        await cur.execute("UPDATE core.chat_session SET current_snapshot_id = NULL")
    return {"confirmed": len(items)}
