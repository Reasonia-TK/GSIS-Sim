"""本物の API で 1 回だけ通す試験(既定は飛ばす。料金がかかり、指示とツールの結果が外部の API に送られる)。

環境変数で明示したときだけ動く:

    # Anthropic 形式(キーは環境変数 ANTHROPIC_API_KEY。モデルは既定 claude-opus-5-5、変えるなら GSIS_AI_LIVE_MODEL)
    $env:GSIS_AI_LIVE_TEST = "anthropic"
    uv run --project python --extra gui pytest python/tests/test_gui_ai_live.py -s

    # OpenAI 形式の Responses API(キーは OPENAI_API_KEY、モデル GSIS_AI_LIVE_MODEL は必須。store: false で送る)
    $env:GSIS_AI_LIVE_TEST = "openai-responses"; $env:GSIS_AI_LIVE_MODEL = "<モデル名>"
    uv run --project python --extra gui pytest python/tests/test_gui_ai_live.py -s

    # OpenAI 形式の Chat Completions API(互換サーバーは GSIS_AI_LIVE_BASE_URL、キーの要らないローカルのサーバーは
    # GSIS_AI_LIVE_NO_KEY=1)
    $env:GSIS_AI_LIVE_TEST = "openai-chat"; $env:GSIS_AI_LIVE_MODEL = "<モデル名>"
    uv run --project python --extra gui pytest python/tests/test_gui_ai_live.py -s

``GSIS_AI_LIVE_TEST = "openai"`` は API の種類を自動で選ぶ(公式の接続先は Responses、ほかは Chat Completions)。

キーは環境変数からだけ読む(OS の資格情報マネージャーは使わない)。作業フォルダは一時フォルダ。
"""

from __future__ import annotations

import os

import pytest

LIVE = os.environ.get("GSIS_AI_LIVE_TEST", "").strip().lower()
LIVE_KINDS = {"anthropic": ("anthropic", "auto"), "openai": ("openai", "auto"), "openai-chat": ("openai", "chat"),
              "openai-responses": ("openai", "responses")}
pytestmark = pytest.mark.skipif(LIVE not in LIVE_KINDS,
                                reason="本物の API の試験は環境変数 GSIS_AI_LIVE_TEST=anthropic / openai-responses / "
                                       "openai-chat / openai のときだけ")


class _NoKeyring:
    def get_password(self, *a):
        return None

    def set_password(self, *a):  # pragma: no cover
        raise RuntimeError("試験では保存しない")

    def delete_password(self, *a):  # pragma: no cover
        raise RuntimeError("試験では消さない")


def test_live_box(tmp_path):
    pytest.importorskip("anthropic")
    pytest.importorskip("openai")
    pytest.importorskip("mcp")
    pytest.importorskip("gmsh")
    from gsis_tools.gui.ai.backend import AiBackend
    from gsis_tools.gui.ai.secrets import KeyResolver, KeyStore
    from gsis_tools.gui.ai.settings import AiSettings

    fmt, api = LIVE_KINDS[LIVE]
    s = AiSettings(format=fmt)
    s.current.api = api
    s.workspace = str(tmp_path / "ws")
    s.tool_limit = 12
    model = os.environ.get("GSIS_AI_LIVE_MODEL", "").strip()
    if model:
        s.current.model = model
    elif fmt == "openai":
        pytest.skip("OpenAI 形式は GSIS_AI_LIVE_MODEL でモデルを指定してください")
    if os.environ.get("GSIS_AI_LIVE_BASE_URL"):
        s.current.base_url = os.environ["GSIS_AI_LIVE_BASE_URL"]
        s.current.use_env_key = True
    if os.environ.get("GSIS_AI_LIVE_NO_KEY") == "1":
        s.current.no_key = True
    events: list = []
    b = AiBackend(settings=s, resolver=KeyResolver(KeyStore(_NoKeyring())), emit=lambda k, p: events.append((k, p)),
                  root=tmp_path)
    try:
        assert b.send("1 辺 10 mm の立方体を 1 つ作り、measure で体積が 1000 mm³ か確かめてください。"
                      "パッチとメッシュは要りません。最後に一言で結果を答えてください。")
        assert b.wait_idle(900)
    finally:
        b.shutdown()
    notes = [p.get("text") for k, p in events if k in ("note", "api_error")]
    assert b.last_status == "done", notes
    tools = [p["name"] for k, p in events if k == "tool_end" and p.get("ok")]
    assert "measure" in tools and any(t in tools for t in ("add_box", "batch")), tools
    answers = [p.get("text") for k, p in events if k == "assistant" and p.get("text")]
    assert answers, "最後の文章がありません"
    usage = b.conv.usage
    print(f"\n形式 {b.conv.format}、モデル {b.conv.models}、ツール {tools}、トークン 入力 {usage.input_tokens:,}"
          f"(キャッシュ読み {usage.cache_read_tokens:,})・出力 {usage.output_tokens:,}、{usage.requests} 回")
    print("最後の文章:", answers[-1][:500])
