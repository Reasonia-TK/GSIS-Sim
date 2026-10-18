"""API キーが、書き出したファイル・ログ・会話の記録・知らせに出ないこと(3 つの形式、本物の MCP サーバー)。

ダミーのキーで管の計算領域を作る反復を通し(API は HTTP の層の模擬)、作業フォルダのすべてのファイル
(.pmesh・品質レポート・状態・自動保存・会話の自動保存・サーバーのログ)、保存した会話、設定、DEBUG の水準で
集めたログ(SDK のものを含む)、画面への知らせの中身に、キーの文字列がないことを確かめる。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("openai")
pytest.importorskip("mcp")
pytest.importorskip("gmsh")

from gsis_tools.gui.ai.backend import AiBackend  # noqa: E402
from gsis_tools.gui.ai.demo import TUBE_REQUEST, ScriptedApi, ScriptTurn, tube_script  # noqa: E402
from gsis_tools.gui.ai.secrets import KeyResolver, KeyStore, Secret, install_log_redaction  # noqa: E402
from gsis_tools.gui.ai.settings import FORMAT_ANTHROPIC, FORMAT_OPENAI, AiSettings  # noqa: E402

DUMMY = {FORMAT_ANTHROPIC: "sk-ant-api03-DUMMYKEYforTESTS-0123456789abcdefABCDEF",
         FORMAT_OPENAI: "sk-proj-DUMMYKEYforTESTS-0123456789abcdefABCDEF"}


class MemoryKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, s, u):
        return self.data.get((s, u))

    def set_password(self, s, u, p):
        self.data[(s, u)] = p

    def delete_password(self, s, u):
        del self.data[(s, u)]


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:  # pragma: no cover
            self.lines.append(str(record.msg))


class DictStore:
    def __init__(self):
        self.d = {}

    def value(self, k, default=None):
        return self.d.get(k, default)

    def setValue(self, k, v):  # noqa: N802
        self.d[k] = v


def all_text(root: Path) -> list[tuple[Path, bytes]]:
    return [(p, p.read_bytes()) for p in root.rglob("*") if p.is_file()]


@pytest.mark.parametrize("fmt,api_kind", [(FORMAT_ANTHROPIC, ""), (FORMAT_OPENAI, "chat"),
                                          (FORMAT_OPENAI, "responses")])
def test_dummy_key_not_written_or_logged(tmp_path, fmt, api_kind, monkeypatch):
    key = DUMMY[fmt]
    # SDK のデバッグログも有効にして集める(GUI と同じく、受け手に伏せ字の filter を付ける)
    monkeypatch.setenv("ANTHROPIC_LOG", "debug")
    monkeypatch.setenv("OPENAI_LOG", "debug")
    cap = Capture()
    cap.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    for name in ("anthropic", "openai", "httpx2", "httpcore2", "mcp", "gsis_tools"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    flt = install_log_redaction()
    cap.addFilter(flt)
    turns = tube_script() + [ScriptTurn(status=401, error_body={"type": "error", "error": {
        "type": "authentication_error", "message": f"invalid key {key}"}} if fmt == FORMAT_ANTHROPIC
        else {"error": {"message": f"Incorrect API key provided: {key}", "type": "invalid_request_error"}})]
    api = ScriptedApi(fmt, turns)
    s = AiSettings(format=fmt)
    s.workspace = str(tmp_path / "ws")
    s.max_retries = 0
    if fmt == FORMAT_OPENAI:
        s.current.model = "demo-model"
        s.current.api = api_kind
    kr = MemoryKeyring()
    r = KeyResolver(KeyStore(kr), environ={})
    r.set_session(fmt, s.base_url, Secret(key))
    events: list = []
    b = AiBackend(settings=s, resolver=r, emit=lambda k, p: events.append((k, p)), root=tmp_path,
                  http_client_factory=api.client_factory)
    try:
        assert b.send(TUBE_REQUEST) and b.wait_idle(300)
        assert b.last_status == "done"
        # 認証のエラー(応答の本文にキーが入っている場合も伏せる)
        assert b.send("もう一度") and b.wait_idle(60)
        assert b.last_status == "error"
        conv_path = b.save_conversation(tmp_path / "saved" / "conversation.json")
        store = DictStore()
        b.settings().save(store)
        (tmp_path / "settings.json").write_text(json.dumps(store.d, ensure_ascii=False), encoding="utf-8")
    finally:
        b.shutdown()
        root.removeHandler(cap)
        root.setLevel(old_level)
    # 送ったリクエストにはキーが入っている(模擬の API が受け取った)
    if api_kind:
        assert api.requests[0]["path"] == ("/v1/responses" if api_kind == "responses" else "/v1/chat/completions")
    sent = api.requests[0]["headers"]
    assert key in (sent.get("x-api-key", "") + sent.get("authorization", ""))
    # 書き出したファイル
    files = all_text(tmp_path)
    names = {p.name for p, _ in files}
    assert {"tube.pmesh", "tube_quality.md", "tube_state.json", "state.json", "conversation_autosave.json",
            "mcp_server.log", "conversation.json", "settings.json"} <= names
    for p, data in files:
        assert key.encode() not in data, p
        assert key.encode("utf-16-le") not in data, p
    assert conv_path.is_file() and "***" in conv_path.read_text(encoding="utf-8")  # 認証のエラーの説明は伏せ字
    # ログ
    assert cap.lines, "ログが集まっていない"
    assert not [line for line in cap.lines if key in line]
    # 画面への知らせ
    blob = json.dumps([(k, {kk: str(vv) for kk, vv in p.items()}) for k, p in events], ensure_ascii=False)
    assert key not in blob
    # 資格情報マネージャーには保存しない(ユーザーが選んでいない)
    assert kr.data == {}
