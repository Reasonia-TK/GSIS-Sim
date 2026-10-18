"""AI の反復(エージェントのループ)と裏側(AiBackend)。

- 模擬の API(HTTP の層の模擬)と、MCP サーバーの代わり(プロセスを起動しない ``FakeConn``)で、ツールのエラーの
  返し方、1 回の応答の複数の呼び出し、上限、停止(API の応答待ち・ツールの実行中の強制停止)、サーバーの異常終了、
  断られた応答、API のエラーと再送、確認のモード、画像、GUI での操作の知らせ、会話の保存と読み込みを確かめる
- 形式は 3 つ(Anthropic の Messages API、OpenAI の Chat Completions API と Responses API)
- 本物の MCP サーバー(子プロセスの gsis-mcp)で、管の計算領域を 3 つの形式で作り、強制停止で起動し直して戻ること
- 試験用の小さな MCP サーバーのプロセス(``fake_mcp_server.py``)を落とし、起動し直して自動保存を読むこと
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("openai")
pytest.importorskip("mcp")
pytest.importorskip("httpx2")

import mcp_types as types  # noqa: E402

from gsis_tools.gui.ai.backend import AiBackend  # noqa: E402
from gsis_tools.gui.ai.demo import TUBE_REQUEST, ScriptedApi, ScriptTurn, tube_script  # noqa: E402
from gsis_tools.gui.ai.mcp_client import McpConnection, McpResult, McpServerDied  # noqa: E402
from gsis_tools.gui.ai.secrets import KeyResolver, KeyStore, Secret  # noqa: E402
from gsis_tools.gui.ai.session import AUTOSAVE_REL  # noqa: E402
from gsis_tools.gui.ai.settings import (  # noqa: E402
    FORMAT_ANTHROPIC,
    FORMAT_OPENAI,
    KIND_ANTHROPIC,
    KIND_OPENAI_CHAT,
    KIND_OPENAI_RESPONSES,
    AiSettings,
)

KEY = "sk-ant-DUMMY-agent-0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
# 試験の形式は会話の形の種類(FORMAT_OPENAI だけを渡したときは Chat Completions)
FORMATS = (KIND_ANTHROPIC, KIND_OPENAI_CHAT, KIND_OPENAI_RESPONSES)
CHAT = (KIND_OPENAI_CHAT, FORMAT_OPENAI)
RESP = KIND_OPENAI_RESPONSES


def format_of(kind: str) -> str:
    return FORMAT_ANTHROPIC if kind == KIND_ANTHROPIC else FORMAT_OPENAI


class MemoryKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, s, u):
        return self.data.get((s, u))

    def set_password(self, s, u, p):
        self.data[(s, u)] = p

    def delete_password(self, s, u):
        del self.data[(s, u)]


class FakeConn:
    """MCP サーバーの代わり(プロセスを起動しない)。状態(履歴)は model_save / model_load のファイルで引き継ぐ。"""

    instances: list[FakeConn] = []
    TOOLS = (("add", False), ("fail", False), ("slow", False), ("crash", False), ("measure", True),
             ("render", True), ("export_pmesh", False), ("model_save", False), ("model_load", False),
             ("undo", False))

    def __init__(self, workspace: Path, log_path: Path):
        self.workspace = Path(workspace)
        self.alive = False
        self.calls: list[tuple[str, dict]] = []
        self.history: list = []
        self.instructions = "偽のサーバーの説明"
        self.server_name, self.server_version = "fake", "0"
        self.tools = [types.Tool(name=n, description=f"{n} の説明", input_schema={"type": "object", "properties": {}},
                                 annotations=types.ToolAnnotations(read_only_hint=ro)) for n, ro in self.TOOLS]
        self.tools.append(types.Tool(name="gui_surface", description="gui", annotations=types.ToolAnnotations(
            read_only_hint=True), input_schema={"type": "object", "properties": {}}, meta={"gsis-pbc/gui-only": True}))
        FakeConn.instances.append(self)

    async def start(self, timeout: float = 30) -> None:
        self.alive = True

    async def close(self, timeout: float = 15) -> None:
        self.alive = False

    @staticmethod
    def ok(d: dict, images=None) -> McpResult:
        return McpResult(json.dumps(d, ensure_ascii=False), d, images or [], False)

    async def call(self, name: str, args: dict | None = None, timeout: float | None = None) -> McpResult:
        if not self.alive:
            raise McpServerDied("dead")
        args = dict(args or {})
        self.calls.append((name, args))
        if name == "add":
            self.history.append(args.get("x", 1))
            return self.ok({"history": list(self.history)})
        if name == "fail":
            d = {"error": "わざと失敗しました", "hint": "x を付けてください"}
            return McpResult(json.dumps(d, ensure_ascii=False), d, [], True)
        if name == "slow":
            await asyncio.sleep(float(args.get("seconds", 30)))
            self.history.append("slow")
            return self.ok({"history": list(self.history)})
        if name == "crash":
            self.alive = False
            raise McpServerDied("落ちました")
        if name == "render":
            return self.ok({"path": "renders/a.png"}, [PNG] if args.get("inline") else [])
        if name == "export_pmesh":
            return self.ok({"path": args.get("path", "a.pmesh"), "check": "OK"})
        if name == "model_save":
            p = self.workspace / args["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"history": self.history}), encoding="utf-8")
            return self.ok({"path": args["path"]})
        if name == "model_load":
            self.history = json.loads((self.workspace / args["path"]).read_text(encoding="utf-8"))["history"]
            return self.ok({"loaded": args["path"]})
        if name == "undo":
            self.history.pop()
            return self.ok({"undone": ["add"]})
        if name == "gui_surface":
            return self.ok({"key": str(len(self.history)), "model": "fake", "unit": "mm", "steps": len(self.history),
                            "volumes": 1, "source": "geometry", "mesh": {}, "patches": [], "faces": []})
        return self.ok({"ok": True})


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeConn.instances.clear()
    yield


def make_backend(tmp_path: Path, kind: str, turns: list[ScriptTurn], *, conn=FakeConn, **kw):
    fmt = format_of(kind)
    api = ScriptedApi(fmt, turns)
    s = AiSettings(format=fmt)
    s.workspace = str(tmp_path / "ws")
    s.max_retries = 0
    if fmt == FORMAT_OPENAI:
        s.current.model = "demo-model"
        s.current.api = "responses" if kind == RESP else "chat"
    for k, v in kw.items():
        setattr(s, k, v)
    assert s.provider_kind() == (KIND_OPENAI_CHAT if kind == FORMAT_OPENAI else kind)
    r = KeyResolver(KeyStore(MemoryKeyring()), environ={})
    r.set_session(fmt, s.base_url, Secret(KEY))
    events: list[tuple[str, dict]] = []
    b = AiBackend(settings=s, resolver=r, emit=lambda k, p: events.append((k, p)), root=tmp_path,
                  http_client_factory=api.client_factory, connection_factory=conn)
    return b, api, events


def kinds(events, kind):
    return [p for k, p in events if k == kind]


def run_turn(b: AiBackend, text: str, timeout: float = 120) -> str:
    assert b.send(text)
    assert b.wait_idle(timeout)
    return b.last_status


def wait_for(cond, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def history_of(kind: str, body: dict) -> list[dict]:
    """リクエストの本文の中の会話(Responses は input、ほかは messages)。"""
    return body["input"] if kind == RESP else body["messages"]


def tool_names(kind: str, tools: list[dict]) -> list[str]:
    return [t["function"]["name"] if kind in CHAT else t["name"] for t in tools]


def user_text(kind: str, m: dict) -> str:
    return m["content"] if kind in CHAT else m["content"][0]["text"]


def system_of(kind: str, body: dict) -> str:
    if kind == KIND_ANTHROPIC:
        return body["system"][0]["text"]
    return body["instructions"] if kind == RESP else body["messages"][0]["content"]


def tool_results_of(kind: str, messages: list[dict]) -> dict[str, dict]:
    """履歴の中のツールの結果(呼び出しの id → 結果)。"""
    out = {}
    for m in messages:
        role = m.get("role")
        if kind == KIND_ANTHROPIC and role == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    out[b["tool_use_id"]] = {"is_error": bool(b.get("is_error")), "text": b["content"][0]["text"],
                                             "content": b["content"]}
        if kind in CHAT and role == "tool":
            out[m["tool_call_id"]] = {"is_error": "error" in m["content"], "text": m["content"]}
        if kind == RESP and m.get("type") == "function_call_output":
            o = m["output"]
            text = o if isinstance(o, str) else next(x["text"] for x in o if x["type"] == "input_text")
            out[m["call_id"]] = {"is_error": "error" in text, "text": text, "content": o}
    return out


def call_ids_of(kind: str, messages: list[dict]) -> list[str]:
    ids = []
    for m in messages:
        if kind == RESP:
            if m.get("type") == "function_call":
                ids.append(m["call_id"])
            continue
        if m.get("role") != "assistant":
            continue
        if kind == KIND_ANTHROPIC:
            ids += [b["id"] for b in m["content"] if b.get("type") == "tool_use"]
        else:
            ids += [c["id"] for c in m.get("tool_calls") or []]
    return ids


def assert_history_closed(fmt: str, messages: list[dict]) -> None:
    """すべての呼び出しに結果があること(次のリクエストを API がエラーにしない)。"""
    res = tool_results_of(fmt, messages)
    assert set(call_ids_of(fmt, messages)) == set(res)


# ---------------------------------------------------------------------------
# 模擬のサーバーで
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
def test_tool_error_returned_and_multiple_calls(tmp_path, fmt):
    turns = [ScriptTurn(text="試します", calls=[("fail", {}), ("add", {"x": 1}), ("add", {"x": 2})]),
             ScriptTurn(calls=[("add", {"x": 3})]), ScriptTurn(text="終わりました")]
    b, api, events = make_backend(tmp_path, fmt, turns)
    try:
        assert run_turn(b, "箱を作って") == "done"
        conn = FakeConn.instances[-1]
        assert [c for c, _ in conn.calls if c not in ("model_save", "gui_surface")] == ["fail", "add", "add", "add"]
        assert conn.history == [1, 2, 3]
        # 2 回目のリクエスト: 3 つの結果(順に)。失敗は is_error で、理由と直し方を AI に返す
        msgs = history_of(fmt, api.requests[1]["json"])
        if fmt == KIND_ANTHROPIC:
            last = msgs[-1]
            assert last["role"] == "user" and [x["type"] for x in last["content"]] == ["tool_result"] * 3
            assert last["content"][0]["is_error"] is True and "わざと失敗" in last["content"][0]["content"][0]["text"]
            assert "x を付けて" in last["content"][0]["content"][0]["text"] and "is_error" not in last["content"][1]
        elif fmt == RESP:
            # 応答の項目(message・function_call)をそのまま返し、呼び出しごとに function_call_output(call_id)
            assert [m.get("type") for m in msgs[-7:]] == ["message"] + ["function_call"] * 3 + \
                ["function_call_output"] * 3
            assert [m["call_id"] for m in msgs[-3:]] == [m["call_id"] for m in msgs[-6:-3]]
            assert "わざと失敗" in msgs[-3]["output"] and "x を付けて" in msgs[-3]["output"]
        else:
            assert [m["role"] for m in msgs[-4:]] == ["assistant", "tool", "tool", "tool"]
            assert "わざと失敗" in msgs[-3]["content"] and msgs[-3]["tool_call_id"] == msgs[-4]["tool_calls"][0]["id"]
        assert_history_closed(fmt, b.conv.messages)
        ends = kinds(events, "tool_end")
        assert [e["ok"] for e in ends] == [False, True, True, True]
        # 自動保存(読むだけでない手順の後)
        assert (Path(b.workspace) / AUTOSAVE_REL).is_file()
        assert kinds(events, "usage")[-1]["total"]["requests"] == 3
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_tool_limit(tmp_path, fmt):
    turns = [ScriptTurn(calls=[("add", {"x": 1}), ("add", {"x": 2}), ("add", {"x": 3})]), ScriptTurn(text="続き")]
    b, api, events = make_backend(tmp_path, fmt, turns, tool_limit=2)
    try:
        assert run_turn(b, "3 回足して") == "limit"
        assert FakeConn.instances[-1].history == [1, 2]
        res = tool_results_of(fmt, b.conv.messages)
        texts = [r["text"] for r in res.values()]
        assert sum("上限" in t for t in texts) == 1
        assert_history_closed(fmt, b.conv.messages)
        assert any("上限" in n["text"] for n in kinds(events, "note"))
        assert len(api.requests) == 1
        # 「続けて」で続けられる(上限は指示ごと)
        assert run_turn(b, "続けて") == "done"
        assert len(api.requests) == 2
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_stop_while_waiting_for_api(tmp_path, fmt):
    turns = [ScriptTurn(text="遅い応答", delay=20), ScriptTurn(text="次")]
    b, api, events = make_backend(tmp_path, fmt, turns)
    try:
        assert b.send("遅い")
        assert wait_for(lambda: len(api.requests) == 1, 30)
        t0 = time.time()
        b.stop()
        assert b.wait_idle(10) and time.time() - t0 < 5
        assert b.last_status == "stopped"
        assert [m["role"] for m in b.conv.messages] == ["user"]  # 途中の応答は足さない
        assert run_turn(b, "もう一度") == "done"  # 続けて送れる(user が 2 つ続いてもよい)
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_force_stop_during_tool_restarts_and_restores(tmp_path, fmt):
    turns = [ScriptTurn(calls=[("add", {"x": 1})]), ScriptTurn(calls=[("slow", {"seconds": 60}), ("add", {"x": 9})]),
             ScriptTurn(text="あとで")]
    b, api, events = make_backend(tmp_path, fmt, turns)
    try:
        assert b.send("ゆっくり")
        assert wait_for(lambda: FakeConn.instances and any(c == "slow" for c, _ in FakeConn.instances[0].calls), 30)
        t0 = time.time()
        b.stop(grace=0.3)
        assert b.wait_idle(20) and time.time() - t0 < 10
        assert b.last_status == "stopped"
        # サーバーを起動し直し、自動保存(add 1 の後)を読み込んだ
        assert len(FakeConn.instances) == 2 and FakeConn.instances[1].history == [1]
        assert ("model_load", {"path": AUTOSAVE_REL}) in FakeConn.instances[1].calls
        res = tool_results_of(fmt, b.conv.messages)
        texts = list(res.values())
        assert any("途中で止めました" in r["text"] for r in texts)
        assert any("実行しませんでした" in r["text"] for r in texts)  # add 9 は実行しない
        assert_history_closed(fmt, b.conv.messages)
        assert any("強制停止" in n["text"] for n in kinds(events, "note"))
        assert run_turn(b, "続けて") == "done"
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_server_crash_recovers_from_autosave(tmp_path, fmt):
    turns = [ScriptTurn(calls=[("add", {"x": 1}), ("crash", {})]), ScriptTurn(calls=[("add", {"x": 2})]),
             ScriptTurn(text="直しました")]
    b, api, events = make_backend(tmp_path, fmt, turns)
    try:
        assert run_turn(b, "落ちる操作") == "done"
        assert len(FakeConn.instances) == 2 and FakeConn.instances[1].history == [1, 2]
        res = tool_results_of(fmt, b.conv.messages)
        crash = [r for r in res.values() if "異常終了" in r["text"]]
        assert len(crash) == 1 and crash[0]["is_error"]
        assert any(p.get("state") == "restarting" for p in kinds(events, "server"))
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", (KIND_ANTHROPIC, RESP))
def test_refusal_is_not_appended(tmp_path, fmt):
    b, api, events = make_backend(tmp_path, fmt, [ScriptTurn(text="途中", stop="refusal")])
    try:
        assert run_turn(b, "x") == "refusal"
        assert [m["role"] for m in b.conv.messages] == ["user"]
        assert any("断りました" in n["text"] for n in kinds(events, "note"))
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_api_error_then_resend(tmp_path, fmt):
    body = ({"type": "error", "error": {"type": "api_error", "message": "内部のエラー"}} if fmt == FORMAT_ANTHROPIC
            else {"error": {"message": "内部のエラー"}})
    turns = [ScriptTurn(status=500, error_body=body), ScriptTurn(text="できました")]
    b, api, events = make_backend(tmp_path, fmt, turns)
    try:
        assert run_turn(b, "作って") == "error"
        err = kinds(events, "api_error")[-1]
        assert err["retryable"] and "500" in err["text"]
        assert b.resend()
        assert b.wait_idle(30) and b.last_status == "done"
        assert [m["role"] for m in b.conv.messages] == ["user", "assistant"]
        assert len(kinds(events, "user")) == 1  # 再送では user を足さない
    finally:
        b.shutdown()


def test_api_key_missing_and_auth_error(tmp_path):
    b, api, events = make_backend(tmp_path, FORMAT_ANTHROPIC, [ScriptTurn(status=401, error_body={
        "type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})])
    try:
        assert run_turn(b, "x") == "error"
        err = kinds(events, "api_error")[-1]
        assert not err["retryable"] and "401" in err["text"] and KEY not in err["text"]
        b.resolver.clear_session()
        assert run_turn(b, "y") == "config"
        assert any("API キーがありません" in n["text"] for n in kinds(events, "note"))
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_confirm_mode(tmp_path, fmt):
    turns = [ScriptTurn(calls=[("add", {"x": 1}), ("add", {"x": 2})]), ScriptTurn(text="了解")]
    answers = iter([False, True])
    holder: dict = {}

    def emit(k, p):
        holder["events"].append((k, p))
        if k == "confirm":
            holder["b"].answer_confirm(p["id"], next(answers))

    b, api, events = make_backend(tmp_path, fmt, turns, confirm_tools=True)
    holder.update(events=events, b=b)
    b._emit_cb = emit
    try:
        assert run_turn(b, "確認して") == "done"
        assert FakeConn.instances[-1].history == [2]
        res = list(tool_results_of(fmt, b.conv.messages).values())
        assert "断りました" in res[0]["text"] and res[0]["is_error"]
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_images_only_when_enabled(tmp_path, fmt):
    turns = [ScriptTurn(calls=[("render", {"width": 4000, "height": 3000})]), ScriptTurn(text="見ました")]
    b, api, events = make_backend(tmp_path, fmt, turns, send_images=True)
    try:
        assert run_turn(b, "見せて") == "done"
        conn = FakeConn.instances[-1]
        args = next(a for c, a in conn.calls if c == "render")
        assert args["inline"] is True and max(args["width"], args["height"]) <= 1568  # 大きすぎる画像は縮めて頼む
        msgs = history_of(fmt, api.requests[1]["json"])
        if fmt == KIND_ANTHROPIC:
            assert msgs[-1]["content"][0]["content"][1]["type"] == "image"
        elif fmt == RESP:  # Responses: 画像は関数の結果の中に入れる
            out = msgs[-1]["output"]
            assert msgs[-1]["type"] == "function_call_output" and [x["type"] for x in out] == ["input_text", "input_image"]
            assert out[1]["image_url"].startswith("data:image/png;base64,")
        else:
            assert msgs[-1]["role"] == "user" and msgs[-1]["content"][1]["type"] == "image_url"
        assert kinds(events, "tool_end")[0]["image"] == PNG
    finally:
        b.shutdown()
    # 画像を送らない設定: render は AI のツールの一覧に入らない(呼ばれたら未知のツール)
    FakeConn.instances.clear()
    b, api, events = make_backend(tmp_path / "2", fmt, list(turns), send_images=False)
    try:
        assert run_turn(b, "見せて") == "done"
        names = tool_names(fmt, api.requests[0]["json"]["tools"])
        assert "render" not in names and "gui_surface" not in names and "add" in names
        res = list(tool_results_of(fmt, b.conv.messages).values())
        assert "未知のツール" in res[0]["text"]
        assert "image" not in json.dumps(history_of(fmt, api.requests[1]["json"])[-1])
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_gui_operations_are_told_to_ai(tmp_path, fmt):
    turns = [ScriptTurn(calls=[("add", {"x": 1}), ("add", {"x": 2})]), ScriptTurn(text="できた"), ScriptTurn(text="了解")]
    b, api, events = make_backend(tmp_path, fmt, turns)
    try:
        assert run_turn(b, "足して") == "done"
        assert b.undo() and b.wait_idle(10)
        assert FakeConn.instances[-1].history == [1]
        assert b.pending_gui_notes() and "元に戻す" in b.pending_gui_notes()[0]
        assert run_turn(b, "続き") == "done"
        last_user = history_of(fmt, api.requests[-1]["json"])[-1]
        text = user_text(fmt, last_user)
        assert text.startswith("[GUI での操作]") and "続き" in text
        assert not b.pending_gui_notes()
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", (KIND_ANTHROPIC, RESP))
def test_max_tokens_cut_tool_calls_not_run(tmp_path, fmt):
    turns = [ScriptTurn(calls=[("add", {"x": 1})], stop="max_tokens"), ScriptTurn(text="分けます")]
    b, api, events = make_backend(tmp_path, fmt, turns)
    try:
        assert run_turn(b, "x") == "done"
        assert FakeConn.instances[-1].history == []
        res = list(tool_results_of(fmt, b.conv.messages).values())
        assert "最大トークン" in res[0]["text"]
        assert_history_closed(fmt, b.conv.messages)
    finally:
        b.shutdown()


def test_invalid_arguments_and_unknown_tool_openai(tmp_path):
    turns = [ScriptTurn(calls=[("add", {}), ("no_such_tool", {})], raw_arguments={0: '{"x": '}),
             ScriptTurn(text="直します")]
    b, api, events = make_backend(tmp_path, FORMAT_OPENAI, turns)
    try:
        assert run_turn(b, "x") == "done"
        res = list(tool_results_of(FORMAT_OPENAI, b.conv.messages).values())
        assert "JSON" in res[0]["text"] and "未知のツール" in res[1]["text"]
        assert FakeConn.instances[-1].history == []
    finally:
        b.shutdown()


@pytest.mark.parametrize("fmt", FORMATS)
def test_conversation_save_and_load(tmp_path, fmt):
    turns = [ScriptTurn(text="作ります", calls=[("add", {"x": 5})]), ScriptTurn(text="できました")]
    b, api, events = make_backend(tmp_path, fmt, turns)
    try:
        assert run_turn(b, "5 を足して") == "done"
        path = b.save_conversation(tmp_path / "conv.json")
        assert (Path(b.workspace) / ".gsis_gui" / "conversation_autosave.json").is_file()
    finally:
        b.shutdown()
    FakeConn.instances.clear()
    b2, api2, events2 = make_backend(tmp_path / "other", fmt, [ScriptTurn(text="続けます")])
    try:
        assert b2.load_conversation(path) and b2.wait_idle(30)
        conv = kinds(events2, "conversation")[-1]
        roles = [r["role"] for r in conv["records"]]
        assert roles[0] == "user" and "tool" in roles and roles[-1] == "assistant"
        assert FakeConn.instances[-1].history == [5]  # 保存したときのモデルの状態を読み込んだ
        assert run_turn(b2, "続き") == "done"
        sent = api2.requests[0]["json"]
        # 前の会話 + 新しい指示。Anthropic は 4 + 1、Chat Completions は先頭にシステムのメッセージ(1 + 4 + 1)、
        # Responses は項目ごと(user・message・function_call・function_call_output・message + 1)
        assert len(history_of(fmt, sent)) == (5 if fmt == KIND_ANTHROPIC else 6)
        # 固定したシステムとツールは、保存したものを使う
        assert "偽のサーバーの説明" in system_of(fmt, sent)
    finally:
        b2.shutdown()
    # 形式(OpenAI の API の種類を含む)が違う会話は開かない
    FakeConn.instances.clear()
    other = {KIND_ANTHROPIC: RESP, KIND_OPENAI_CHAT: RESP, RESP: KIND_OPENAI_CHAT}[fmt]
    b3, _a, events3 = make_backend(tmp_path / "third", other, [])
    try:
        b3.load_conversation(path)
        b3.wait_idle(30)
        assert any("形式" in n["text"] for n in kinds(events3, "note"))
    finally:
        b3.shutdown()


def test_list_models(tmp_path):
    b, api, events = make_backend(tmp_path, FORMAT_OPENAI, [])
    try:
        assert b.list_models().result(30) == sorted(api.models)
    finally:
        b.shutdown()


# ---------------------------------------------------------------------------
# 本物の MCP サーバーで
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
def test_tube_with_real_server(tmp_path, fmt):
    pytest.importorskip("gmsh")
    b, api, events = make_backend(tmp_path, fmt, tube_script(), conn=None)
    try:
        assert run_turn(b, TUBE_REQUEST, timeout=300) == "done"
        ws = Path(b.workspace)
        assert (ws / "tube" / "tube.pmesh").is_file() and (ws / "tube" / "tube_quality.md").is_file()
        assert (ws / AUTOSAVE_REL).is_file()
        ends = kinds(events, "tool_end")
        assert len(ends) == 13 and all(e["ok"] for e in ends), [e["text"][:200] for e in ends if not e["ok"]]
        surf = b.surface
        assert {p["name"] for p in surf.patches} == {"inlet", "outlet", "wall"} and surf.unassigned_faces() == []
        assert surf.source == "mesh" and surf.triangles.shape[0] > 100 and len(surf.faces) == 7
        assert kinds(events, "pmesh")[-1]["path"].endswith("tube.pmesh")
        # AI に渡したツールに GUI 専用のものはない
        names = set(tool_names(fmt, api.requests[0]["json"]["tools"]))
        assert "gui_surface" not in names and "render" not in names and len(names) == 30
        from gsis_tools.pmesh import read_pmesh

        m = read_pmesh(ws / "tube" / "tube.pmesh")
        assert [p.name for p in m.patches] == ["inlet", "outlet", "wall"] and m.n_cells > 1000
    finally:
        b.shutdown()


@pytest.mark.slow
def test_force_stop_real_server_mesh(tmp_path):
    """本物のサーバーで長いメッシュの生成を止めると、サーバーを起動し直して直前の状態に戻る。"""
    pytest.importorskip("gmsh")
    turns = [ScriptTurn(calls=[("model_new", {"name": "box", "unit": "mm"}),
                               ("add_box", {"corner": [0, 0, 0], "size": [1, 1, 1], "name": "box"}),
                               ("assign_patch", {"name": "wall", "select": {"unassigned": True}}),
                               ("mesh_settings", {"size_max": 0.012}),
                               ("mesh_generate", {"max_cells": 5_000_000})]),
             ScriptTurn(text="止まりました")]
    b, api, events = make_backend(tmp_path, FORMAT_ANTHROPIC, turns, conn=None)
    try:
        assert b.send("細かいメッシュ")
        assert wait_for(lambda: any(p.get("name") == "mesh_generate" for p in kinds(events, "tool_start")), 120)
        time.sleep(1.0)
        t0 = time.time()
        b.stop(grace=0.3)
        assert b.wait_idle(60)
        assert b.last_status == "stopped" and time.time() - t0 < 30
        assert b.session.restarts == 1 and b.session.alive
        info = b.call_soon(b.session.call("model_info", {}), 60)
        assert not info.is_error and info.data["volumes"][0]["volume"] == pytest.approx(1.0)
        assert info.data["patches"] == {"wall": {"kind": "wall", "faces": 6}} and not info.data["mesh"]["exists"]
        res = list(tool_results_of(FORMAT_ANTHROPIC, b.conv.messages).values())
        assert any("途中で止めました" in r["text"] for r in res)
    finally:
        b.shutdown()


def test_process_crash_with_fake_server(tmp_path):
    """MCP サーバーのプロセスが落ちたら McpServerDied になり、起動し直して自動保存を読める。"""
    script = Path(__file__).with_name("fake_mcp_server.py")

    def factory(ws: Path, log: Path) -> McpConnection:
        return McpConnection(ws, command=sys.executable, args=[str(script), "--workspace", str(ws)], log_path=log)

    turns = [ScriptTurn(calls=[("add", {"x": 1}), ("add", {"x": 2}), ("crash", {})]), ScriptTurn(calls=[("add", {"x": 3})]),
             ScriptTurn(text="続けました")]
    b, api, events = make_backend(tmp_path, FORMAT_ANTHROPIC, turns, conn=factory)
    try:
        assert run_turn(b, "x", timeout=120) == "done"
        assert b.session.restarts == 1
        r = b.call_soon(b.session.call("add", {"x": 4}), 60)
        assert r.data["history"] == [1, 2, 3, 4]  # 自動保存(add 2 の後)から続いている
        res = list(tool_results_of(FORMAT_ANTHROPIC, b.conv.messages).values())
        assert sum("異常終了" in x["text"] for x in res) == 1
    finally:
        b.shutdown()


def test_mcp_connection_lists_gui_tools_with_meta(tmp_path):
    pytest.importorskip("gmsh")

    async def go():
        c = McpConnection(tmp_path / "ws", log_path=tmp_path / "log.txt")
        await c.start()
        try:
            names = {t.name for t in c.tools}
            gs = next(t for t in c.tools if t.name == "gui_surface")
            assert gs.meta == {"gsis-pbc/gui-only": True} and len(names) == 32
            r = await c.call("gui_surface", {})
            assert not r.is_error and r.data["n_triangles"] == 0
            r = await c.call("no_such_tool", {})
            assert r.is_error
        finally:
            await c.close()
        assert not c.alive

    asyncio.run(go())


def test_server_env_has_no_api_keys(monkeypatch):
    """子プロセスの MCP サーバーには、API キーの環境変数を渡さない(SDK の既定の安全な環境変数だけ)。"""
    from mcp.client.stdio import get_default_environment

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-pass-000000")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-pass-0000000000000")
    env = get_default_environment()
    assert "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env and "PATH" in env
