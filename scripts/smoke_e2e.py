"""End-to-end smoke test against a running API + worker.

    python scripts/smoke_e2e.py --api http://localhost:8000 --token $TOKEN --data ./sample-data

Walks the product flow: create chat → upload files → wait for proposals → save mappings
(accept proposals; unresolved fields not stored) → wait for ready → ask a data question →
"Create a demand plan for November" → edit "increase Cake Mixes by 30%" → undo → exports.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx


def sse(client: httpx.Client, url: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    with client.stream("POST", url, json=body, timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--token", required=True)
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--target", default="November")
    a = p.parse_args()
    c = httpx.Client(base_url=a.api, headers={"Authorization": f"Bearer {a.token}"}, timeout=120)

    sid = c.post("/sessions", json={"title": "Smoke test"}).raise_for_status().json()["id"]
    print("chat", sid)
    files = [f for f in sorted(a.data.iterdir()) if f.suffix.lower() in (".csv", ".xlsx")]
    up = c.post(f"/sessions/{sid}/imports",
                files=[("files", (f.name, f.read_bytes())) for f in files]).raise_for_status().json()
    ids = [f["dataset_id"] for f in up["files"] if f["status"] == "uploaded"]
    print("uploaded", len(ids), [f["status"] for f in up["files"]])

    def wait(status: set[str], timeout: float = 300) -> list[dict[str, Any]]:
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = [c.get(f"/imports/{d}").json() for d in ids]
            if all(s["status"] in status | {"failed"} for s in st):
                bad = [s for s in st if s["status"] == "failed"]
                if bad:
                    raise RuntimeError(f"import failed: {[(b['file_name'], b['error']) for b in bad]}")
                return st
            time.sleep(2)
        raise TimeoutError(f"imports not {status}")

    for s in wait({"parsed"}):
        print(f"  {s['file_name']}: {s['status']} type={s['file_type']} rows={s['rows_read']} {s.get('error') or ''}")
    for d in ids:
        m = c.get(f"/imports/{d}/mapping").raise_for_status().json()
        fields = []
        for f in m["proposal"]["fields"]:
            state = f["state"]
            if state == "needs_decision":
                state = "not_stored"
            fields.append({"sheet": f["sheet"], "source_field": f["source_field"], "variable": f["variable"],
                           "category": f["category"], "state": state, "new_variable": f.get("new_variable")})
        saved = c.put(f"/imports/{d}/mapping", json={"fields": fields}).raise_for_status().json()
        print(f"  saved v{saved['mapping_version']}: {saved['variables_stored']} stored, "
              f"{len(saved['fields_not_stored'])} not stored")
    for s in wait({"ready"}, 600):
        print(f"  {s['file_name']}: {s['status']}")

    sug = c.get(f"/sessions/{sid}/suggestions").raise_for_status().json()
    print("suggestions", {k: sug.get(k) for k in ("plan", "why", "data")})

    ev = sse(c, f"/sessions/{sid}/messages", {"content": "Show sales by city"})
    print("ask:", [e["type"] for e in ev])
    ev = sse(c, f"/sessions/{sid}/messages", {"content": "Do a direct lookup of PO counts"})
    print("lookup evidence:", [e.get("label") for e in ev if e["type"] == "evidence"])

    ev = sse(c, f"/sessions/{sid}/messages", {"content": f"Create a demand plan for {a.target}"})
    run_id = next(e["run_id"] for e in ev if e["type"] == "run_queued")
    print("plan events:", sorted({e["type"] for e in ev}))
    t0 = time.time()
    while True:
        run = c.get(f"/runs/{run_id}").raise_for_status().json()
        if run["status"] in ("succeeded", "failed", "cancelled") or time.time() - t0 > 900:
            break
        time.sleep(3)
    if run["status"] == "failed" and "crosswalk" in (run.get("error") or ""):
        print("run stopped for crosswalk review:", run["error"][:200])
        cw = c.get("/crosswalk", params={"status": "review"}).json()
        confirm = [{"platform": r["platform"], "platform_item_id": r["platform_item_id"], "sku_code": r["sku_code"]}
                   for r in cw["rows"] if r["match_method"] == "review" and r["sku_code"]]
        print("confirming", len(confirm), "review rows:", c.post("/crosswalk", json=confirm).json())
        run_id = c.post(f"/runs/{run_id}/retry").raise_for_status().json()["run_id"]
        t0 = time.time()
        while True:
            run = c.get(f"/runs/{run_id}").raise_for_status().json()
            if run["status"] in ("succeeded", "failed", "cancelled") or time.time() - t0 > 900:
                break
            time.sleep(3)
    print("run", run["status"], run.get("error"))
    for sct in run["sections"]:
        print(f"  [{sct['status']}] {sct['section']}: {(sct['summary'] or '')[:140]}")
    if run["status"] != "succeeded":
        return 1
    card = run["report_card"]
    print("card", {k: card[k] for k in ("target_month", "version", "dp_total", "change_pct", "lines", "agent_fallback")})
    print("overview:", card["overview"])

    ev = sse(c, f"/sessions/{sid}/messages", {"content": "increase Cake Mixes by 30%"})
    print("edit reply:", "".join(e.get("text", "") for e in ev if e["type"] == "message_delta"))
    v = c.get(f"/runs/{run_id}/versions").json()
    print("versions", [(x["version"], x["is_current"]) for x in v])
    rs = c.get(f"/runs/{run_id}/reasoning", params={"page_size": 5}).json()
    print("reasoning rows", rs["total"], "version", rs["version"])
    plan_x = c.get(f"/runs/{run_id}/export/plan.xlsx")
    reas_x = c.get(f"/runs/{run_id}/export/reasoning.xlsx")
    print("exports", plan_x.status_code, len(plan_x.content), reas_x.status_code, len(reas_x.content))
    out = Path("out-smoke")
    out.mkdir(exist_ok=True)
    (out / "plan.xlsx").write_bytes(plan_x.content)
    (out / "reasoning.xlsx").write_bytes(reas_x.content)
    ev = sse(c, f"/sessions/{sid}/messages", {"content": "undo"})
    v = c.get(f"/runs/{run_id}/versions").json()
    print("after undo", [(x["version"], x["is_current"]) for x in v])
    return 0


if __name__ == "__main__":
    sys.exit(main())
