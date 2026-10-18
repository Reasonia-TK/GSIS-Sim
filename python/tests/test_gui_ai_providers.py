"""形式ごとの層(Anthropic の Messages API・OpenAI の Chat Completions API)を、HTTP の層の模擬で確かめる。

OpenAI の Responses API は ``test_gui_ai_responses.py``。

実際の API は呼ばない。公式 SDK に ``httpx2.MockTransport`` を持つクライアントを渡し、決まった応答(JSON と SSE)を
返す(:mod:`gsis_tools.gui.ai.demo`)。送ったリクエストの形(パス・ヘッダー・本文)が公式資料の形と合うこと、
応答の読み方(複数の呼び出し、考え、トークン数)、エラーの種類を確かめる。
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("openai")
httpx2 = pytest.importorskip("httpx2")

from gsis_tools.gui.ai.demo import ScriptedApi, ScriptTurn  # noqa: E402
from gsis_tools.gui.ai.providers import make_provider  # noqa: E402
from gsis_tools.gui.ai.secrets import KeyResolver, KeyStore, ResolvedKey, Secret  # noqa: E402
from gsis_tools.gui.ai.settings import FORMAT_ANTHROPIC, FORMAT_OPENAI, AiSettings  # noqa: E402
from gsis_tools.gui.ai.types import (  # noqa: E402
    ERR_AUTH,
    ERR_CONNECTION,
    ERR_NOT_FOUND,
    ERR_OVERLOADED,
    ERR_RATE_LIMIT,
    ERR_SERVER,
    STOP_END,
    STOP_MAX_TOKENS,
    STOP_REFUSAL,
    STOP_TOOL,
    ProviderError,
    ToolCall,
    ToolDef,
    ToolOutcome,
)

KEY = "sk-test-DUMMY-key-abcdefghijklmnop"
TOOLS = [
    ToolDef("add_box", "直方体を作る", {"type": "object", "properties": {"corner": {"type": "array"}},
                                         "required": ["corner"], "additionalProperties": False}),
    ToolDef("measure", "計測", {"type": "object", "properties": {}}, read_only=True),
]


def settings(fmt: str, **kw) -> AiSettings:
    s = AiSettings(format=fmt)
    s.max_retries = 0
    if fmt == FORMAT_OPENAI:
        s.current.model = "demo-model"
        s.current.api = "chat"  # この試験は Chat Completions(自動なら公式の接続先は Responses)
    for k, v in kw.items():
        if hasattr(s.current, k):
            setattr(s.current, k, v)
        else:
            setattr(s, k, v)
    return s


def key() -> ResolvedKey:
    return ResolvedKey(Secret(KEY), "session", "試験")


def run(coro):
    return asyncio.run(coro)


async def _send(provider, messages=None, events=None):
    try:
        return await provider.send(system="システムの指示", tools=TOOLS,
                                   messages=messages or [{"role": "user", "content": [{"type": "text", "text": "箱"}]}],
                                   on_event=(lambda k, t: events.append((k, t))) if events is not None else None)
    finally:
        await provider.aclose()


def make(fmt, api: ScriptedApi, s: AiSettings | None = None, k: ResolvedKey | None = None):
    s = s or settings(fmt)
    return make_provider(s, k or key(), http_client=api.client_factory())


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stream", [True, False])
def test_anthropic_request_and_parse(stream):
    turn = ScriptTurn(text="箱を作ります。", thinking="途中の報告", calls=[("add_box", {"corner": [0, 0, 0]}),
                                                                          ("measure", {})],
                      usage=(100, 50, 2000, 300))
    api = ScriptedApi("anthropic", [turn])
    s = settings(FORMAT_ANTHROPIC, stream=stream)
    events: list = []
    t = run(_send(make(FORMAT_ANTHROPIC, api, s), events=events))
    req = api.requests[0]
    # 公式の接続先・既定の Opus 5.5: 途中の報告(beta)と断られたときの切り替え(beta)
    assert req["path"] == "/v1/messages" and req["query"] == {"beta": "true"}
    h = req["headers"]
    assert h["x-api-key"] == KEY and h["anthropic-version"] == "2023-06-01" and "authorization" not in h
    assert set(h["anthropic-beta"].split(",")) == {"thinking-display-updates-2026-08-18", "server-side-fallback-2026-07-01"}
    b = req["json"]
    assert b["model"] == "claude-opus-5-5" and b["max_tokens"] == 32000 and b.get("stream", False) is stream
    assert b["system"] == [{"type": "text", "text": "システムの指示", "cache_control": {"type": "ephemeral"}}]
    assert b["cache_control"] == {"type": "ephemeral"}  # 会話の自動キャッシュ
    assert [x["name"] for x in b["tools"]] == ["add_box", "measure"]
    assert b["tools"][0] == {"name": "add_box", "description": "直方体を作る", "input_schema": TOOLS[0].input_schema}
    assert b["tools"][-1]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in b["tools"][0]
    assert b["thinking"] == {"type": "adaptive", "display": "updates"} and b["fallbacks"] == "default"
    # 応答
    assert t.stop == STOP_TOOL and t.text == "箱を作ります。" and t.thinking == ["途中の報告"]
    assert [(c.name, c.arguments) for c in t.tool_calls] == [("add_box", {"corner": [0, 0, 0]}), ("measure", {})]
    assert t.tool_calls[0].id.startswith("toolu_") and t.model == "claude-opus-5-5"
    assert (t.usage.input_tokens, t.usage.output_tokens, t.usage.cache_read_tokens, t.usage.cache_write_tokens) == \
        (2400, 50, 2000, 300)
    # 履歴に足す形: 考えのブロックは署名ごと、そのまま
    native = t.native[0]
    assert native["role"] == "assistant"
    kinds = [x["type"] for x in native["content"]]
    assert kinds == ["thinking", "text", "tool_use", "tool_use"]
    assert native["content"][0]["signature"].startswith("sig") and native["content"][0]["thinking"] == "途中の報告"
    if stream:
        assert ("text", "箱を作") in events or any(k == "text" for k, _ in events)
        assert any(k == "thinking" for k, _ in events) and ("tool", "add_box") in events


def test_anthropic_custom_url_no_betas_and_options():
    api = ScriptedApi("anthropic", [ScriptTurn(text="OK")])
    s = settings(FORMAT_ANTHROPIC, base_url="https://proxy.example.com", prompt_cache=False)
    s.effort = "high"
    s.current.extra_body = '{"metadata": {"user_id": "u1"}}'
    t = run(_send(make(FORMAT_ANTHROPIC, api, s)))
    req = api.requests[0]
    assert req["url"].startswith("https://proxy.example.com/v1/messages") and req["query"] == {}
    assert "anthropic-beta" not in req["headers"]
    b = req["json"]
    assert "fallbacks" not in b and "thinking" not in b and "cache_control" not in b
    assert b["system"] == [{"type": "text", "text": "システムの指示"}] and "cache_control" not in b["tools"][-1]
    assert b["output_config"] == {"effort": "high"} and b["metadata"] == {"user_id": "u1"}
    assert t.stop == STOP_END and t.text == "OK" and t.tool_calls == []


def test_anthropic_tool_results_format():
    p = make(FORMAT_ANTHROPIC, ScriptedApi("anthropic", []))
    calls = [ToolCall("toolu_1", "add_box", {}), ToolCall("toolu_2", "render", {})]
    outs = [ToolOutcome(calls[0], '{"error": "x"}', is_error=True),
            ToolOutcome(calls[1], '{"path": "a.png"}', images=[b"\x89PNG\r\n\x1a\nfake"])]
    msgs = p.tool_result_messages(outs, send_images=True)
    assert len(msgs) == 1 and msgs[0]["role"] == "user"
    blocks = msgs[0]["content"]
    assert [b["tool_use_id"] for b in blocks] == ["toolu_1", "toolu_2"] and all(b["type"] == "tool_result" for b in blocks)
    assert blocks[0]["is_error"] is True and "is_error" not in blocks[1]
    assert blocks[1]["content"][1]["type"] == "image" and blocks[1]["content"][1]["source"]["media_type"] == "image/png"
    no_img = p.tool_result_messages(outs, send_images=False)
    assert all(len(b["content"]) == 1 for b in no_img[0]["content"])
    run(p.aclose())


def test_anthropic_refusal_and_max_tokens():
    api = ScriptedApi("anthropic", [ScriptTurn(text="途中", stop="refusal"),
                                    ScriptTurn(calls=[("add_box", {"corner": [0, 0]})], stop="max_tokens")])
    s = settings(FORMAT_ANTHROPIC)
    t1 = run(_send(make(FORMAT_ANTHROPIC, api, s)))
    t2 = run(_send(make(FORMAT_ANTHROPIC, api, s)))
    assert t1.stop == STOP_REFUSAL
    assert t2.stop == STOP_MAX_TOKENS and len(t2.tool_calls) == 1


def test_anthropic_fallback_echo_rule():
    """途中で別のモデルに切り替わった応答: 最後の fallback ブロックより前は text だけを送り返す。"""
    p = make(FORMAT_ANTHROPIC, ScriptedApi("anthropic", []))

    class Msg:
        stop_reason = "tool_use"
        model = "claude-opus-4-8"
        usage = None
        content = [{"type": "thinking", "thinking": "a", "signature": "s"}, {"type": "text", "text": "前半"},
                   {"type": "tool_use", "id": "t0", "name": "add_box", "input": {}},
                   {"type": "fallback", "from": {"model": "claude-fable-5-1"}, "to": {"model": "claude-opus-4-8"}},
                   {"type": "text", "text": "後半"}, {"type": "tool_use", "id": "t1", "name": "measure", "input": {}}]

    t = p.to_turn(Msg())
    assert [b["type"] for b in t.native[0]["content"]] == ["text", "fallback", "text", "tool_use"]
    assert [c.id for c in t.tool_calls] == ["t1"] and t.text == "前半後半"
    run(p.aclose())


@pytest.mark.parametrize("status,kind", [(401, ERR_AUTH), (404, ERR_NOT_FOUND), (500, ERR_SERVER),
                                         (529, ERR_OVERLOADED)])
def test_anthropic_errors(status, kind):
    body = {"type": "error", "error": {"type": {401: "authentication_error", 404: "not_found_error",
                                                500: "api_error", 529: "overloaded_error"}[status],
                                       "message": f"msg {status} {KEY}"}}
    api = ScriptedApi("anthropic", [ScriptTurn(status=status, error_body=body)] * 3)
    with pytest.raises(ProviderError) as ei:
        run(_send(make(FORMAT_ANTHROPIC, api)))
    e = ei.value
    assert e.kind == kind and e.status == status and len(api.requests) == 1  # max_retries=0
    assert KEY not in e.message and KEY not in e.describe()


def test_anthropic_rate_limit_retries():
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    api = ScriptedApi("anthropic", [ScriptTurn(status=429, error_body=body, headers={"retry-after-ms": "1",
                                                                                     "retry-after": "7"})] * 3)
    s = settings(FORMAT_ANTHROPIC)
    s.max_retries = 1
    with pytest.raises(ProviderError) as ei:
        run(_send(make(FORMAT_ANTHROPIC, api, s)))
    assert ei.value.kind == ERR_RATE_LIMIT and ei.value.retryable and len(api.requests) == 2
    assert ei.value.retry_after == pytest.approx(0.001)


def test_anthropic_connection_error_and_stream_error():
    api = ScriptedApi("anthropic", [ScriptTurn(exception=httpx2.ConnectError("切断"))])
    with pytest.raises(ProviderError) as ei:
        run(_send(make(FORMAT_ANTHROPIC, api)))
    assert ei.value.kind == ERR_CONNECTION and ei.value.retryable
    api = ScriptedApi("anthropic", [ScriptTurn(text="途中まで", stream_error={"type": "overloaded_error",
                                                                              "message": "Overloaded"})])
    with pytest.raises(ProviderError) as ei:
        run(_send(make(FORMAT_ANTHROPIC, api)))
    assert ei.value.kind == ERR_OVERLOADED


def test_anthropic_list_models_and_explicit_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-should-not-be-used-0000")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-should-not-be-used-0000")
    api = ScriptedApi("anthropic", [], models=["claude-fable-5-1", "claude-opus-5-5"])
    p = make(FORMAT_ANTHROPIC, api)
    assert run(p.list_models()) == ["claude-fable-5-1", "claude-opus-5-5"]
    run(p.aclose())
    h = api.requests[0]["headers"]
    assert api.requests[0]["path"] == "/v1/models" and h["x-api-key"] == KEY and "authorization" not in h


def test_anthropic_requires_key():
    with pytest.raises(ProviderError) as ei:
        make_provider(settings(FORMAT_ANTHROPIC), ResolvedKey(None, "none", "なし"))
    assert ei.value.kind == "config"


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stream", [True, False])
def test_openai_request_and_parse(stream):
    turn = ScriptTurn(text="箱を作ります。", thinking="考え", calls=[("add_box", {"corner": [0, 0, 0]}), ("measure", {})],
                      usage=(100, 40, 900, 0))
    api = ScriptedApi("openai", [turn])
    s = settings(FORMAT_OPENAI, stream=stream)
    events: list = []
    t = run(_send(make(FORMAT_OPENAI, api, s), messages=[{"role": "user", "content": "箱"}], events=events))
    req = api.requests[0]
    assert req["path"] == "/v1/chat/completions" and req["url"].startswith("https://api.openai.com/v1/")
    assert req["headers"]["authorization"] == f"Bearer {KEY}"
    b = req["json"]
    assert b["model"] == "demo-model" and b["messages"][0] == {"role": "system", "content": "システムの指示"}
    assert b["messages"][1] == {"role": "user", "content": "箱"}
    assert b["tools"][0] == {"type": "function", "function": {"name": "add_box", "description": "直方体を作る",
                                                              "parameters": TOOLS[0].input_schema}}
    assert b["max_completion_tokens"] == 16384 and "max_tokens" not in b  # 公式の接続先
    if stream:
        assert b["stream"] is True and b["stream_options"] == {"include_usage": True}
    else:
        assert "stream" not in b and "stream_options" not in b
    assert t.stop == STOP_TOOL and t.text == "箱を作ります。" and t.thinking == ["考え"]
    assert [(c.name, c.arguments) for c in t.tool_calls] == [("add_box", {"corner": [0, 0, 0]}), ("measure", {})]
    assert (t.usage.input_tokens, t.usage.output_tokens, t.usage.cache_read_tokens) == (1000, 40, 900)
    m = t.native[0]
    assert m["role"] == "assistant" and m["content"] == "箱を作ります。"
    assert [c["function"]["name"] for c in m["tool_calls"]] == ["add_box", "measure"]
    assert json.loads(m["tool_calls"][0]["function"]["arguments"]) == {"corner": [0, 0, 0]}
    assert "reasoning_content" not in m  # 考えの文章は送り返さない
    if stream:
        assert any(k == "text" for k, _ in events) and ("tool", "add_box") in events


def test_openai_tool_results_and_images():
    p = make(FORMAT_OPENAI, ScriptedApi("openai", []))
    calls = [ToolCall("call_1", "add_box", {}), ToolCall("call_2", "render", {})]
    outs = [ToolOutcome(calls[0], '{"error": "x"}', is_error=True),
            ToolOutcome(calls[1], '{"path": "a.png"}', images=[b"\x89PNG\r\n\x1a\nfake"])]
    msgs = p.tool_result_messages(outs, send_images=True)
    assert [m["role"] for m in msgs] == ["tool", "tool", "user"]
    assert msgs[0] == {"role": "tool", "tool_call_id": "call_1", "content": '{"error": "x"}'}
    assert msgs[1]["tool_call_id"] == "call_2" and isinstance(msgs[1]["content"], str)
    parts = msgs[2]["content"]
    assert parts[0]["type"] == "text" and parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert [m["role"] for m in p.tool_result_messages(outs, send_images=False)] == ["tool", "tool"]
    run(p.aclose())


def test_openai_custom_server_no_key_and_headers(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-should-not-be-sent-000000000")
    monkeypatch.setenv("OPENAI_ORG_ID", "org-from-env")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-from-env")
    api = ScriptedApi("openai", [ScriptTurn(text="OK")])
    s = settings(FORMAT_OPENAI, base_url="http://localhost:11434/v1", no_key=True)
    r = KeyResolver(KeyStore(object()), environ={"OPENAI_API_KEY": "sk-env-should-not-be-sent-000000000"})
    k = r.resolve(s)
    assert k.source == "no_key"
    t = run(_send(make(FORMAT_OPENAI, api, s, k), messages=[{"role": "user", "content": "x"}]))
    h = api.requests[0]["headers"]
    assert api.requests[0]["url"].startswith("http://localhost:11434/v1/chat/completions")
    assert "authorization" not in h and "openai-organization" not in h and "openai-project" not in h
    assert api.requests[0]["json"]["max_tokens"] == 16384  # 互換サーバーの既定
    assert t.text == "OK"
    # 公式の接続先では、環境変数の組織の ID を SDK がそのまま付ける(公式の動き)
    api2 = ScriptedApi("openai", [ScriptTurn(text="OK")])
    run(_send(make(FORMAT_OPENAI, api2), messages=[{"role": "user", "content": "x"}]))
    assert api2.requests[0]["headers"]["openai-organization"] == "org-from-env"
    assert api2.requests[0]["headers"]["authorization"] == f"Bearer {KEY}"


def test_openai_auto_switch_token_param():
    err = {"error": {"message": "Unsupported parameter: 'max_completion_tokens' is not supported with this model. "
                                "Use 'max_tokens' instead.", "type": "invalid_request_error", "code": None}}
    api = ScriptedApi("openai", [ScriptTurn(status=400, error_body=err), ScriptTurn(text="OK")])
    p = make(FORMAT_OPENAI, api)
    t = run(_send(p, messages=[{"role": "user", "content": "x"}]))
    assert t.text == "OK" and len(api.requests) == 2
    assert "max_completion_tokens" in api.requests[0]["json"] and "max_tokens" in api.requests[1]["json"]
    assert p.token_param == "max_tokens" and any("max_tokens" in n for n in t.notes)
    # 2 回目の 400 はそのまま返す
    api = ScriptedApi("openai", [ScriptTurn(status=400, error_body=err)] * 3)
    with pytest.raises(ProviderError):
        run(_send(make(FORMAT_OPENAI, api), messages=[{"role": "user", "content": "x"}]))
    assert len(api.requests) == 2


def test_openai_bad_arguments_and_missing_id():
    api = ScriptedApi("openai", [ScriptTurn(calls=[("add_box", {}), ("measure", {})],
                                            raw_arguments={0: '{"corner": [0, 0', 1: ""})])
    t = run(_send(make(FORMAT_OPENAI, api), messages=[{"role": "user", "content": "x"}]))
    assert t.tool_calls[0].arguments is None and "JSON" in t.tool_calls[0].error
    assert t.tool_calls[1].arguments == {}  # 空の引数は {} とみなす
    assert t.native[0]["tool_calls"][0]["function"]["arguments"] == '{"corner": [0, 0'  # そのまま送り返す


@pytest.mark.parametrize("status,kind", [(401, ERR_AUTH), (404, ERR_NOT_FOUND), (503, ERR_SERVER)])
def test_openai_errors(status, kind):
    api = ScriptedApi("openai", [ScriptTurn(status=status, error_body={"error": {"message": f"m {KEY}",
                                                                              "type": "x"}})] * 2)
    with pytest.raises(ProviderError) as ei:
        run(_send(make(FORMAT_OPENAI, api), messages=[{"role": "user", "content": "x"}]))
    assert ei.value.kind == kind and KEY not in ei.value.describe()


def test_openai_rate_limit_and_connection():
    api = ScriptedApi("openai", [ScriptTurn(status=429, error_body={"error": {"message": "rate"}},
                                            headers={"retry-after-ms": "1"})] * 3)
    s = settings(FORMAT_OPENAI)
    s.max_retries = 1
    with pytest.raises(ProviderError) as ei:
        run(_send(make(FORMAT_OPENAI, api, s), messages=[{"role": "user", "content": "x"}]))
    assert ei.value.kind == ERR_RATE_LIMIT and len(api.requests) == 2
    api = ScriptedApi("openai", [ScriptTurn(exception=httpx2.ConnectError("refused"))])
    with pytest.raises(ProviderError) as ei:
        run(_send(make(FORMAT_OPENAI, api), messages=[{"role": "user", "content": "x"}]))
    assert ei.value.kind == ERR_CONNECTION


def test_openai_list_models_and_finish_reasons():
    api = ScriptedApi("openai", [ScriptTurn(text="a", stop="length"), ScriptTurn(text="b", stop="content_filter")],
                      models=["m2", "m1"])
    p = make(FORMAT_OPENAI, api)
    assert run(p.list_models()) == ["m1", "m2"]
    assert api.requests[0]["path"] == "/v1/models"
    assert run(_send(make(FORMAT_OPENAI, api), messages=[{"role": "user", "content": "x"}])).stop == STOP_MAX_TOKENS
    assert run(_send(make(FORMAT_OPENAI, api), messages=[{"role": "user", "content": "x"}])).stop == STOP_REFUSAL
