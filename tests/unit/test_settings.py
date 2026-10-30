from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from api.services.settings import settings_view, with_agent
from core.plan_config import PlanConfig


def test_with_agent_changes_only_the_switch_and_round_trips():
    base = PlanConfig.load().as_dict()
    off = with_agent(base, False)
    assert off.get("agent.enabled") is False
    assert {k: v for k, v in off.as_dict().items() if k != "agent"} == {k: v for k, v in base.items() if k != "agent"}
    assert {k: v for k, v in off.as_dict()["agent"].items() if k != "enabled"} == \
        {k: v for k, v in base["agent"].items() if k != "enabled"}
    assert with_agent(off.as_dict(), base["agent"]["enabled"]).sha256 == PlanConfig(base).sha256


def test_settings_view_reports_switch_scope_and_model(monkeypatch):
    monkeypatch.setenv("LLM_DATA_COLLECTION", "allow")
    row = {"id": "c1", "company_id": "co", "created_at": datetime(2026, 10, 30, tzinfo=timezone.utc), "note": "n",
           "config": with_agent(PlanConfig.load().as_dict(), False).as_dict()}
    s = SimpleNamespace(llm_model="nvidia/nemotron-3.5-super-vl:free", openrouter_base_url="https://openrouter.ai/api/v1")
    v = settings_view(row, s)
    assert v["planning_agent"] == {"enabled": False, "config_version_id": "c1", "scope": "workspace",
                                   "updated_at": "2026-10-30T00:00:00+00:00", "note": "n"}
    assert v["model"] == {"id": "nvidia/nemotron-3.5-super-vl:free", "provider": "openrouter.ai",
                          "data_collection": "allow"}
    assert settings_view({**row, "company_id": None}, s)["planning_agent"]["scope"] == "default"
