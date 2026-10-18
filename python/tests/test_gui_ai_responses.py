"""OpenAI 形式の Responses API の層を、HTTP の層の模擬で確かめる。

実際の API は呼ばない。公式 SDK に ``httpx2.MockTransport`` を持つクライアントを渡し、決まった応答(JSON と SSE)を
返す(:mod:`gsis_tools.gui.ai.demo`)。送ったリクエストの形が公式資料(Function calling・Reasoning・Conversation
state のガイド、SDK の型)と合うこと: ``instructions``・``input``、関数の定義(``strict: false``)、``store: false`` と
``include``、``max_output_tokens``、関数の結果(``function_call_output`` と ``call_id``、画像は ``input_image``)、
推論の項目(``encrypted_content``)をそのまま送り返すこと、ストリーミングのイベント、トークン数、エラー。
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("openai")
httpx2 = pytest.importorskip("httpx2")

from gsis_tools.gui.ai.demo import ScriptedApi, ScriptTurn  # noqa: E402
from gsis_tools.gui.ai.providers import make_provider  # noqa: E402
from gsis_tools.gui.ai.providers.openai_provider import OpenAIProvider  # noqa: E402
from gsis_tools.gui.ai.providers.openai_responses_provider import OpenAIResponsesProvider  # noqa: E402
from gsis_tools.gui.ai.secrets import KeyResolver, KeyStore, ResolvedKey, Secret  # noqa: E402
from gsis_tools.gui.ai.settings import FORMAT_OPENAI, AiSettings  # noqa: E402
from gsis_tools.gui.ai.types import (  # noqa: E402
    ERR_AUTH,
    ERR_CONNECTION,
    ERR_NOT_FOUND,
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

KEY = "sk-test-DUMMY-responses-abcdefghijklmnop"
TOOLS = [
    ToolDef("add_box", "直方体を作る", {"type": "object", "properties": {"corner": {"type": "array"}},
                                         "required": ["corner"], "additionalProperties": False}),
    ToolDef("measure", "計測", {"type": "object", "properties": {}}, read_only=True),
]
USER = [{"role": "user", "content": [{"type": "input_text", "text": "箱"}]}]


def settings(**kw) -> AiSettings:
    s = AiSettings(format=FORMAT_OPENAI)
    s.max_retries = 0
    s.current.model = "demo-model"
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
        return await provider.send(system="システムの指示", tools=TOOLS, messages=messages or list(USER),
                                   on_event=(lambda k, t: events.append((k, t))) if events is not None else None)
    finally:
        await provider.aclose()


def make(api: ScriptedApi, s: AiSettings | None = None, k: ResolvedKey | None = None):
    return make_provider(s or settings(), k or key(), http_client=api.client_factory())


def test_api_kind_dispatch():
    """自動: 公式の接続先は Responses、ほかは Chat Completions。明示すればそれを使う。"""
    api = ScriptedApi("openai", [])
    cases = [(settings(), OpenAIResponsesProvider), (settings(base_url="http://localhost:11434/v1"), OpenAIProvider),
             (settings(base_url="http://localhost:11434/v1", api="responses"), OpenAIResponsesProvider),
             (settings(api="chat"), OpenAIProvider)]
    for s, cls in cases:
        p = make(api, s)
        assert type(p) is cls and p.kind == s.provider_kind()
        run(p.aclose())


@pytest.mark.parametrize("stream", [True, False])
def test_request_and_parse(stream):
    turn = ScriptTurn(text="箱を作ります。", thinking="考えの要約", calls=[("add_box", {"corner": [0, 0, 0]}),
                                                                           ("measure", {})],
                      usage=(100, 40, 900, 0))
    api = ScriptedApi("openai", [turn])
    events: list = []
    t = run(_send(make(api, settings(stream=stream)), events=events))
    req = api.requests[0]
    assert req["path"] == "/v1/responses" and req["url"].startswith("https://api.openai.com/v1/")
    assert req["headers"]["authorization"] == f"Bearer {KEY}"
    b = req["json"]
    # システムの指示は instructions、会話は input。Chat Completions の欄は送らない
    assert b["model"] == "demo-model" and b["instructions"] == "システムの指示" and b["input"] == USER
    assert "messages" not in b and "max_tokens" not in b and "max_completion_tokens" not in b
    # 関数の定義は function の中に入れない。strict は false を明示する(MCP のスキーマは strict の条件を満たさないことがある)
    assert b["tools"][0] == {"type": "function", "name": "add_box", "description": "直方体を作る",
                             "parameters": TOOLS[0].input_schema, "strict": False}
    assert all(x["strict"] is False for x in b["tools"])
    # 会話を OpenAI 側に保存しない。推論の暗号化された内容を受け取って送り返す。previous_response_id は使わない
    assert b["store"] is False and b["include"] == ["reasoning.encrypted_content"]
    assert "previous_response_id" not in b and "conversation" not in b
    assert b["max_output_tokens"] == 16384
    if stream:
        assert b["stream"] is True and "stream_options" not in b
    else:
        assert "stream" not in b
    # 応答: 文章・推論の要約・2 つの呼び出し(id は call_id)・トークン数
    assert t.stop == STOP_TOOL and t.text == "箱を作ります。" and t.thinking == ["考えの要約"]
    assert [(c.name, c.arguments) for c in t.tool_calls] == [("add_box", {"corner": [0, 0, 0]}), ("measure", {})]
    assert all(c.id.startswith("call_") for c in t.tool_calls)
    assert (t.usage.input_tokens, t.usage.output_tokens, t.usage.cache_read_tokens) == (1000, 40, 900)
    # 履歴に足す形: 応答の項目をそのまま(推論の項目は暗号化された内容ごと)
    assert [x["type"] for x in t.native] == ["reasoning", "message", "function_call", "function_call"]
    assert t.native[0]["encrypted_content"].startswith("gAAAA") and t.native[0]["id"].startswith("rs_")
    fc = t.native[2]
    assert fc["call_id"] == t.tool_calls[0].id and fc["id"].startswith("fc_") and fc["name"] == "add_box"
    assert json.loads(fc["arguments"]) == {"corner": [0, 0, 0]}
    if stream:
        assert any(k == "text" for k, _ in events) and any(k == "thinking" for k, _ in events)
        assert ("tool", "add_box") in events and ("tool", "measure") in events


def test_tool_results_and_images():
    p = make(ScriptedApi("openai", []))
    calls = [ToolCall("call_1", "add_box", {}), ToolCall("call_2", "render", {})]
    outs = [ToolOutcome(calls[0], '{"error": "x"}', is_error=True),
            ToolOutcome(calls[1], '{"path": "a.png"}', images=[b"\x89PNG\r\n\x1a\nfake"])]
    msgs = p.tool_result_messages(outs, send_images=True)
    assert msgs[0] == {"type": "function_call_output", "call_id": "call_1", "output": '{"error": "x"}'}
    out = msgs[1]["output"]
    assert msgs[1]["call_id"] == "call_2" and [x["type"] for x in out] == ["input_text", "input_image"]
    assert out[0]["text"] == '{"path": "a.png"}' and out[1]["image_url"].startswith("data:image/png;base64,")
    assert out[1]["detail"] == "auto"
    plain = p.tool_result_messages(outs, send_images=False)
    assert [m["output"] for m in plain] == ['{"error": "x"}', '{"path": "a.png"}']
    assert p.user_message("箱") == USER
    run(p.aclose())


def test_reasoning_items_are_sent_back_unchanged():
    """推論のモデル: 関数の呼び出しと一緒に返った推論の項目を、次のリクエストでそのまま送り返す。"""
    api = ScriptedApi("openai", [ScriptTurn(thinking="考え 1", calls=[("add_box", {"corner": [1, 2, 3]})]),
                                 ScriptTurn(thinking="考え 2", text="できました")])
    s = settings()
    p = make(api, s)

    async def go():
        msgs = list(USER)
        t1 = await p.send(system="指示", tools=TOOLS, messages=msgs)
        msgs += t1.native
        msgs += p.tool_result_messages([ToolOutcome(t1.tool_calls[0], '{"volume": 1}')], send_images=False)
        t2 = await p.send(system="指示", tools=TOOLS, messages=msgs)
        await p.aclose()
        return t1, t2, msgs

    t1, t2, msgs = run(go())
    sent = api.requests[1]["json"]["input"]
    assert sent == msgs
    assert [x.get("type") for x in sent] == [None, "reasoning", "function_call", "function_call_output"]
    assert sent[1] == t1.native[0] and sent[1]["encrypted_content"] == t1.native[0]["encrypted_content"]
    assert sent[3]["call_id"] == sent[2]["call_id"] == t1.tool_calls[0].id
    assert t2.stop == STOP_END and t2.text == "できました" and t2.thinking == ["考え 2"]


def test_store_true_and_custom_server_no_key(monkeypatch):
    # 保存を選んだとき: store: true。履歴はそれでも手元から送る(previous_response_id は使わない)
    api = ScriptedApi("openai", [ScriptTurn(text="OK")])
    run(_send(make(api, settings(store=True))))
    b = api.requests[0]["json"]
    assert b["store"] is True and b["include"] == ["reasoning.encrypted_content"] and "previous_response_id" not in b
    # 互換サーバー(キーなし): Authorization・組織の ID を送らない。include は送らない(公式の拡張のため)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-should-not-be-sent-000000000")
    monkeypatch.setenv("OPENAI_ORG_ID", "org-from-env")
    s = settings(base_url="http://localhost:8000/v1", api="responses", no_key=True)
    k = KeyResolver(KeyStore(object()), environ={"OPENAI_API_KEY": "sk-env-should-not-be-sent-000000000"}).resolve(s)
    assert k.source == "no_key"
    api = ScriptedApi("openai", [ScriptTurn(text="OK")])
    t = run(_send(make(api, s, k)))
    req = api.requests[0]
    assert req["url"].startswith("http://localhost:8000/v1/responses")
    assert "authorization" not in req["headers"] and "openai-organization" not in req["headers"]
    assert req["json"]["store"] is False and "include" not in req["json"] and t.text == "OK"


def test_auto_fix_unknown_include_and_store():
    """接続先が include・store を知らないときは、1 回ずつ外して送り直す。公式の接続先では store を外さない。"""
    e_inc = {"error": {"message": "Unknown parameter: 'include'.", "type": "invalid_request_error", "param": "include"}}
    e_store = {"error": {"message": "Unrecognized request argument supplied: store", "type": "invalid_request_error"}}
    # 公式の接続先: include は外して送り直す。store は外さない(省くと応答が保存される)
    api = ScriptedApi("openai", [ScriptTurn(status=400, error_body=e_inc), ScriptTurn(status=400, error_body=e_store)])
    with pytest.raises(ProviderError) as ei:
        run(_send(make(api)))
    assert ei.value.status == 400 and len(api.requests) == 2
    assert "include" in api.requests[0]["json"] and "include" not in api.requests[1]["json"]
    assert api.requests[1]["json"]["store"] is False
    # 互換サーバー: store も外して送り直す
    s = settings(base_url="http://localhost:8000/v1", api="responses")
    api = ScriptedApi("openai", [ScriptTurn(status=400, error_body=e_store), ScriptTurn(text="OK")])
    t = run(_send(make(api, s)))
    assert t.text == "OK" and len(api.requests) == 2
    assert api.requests[0]["json"]["store"] is False and "store" not in api.requests[1]["json"]
    assert len(t.notes) == 1 and "store" in t.notes[0]
    # 関係のない 400 はそのまま返す
    api = ScriptedApi("openai", [ScriptTurn(status=400, error_body={"error": {"message": "bad tools"}})] * 2)
    with pytest.raises(ProviderError) as ei:
        run(_send(make(api)))
    assert ei.value.status == 400 and len(api.requests) == 1


def test_incomplete_refusal_and_failed():
    api = ScriptedApi("openai", [ScriptTurn(calls=[("add_box", {"corner": [0]})], stop="max_output_tokens"),
                                 ScriptTurn(thinking="考えの途中", stop="max_output_tokens"),
                                 ScriptTurn(text="途中", stop="content_filter"),
                                 ScriptTurn(text="お手伝いできません", stop="refusal"),
                                 ScriptTurn(text="x", stop="failed"), ScriptTurn(text="x", stop="failed")])
    t = run(_send(make(api)))
    assert t.stop == STOP_MAX_TOKENS and len(t.tool_calls) == 1  # 上限で切れた(呼び出しは実行しない)
    t = run(_send(make(api)))  # 推論の途中で切れた: 続く項目のない推論の項目は送り返さない(要約は表示する)
    assert t.stop == STOP_MAX_TOKENS and t.native == [] and t.thinking == ["考えの途中"]
    t = run(_send(make(api)))
    assert t.stop == STOP_REFUSAL and t.stop_detail == "content_filter"
    t = run(_send(make(api)))
    assert t.stop == STOP_REFUSAL and t.text == "お手伝いできません" and t.tool_calls == []
    with pytest.raises(ProviderError) as ei:  # response.failed(ストリーミング)
        run(_send(make(api)))
    assert ei.value.kind == ERR_SERVER and "failed" in ei.value.message
    with pytest.raises(ProviderError) as ei:  # status: failed(ストリーミングでない)
        run(_send(make(api, settings(stream=False))))
    assert ei.value.kind == ERR_SERVER


@pytest.mark.parametrize("status,kind", [(401, ERR_AUTH), (404, ERR_NOT_FOUND), (500, ERR_SERVER)])
def test_errors(status, kind):
    api = ScriptedApi("openai", [ScriptTurn(status=status, error_body={"error": {"message": f"m {KEY}",
                                                                              "type": "x"}})] * 2)
    with pytest.raises(ProviderError) as ei:
        run(_send(make(api)))
    assert ei.value.kind == kind and KEY not in ei.value.describe() and api.requests[0]["path"] == "/v1/responses"


def test_rate_limit_connection_and_stream_error():
    api = ScriptedApi("openai", [ScriptTurn(status=429, error_body={"error": {"message": "rate"}},
                                            headers={"retry-after-ms": "1"})] * 3)
    s = settings()
    s.max_retries = 1
    with pytest.raises(ProviderError) as ei:
        run(_send(make(api, s)))
    assert ei.value.kind == ERR_RATE_LIMIT and len(api.requests) == 2
    api = ScriptedApi("openai", [ScriptTurn(exception=httpx2.ConnectError("refused"))])
    with pytest.raises(ProviderError) as ei:
        run(_send(make(api)))
    assert ei.value.kind == ERR_CONNECTION
    # SSE の途中の error イベント
    api = ScriptedApi("openai", [ScriptTurn(text="途中まで", stream_error={"code": "server_error",
                                                                          "message": f"boom {KEY}"})])
    with pytest.raises(ProviderError) as ei:
        run(_send(make(api)))
    assert ei.value.kind == ERR_SERVER and KEY not in ei.value.describe()


def test_bad_arguments_and_list_models():
    api = ScriptedApi("openai", [ScriptTurn(calls=[("add_box", {}), ("measure", {})],
                                            raw_arguments={0: '{"corner": [0, 0', 1: ""})], models=["m2", "m1"])
    t = run(_send(make(api)))
    assert t.tool_calls[0].arguments is None and "JSON" in t.tool_calls[0].error
    assert t.tool_calls[1].arguments == {}
    assert t.native[0]["arguments"] == '{"corner": [0, 0'  # そのまま送り返す
    p = make(api)
    assert run(p.list_models()) == ["m1", "m2"] and api.requests[-1]["path"] == "/v1/models"
    run(p.aclose())
