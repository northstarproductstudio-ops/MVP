"""LLM_DATA_COLLECTION controls OpenRouter's provider.data_collection; anything but 'allow' is deny."""

import pytest

from core.llm import LLMClient, data_collection_policy


@pytest.mark.parametrize(
    ("env", "expected"),
    [(None, "deny"), ("deny", "deny"), ("allow", "allow"), (" Allow ", "allow"), ("yes", "deny"), ("", "deny")],
)
def test_policy_from_env(monkeypatch: pytest.MonkeyPatch, env: str | None, expected: str) -> None:
    if env is None:
        monkeypatch.delenv("LLM_DATA_COLLECTION", raising=False)
    else:
        monkeypatch.setenv("LLM_DATA_COLLECTION", env)
    assert data_collection_policy() == expected
    assert LLMClient(api_key="k", model="m").data_collection == expected


def test_request_body_carries_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_DATA_COLLECTION", "allow")
    sent: dict = {}

    class FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    class FakeHttp:
        def post(self, url: str, json: dict) -> FakeResp:  # noqa: A002
            sent.update(json)
            return FakeResp()

    client = LLMClient(api_key="k", model="m", _http=FakeHttp())  # type: ignore[arg-type]
    client.complete(role="ask", messages=[{"role": "user", "content": "hi"}])
    assert sent["provider"] == {"data_collection": "allow"}
