"""Test-only scripted stand-in for OpenRouter chat completions.

* planning calls (response_format json_schema demand_plan_lines): apply every pack factor at
  its value plus ``agent:diwali`` +10% on Home Baking / Cake Décor lines, with exact arithmetic
* import calls: mark every field not_stored
* ask calls with tools: route by keywords to run_demand_plan / apply_edit / query_known, then
  answer with a short sentence once tool results are present
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

FESTIVE = ("Home Baking", "Cake Décor", "Cake Decor", "Colours & Flavours")


def plan_answer(pack: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for ln in pack["lines"]:
        applied = [{"name": f["name"], "value": f["value"], "reason": None} for f in ln["factors"]]
        if (pack.get("plan_category") or "") in FESTIVE:
            applied.append({"name": "agent:diwali", "value": 0.1,
                            "reason": "Diwali in November lifts festive home-baking demand"})
        total = float(ln["base"])
        for f in applied:
            kind = next((p["kind"] for p in ln["factors"] if p["name"] == f["name"]), "multiplier")
            if kind == "multiplier":
                total *= 1 + f["value"]
        for f in applied:
            kind = next((p["kind"] for p in ln["factors"] if p["name"] == f["name"]), "multiplier")
            if kind == "additive":
                total += f["value"]
        lines.append({"line_id": ln["line_id"], "final_dp": max(0, round(total)), "applied_factors": applied,
                      "reason": "trend and agent:diwali as listed" if len(applied) > len(ln["factors"])
                      else "Python factors as derived", "confidence": "medium", "flag_for_review": False})
    return {"lines": lines}


def ask_answer(body: dict[str, Any]) -> dict[str, Any]:
    msgs = body["messages"]
    last_user = next(m["content"] for m in reversed(msgs) if m["role"] == "user").lower()
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    if tool_msgs:
        last = json.loads(tool_msgs[-1]["content"])
        if "error" in last:
            return {"content": f"I could not do that: {last['error']}"}
        if "lines_changed" in last:
            return {"content": f"Applied {last['edit']}: {last['lines_changed']} lines, "
                               f"{last['units_before']} → {last['units_after']} units (version {last['version']})."}
        if last.get("status") == "succeeded":
            return {"content": "The plan is ready; see the report card."}
        return {"content": f"Here is what the data shows ({last.get('row_count', 0)} rows)."}
    if "demand plan" in last_user:
        m = re.search(r"(january|february|march|april|may|june|july|august|september|october|november|december)",
                      last_user)
        return _tool("run_demand_plan", {"target_month": m.group(1) if m else "next month"})
    edit = re.search(r"(increase|reduce|decrease)\s+(.+?)\s+by\s+(\d+)%", last_user)
    if edit:
        sign = 1 if edit.group(1) == "increase" else -1
        target = edit.group(2).strip()
        tgt = {"sku_code": target.upper()} if re.fullmatch(r"[a-z0-9]+", target) else {"plan_category": target.title()}
        return _tool("apply_edit", {"target": tgt, "op": "pct", "value": sign * int(edit.group(3))})
    if "undo" in last_user:
        return _tool("undo_edit", {})
    if "lookup" in last_user:
        return _tool("query_workspace", {"sql": "select platform, count(*) n from po_line group by 1"})
    return _tool("query_known", {"name": "sales_history", "params": {"by": "city"}})


def _tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"content": "", "tool_calls": [{"id": f"call_{name}", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}


class _H(BaseHTTPRequestHandler):
    fail = False

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if _H.fail:
            self._send(503, {"error": "down"})
            return
        rf = body.get("response_format") or {}
        name = (rf.get("json_schema") or {}).get("name")
        if name == "demand_plan_lines":
            user = body["messages"][-1]["content"]
            pack = json.loads(user.split("Evidence pack:\n", 1)[1].split("\n\n", 1)[0])
            msg = {"content": json.dumps(plan_answer(pack))}
        elif name == "import_mapping":
            p = json.loads(body["messages"][-1]["content"])
            msg = {"content": json.dumps({"file_type": p["detection"]["file_type"], "type_confidence": 0.9,
                                          "ask_user": False, "question": None, "sheets": [],
                                          "fields": [{"sheet": f["sheet"], "source_field": f["source_field"],
                                                      "variable": None, "tier": None, "category": None,
                                                      "state": "not_stored", "confidence": 0.6,
                                                      "new_variable": None} for f in p["fields"]]})}
        elif body.get("tools"):
            msg = ask_answer(body)
        else:
            msg = {"content": "The plan totals the figures shown. See the reasoning report for the per-SKU detail."}
        self._send(200, {"choices": [{"message": {"role": "assistant", **msg}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 10, "cost": 0.0}})

    def _send(self, code: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a: object) -> None:
        return


def serve(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    import sys
    import time

    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 54322)
    while True:
        time.sleep(3600)
