"""AI モデリングの Qt なしの部分: 設定、API キーの扱い、ツールの定義の変換、AI への指示、会話の保存、3D 表示のデータ。

実際の API は呼ばない。環境変数と OS の資格情報マネージャーは、試験用の差し替えを使う(本物のキーを読まない)。
"""

from __future__ import annotations

import base64
import json
import logging

import numpy as np
import pytest

from gsis_tools.gui.ai import secrets
from gsis_tools.gui.ai.conversation import Conversation
from gsis_tools.gui.ai.prompt import build_system_prompt
from gsis_tools.gui.ai.secrets import KeyResolver, KeyStore, RedactingFilter, Secret, redact
from gsis_tools.gui.ai.settings import (
    FORMAT_ANTHROPIC,
    FORMAT_OPENAI,
    AiSettings,
    default_workspace,
    normalize_base_url,
)
from gsis_tools.gui.ai.surface import face_border_lines, face_label_points, update_surface
from gsis_tools.gui.ai.tooldefs import GUI_ONLY_TOOLS, inline_refs, tools_for_ai
from gsis_tools.gui.ai.types import ToolDef, Usage

DUMMY = "sk-ant-test-DUMMY-0123456789abcdefghij"


class MemoryKeyring:
    """keyring の差し替え(OS の資格情報マネージャーに触らない)。"""

    def __init__(self):
        self.data: dict[tuple[str, str], str] = {}
        self.calls: list[str] = []

    def get_password(self, service, user):
        self.calls.append("get")
        return self.data.get((service, user))

    def set_password(self, service, user, password):
        self.calls.append("set")
        self.data[(service, user)] = password

    def delete_password(self, service, user):
        self.calls.append("delete")
        if (service, user) not in self.data:
            raise KeyError(user)
        del self.data[(service, user)]


class DictStore:
    """QSettings の代わり(value / setValue)。"""

    def __init__(self):
        self.d: dict[str, object] = {}

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, v):  # noqa: N802
        self.d[key] = v


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------


def test_settings_defaults_and_roundtrip():
    s = AiSettings()
    assert s.format == FORMAT_ANTHROPIC and s.model == "claude-opus-5-5"  # ユーザーの決定(2026-10-18)
    assert s.current.models[:2] == ["claude-opus-5-5", "claude-fable-5-1"]  # Fable 5.1 は一覧に残す
    assert s.base_url == "https://api.anthropic.com" and s.is_official()
    assert s.per_format[FORMAT_OPENAI].model == ""  # OpenAI 形式の既定のモデルは固定しない
    assert s.tool_limit == 40 and s.send_images is False
    s.format = FORMAT_OPENAI
    s.current.model = "my-model"
    s.current.base_url = "http://localhost:11434/v1/"
    s.tool_limit = 12
    s.send_images = True
    st = DictStore()
    s.save(st)
    assert not any("key" in k.lower() and "api" in k.lower() for k in st.d)  # キーの欄はない
    t = AiSettings.load(st)
    assert t.format == FORMAT_OPENAI and t.model == "my-model" and t.base_url == "http://localhost:11434/v1"
    assert t.tool_limit == 12 and t.send_images is True
    assert t.per_format[FORMAT_ANTHROPIC].model == "claude-opus-5-5"
    # ini は文字列で返す
    st.d = {k: (str(v).lower() if isinstance(v, bool) else str(v)) for k, v in st.d.items()}
    u = AiSettings.load(st)
    assert u.send_images is True and u.tool_limit == 12


def test_settings_effective_params():
    s = AiSettings()
    assert s.effective_thinking_display() == "updates" and s.use_fallbacks()
    s.current.model = "claude-haiku-5-5"
    assert s.effective_thinking_display() == "omit" and not s.use_fallbacks()
    s.current.model = "claude-fable-5-1"
    assert s.effective_thinking_display() == "updates" and s.use_fallbacks()
    s.current.model = "claude-opus-5-5"
    s.current.base_url = "https://proxy.example.com"
    assert not s.is_official() and not s.use_fallbacks() and s.effective_thinking_display() == "omit"
    o = AiSettings(format=FORMAT_OPENAI)
    o.current.model = "m"
    assert o.effective_token_param() == "max_completion_tokens"
    o.current.base_url = "http://localhost:8000/v1"
    assert o.effective_token_param() == "max_tokens"
    o.current.token_param = "max_completion_tokens"
    assert o.effective_token_param() == "max_completion_tokens"
    o.current.extra_body = "[1, 2]"
    assert any("オブジェクト" in p for p in o.problems())
    o.current.extra_body = '{"reasoning_effort": "low"}'
    assert o.problems() == [] and o.extra_body() == {"reasoning_effort": "low"}
    o.current.model = ""
    assert any("モデル" in p for p in o.problems())
    assert normalize_base_url("", FORMAT_OPENAI) == "https://api.openai.com/v1"


def test_settings_openai_api_kind():
    """OpenAI 形式の API の種類: 自動なら公式の接続先は Responses、ほかは Chat Completions。会話の形の種類も変わる。"""
    a = AiSettings()
    assert a.provider_kind() == "anthropic" and a.effective_openai_api() == "chat"
    o = AiSettings(format=FORMAT_OPENAI)
    assert o.current.api == "auto" and o.current.store is False
    assert o.effective_openai_api() == "responses" and o.provider_kind() == "openai-responses"
    o.current.base_url = "http://localhost:11434/v1"
    assert o.effective_openai_api() == "chat" and o.provider_kind() == "openai-chat"
    o.current.api = "responses"
    assert o.provider_kind() == "openai-responses"
    o.current.base_url = ""
    o.current.api = "chat"
    assert o.provider_kind() == "openai-chat"
    o.current.store = True
    st = DictStore()
    o.save(st)
    t = AiSettings.load(st)
    assert t.current.api == "chat" and t.current.store is True
    st.d["ai/openai/api"] = "unknown"
    assert AiSettings.load(st).current.api == "auto"


def test_default_workspace(monkeypatch, tmp_path):
    monkeypatch.delenv("GSIS_GUI_AI_WORKSPACE", raising=False)
    monkeypatch.delenv("GSIS_GUI_SETTINGS", raising=False)
    assert default_workspace(tmp_path) == tmp_path / "mcp_workspace" / "gui"
    monkeypatch.setenv("GSIS_GUI_SETTINGS", str(tmp_path / "s" / "x.ini"))
    assert default_workspace(tmp_path) == tmp_path / "s" / "ai_workspace"
    monkeypatch.setenv("GSIS_GUI_AI_WORKSPACE", str(tmp_path / "w"))
    assert default_workspace(tmp_path) == tmp_path / "w"


# ---------------------------------------------------------------------------
# API キー
# ---------------------------------------------------------------------------


def test_secret_hides_value():
    k = Secret(DUMMY)
    assert k.reveal() == DUMMY
    for text in (repr(k), str(k), f"{k}", "%s" % k, json.dumps({"k": str(k)})):
        assert DUMMY not in text
    with pytest.raises(TypeError):
        import pickle

        pickle.dumps(k)
    assert redact(f"error with key {DUMMY} here") == "error with key *** here"
    # 登録していないキーらしい文字列も伏せる
    assert "sk-proj-" not in redact("Bearer sk-proj-ABCDEFGHIJKLMNOPQRSTUV")
    assert redact("x-api-key: abcdefghijkl") == "x-api-key: ***"
    assert redact("普通の文章") == "普通の文章"


def test_redacting_filter():
    Secret(DUMMY)
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "key=%s", (DUMMY,), None)
    RedactingFilter().filter(rec)
    assert DUMMY not in rec.getMessage()


def test_redact_obj_keeps_opaque_values():
    """API が返す不透明な値(暗号化された推論・考えの署名)は書き換えない(送り返せなくなるため)。ほかは伏せる。"""
    blob = "gAAAAB-x-sk-ABCDEFGHIJKLMNOPQRSTUVWX_yz"  # URL で使える base64 の中に、キーらしい並びが入りうる
    assert redact(blob) != blob
    obj = {"type": "reasoning", "encrypted_content": blob, "summary": [{"text": "sk-proj-ABCDEFGHIJKLMNOPQRSTUV"}],
           "content": [{"type": "thinking", "signature": blob}], "text": f"key {DUMMY}"}
    out = secrets.redact_obj(obj)
    assert out["encrypted_content"] == blob and out["content"][0]["signature"] == blob
    assert "sk-proj-" not in out["summary"][0]["text"] and DUMMY not in out["text"]


def test_key_resolution_order():
    kr = MemoryKeyring()
    store = KeyStore(kr)
    env = {"ANTHROPIC_API_KEY": "sk-ant-env-0000000000000000", "OPENAI_API_KEY": "sk-env-1111111111111111111"}
    r = KeyResolver(store, environ=env)
    s = AiSettings()
    # 環境変数(公式の接続先)
    res = r.resolve(s)
    assert res.source == "env" and res.secret.reveal() == env["ANTHROPIC_API_KEY"] and "ANTHROPIC_API_KEY" in res.label
    # 資格情報マネージャー
    store.set(s.format, s.base_url, Secret("sk-ant-keyring-22222222222"))
    assert r.resolve(s).source == "keyring"
    # その場の入力が最優先
    r.set_session(s.format, s.base_url, Secret("sk-ant-session-3333333333"))
    assert r.resolve(s).source == "session"
    r.set_session(s.format, s.base_url, None)
    assert r.resolve(s).source == "keyring"
    assert store.delete(s.format, s.base_url) and not store.delete(s.format, s.base_url)
    # 公式以外の接続先には環境変数のキーを使わない(選んだときだけ)
    s.current.base_url = "https://proxy.example.com"
    res = r.resolve(s)
    assert res.secret is None and res.source == "none" and "公式" in res.label
    s.current.use_env_key = True
    assert r.resolve(s).source == "env"
    # OpenAI のキーなし
    o = AiSettings(format=FORMAT_OPENAI)
    o.current.base_url = "http://localhost:11434/v1"
    assert r.resolve(o).secret is None and not r.resolve(o).ok
    o.current.no_key = True
    res = r.resolve(o)
    assert res.ok and res.source == "no_key" and res.secret is None
    # ラベルにキーの値は出ない
    for lab in (r.resolve(s).label, res.label):
        assert "sk-" not in lab


def test_keystore_failures_do_not_leak():
    class Broken:
        def get_password(self, *a):
            raise RuntimeError("backend broken " + DUMMY)

        def set_password(self, *a):
            raise RuntimeError("cannot store " + DUMMY)

        def delete_password(self, *a):
            raise RuntimeError("x")

    st = KeyStore(Broken())
    Secret(DUMMY)
    assert st.get("anthropic", "") is None
    with pytest.raises(RuntimeError) as ei:
        st.set("anthropic", "", Secret(DUMMY))
    assert DUMMY not in str(ei.value)
    assert st.delete("anthropic", "") is False


# ---------------------------------------------------------------------------
# ツールの定義・AI への指示
# ---------------------------------------------------------------------------


def test_inline_refs():
    sch = {"type": "object", "properties": {"select": {"$ref": "#/$defs/Sel", "description": "面"},
                                            "loops": {"type": "array", "items": {"$ref": "#/$defs/Loop"}}},
           "$defs": {"Sel": {"type": "object", "properties": {"plane": {"$ref": "#/$defs/Plane"}}},
                     "Plane": {"type": "object", "properties": {"point": {"type": "array"}}},
                     "Loop": {"type": "object", "properties": {"inner": {"$ref": "#/$defs/Loop"}}}}}
    out = inline_refs(sch)
    assert out["properties"]["select"]["description"] == "面"
    assert out["properties"]["select"]["properties"]["plane"]["properties"]["point"]["type"] == "array"
    # 再帰する参照は残し、$defs もその分だけ残す
    assert out["properties"]["loops"]["items"]["properties"]["inner"] == {"$ref": "#/$defs/Loop"}
    assert set(out["$defs"]) == {"Loop"}
    assert inline_refs({})["type"] == "object"


def test_tools_for_ai_filters_gui_only_and_images():
    pytest.importorskip("pydantic")
    from gsis_tools.mcp.tools import GUI_ONLY_META, GUI_ONLY_TOOLS as MCP_GUI_ONLY, TOOLS, annotations_for, input_schema

    assert MCP_GUI_ONLY == GUI_ONLY_TOOLS and "gsis-pbc/gui-only" in GUI_ONLY_META
    listing = []
    for s in TOOLS:
        ann = annotations_for(s)
        listing.append({"name": s.name, "description": s.description, "inputSchema": input_schema(s),
                        "annotations": {"readOnlyHint": ann.get("read_only_hint")},
                        **({"_meta": GUI_ONLY_META} if s.name in MCP_GUI_ONLY else {})})
    on = tools_for_ai(listing, send_images=True)
    off = tools_for_ai(listing, send_images=False)
    names_on = {t.name for t in on}
    assert "gui_surface" not in names_on and "render" in names_on and "batch" in names_on
    assert "render" not in {t.name for t in off}
    # $ref は残らない(OpenAI 互換のサーバー向け)
    for t in on:
        assert "$ref" not in json.dumps(t.input_schema) and t.input_schema["type"] == "object"
    assert next(t for t in on if t.name == "measure").read_only
    # 名前だけで印がなくても外す
    assert tools_for_ai([{"name": "gui_surface", "inputSchema": {}}], send_images=True) == []


def test_system_prompt():
    p = build_system_prompt("サーバーの説明 X", send_images=False, tool_limit=40)
    for word in ("ユーザーと同じ言語", "単位", "mm", "batch", "measure", "check_geometry", "list_patches",
                 "complete", "mesh_generate", "export_pmesh", "wall", "inlet", "outlet", "symmetry", "40 回",
                 "サーバーの説明 X", '{"unassigned": true}', "render は使えない"):
        assert word in p, word
    assert "{" + "image_rule" not in p
    p2 = build_system_prompt(None, send_images=True, tool_limit=7)
    assert "画像はあなたに送られる" in p2 and "7 回" in p2


# ---------------------------------------------------------------------------
# 会話・3D 表示のデータ
# ---------------------------------------------------------------------------


def test_conversation_roundtrip_and_redaction(tmp_path):
    Secret(DUMMY)
    c = Conversation("anthropic")
    c.freeze("system", [ToolDef("a", "d", {"type": "object", "properties": {}})])
    c.freeze("other", [])  # 2 回目は変えない
    assert c.system == "system" and len(c.tools) == 1
    c.messages.append({"role": "user", "content": [{"type": "text", "text": "こんにちは"}]})
    c.add_record({"role": "user", "text": f"キーは {DUMMY} です"})
    c.usage.add(Usage(input_tokens=10, output_tokens=2, requests=1))
    p = c.save(tmp_path / "conv.json", model_state={"format": "gsis-mcp-model", "history": []})
    raw = p.read_text(encoding="utf-8")
    assert DUMMY not in raw and "***" in raw
    c2, data = Conversation.load(p)
    assert c2.format == "anthropic" and c2.system == "system" and c2.tools[0].name == "a"
    assert c2.messages == c.messages and c2.usage.input_tokens == 10 and data["model_state"]["history"] == []
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        Conversation.load(bad)


def _pack(a, dt):
    a = np.ascontiguousarray(a, dtype=dt)
    return {"dtype": np.dtype(dt).name, "shape": list(a.shape), "data": base64.b64encode(a.tobytes()).decode()}


def test_surface_decode_and_borders():
    pts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    tris = np.array([[0, 1, 2], [0, 2, 3]])
    fid = np.array([1, 2])
    data = {"key": "1:1:0", "unit": "mm", "model": "m", "source": "geometry", "volumes": 1, "steps": 1,
            "patches": [{"name": "inlet", "kind": "inlet", "faces": [1]}],
            "faces": [{"id": 1, "type": "plane", "boundary": True, "area": 0.5, "c": [0.6, 0.3, 0]},
                      {"id": 2, "type": "plane", "boundary": True, "area": 0.5, "c": [0.3, 0.6, 0]}],
            "points": _pack(pts, "<f4"), "triangles": _pack(tris, "<i4"), "face_ids": _pack(fid, "<i4")}
    s = update_surface(None, data)
    assert s.points.shape == (4, 3) and s.triangles.tolist() == tris.tolist() and s.face_ids.tolist() == [1, 2]
    assert s.patch_of() == {1: "inlet"} and s.unassigned_faces() == [2]
    assert s.patch_rows()[0]["area"] == pytest.approx(0.5)
    # 変わっていなければ前の三角形を使う
    s2 = update_surface(s, {**{k: v for k, v in data.items() if k not in ("points", "triangles", "face_ids")},
                            "unchanged": True, "patches": []})
    assert s2.triangles is s.triangles and s2.unassigned_faces() == [1, 2]
    # 面の境目: 2 つの三角形の間の対角線(面が違う)と外周の 4 辺
    lines = face_border_lines(s.points, s.triangles, s.face_ids)
    assert {tuple(e) for e in lines.tolist()} == {(0, 1), (1, 2), (2, 3), (0, 3), (0, 2)}
    lp, labels = face_label_points(s)
    assert labels == ["1", "2"] and lp.shape == (2, 3)


def test_mcp_engine_gui_surface(tmp_path):
    pytest.importorskip("gmsh")
    pytest.importorskip("pydantic")
    from gsis_tools.mcp.engine import ModelEngine
    from gsis_tools.mcp.errors import ToolFailure
    from gsis_tools.mcp.tools import available_tools, normalize
    from gsis_tools.mcp.workspace import Workspace

    assert "gui_surface" not in {t.name for t in available_tools(False)}
    assert "gui_surface" in {t.name for t in available_tools(False, gui_tools=True)}
    e = ModelEngine(Workspace(tmp_path / "ws"), threads=2)
    e.arg_normalizer = normalize
    try:
        def t(tool, **a):
            return e.call(tool, normalize(tool, a))

        empty = t("gui_surface")
        assert empty["n_triangles"] == 0 and empty["faces"] == []
        t("add_box", corner=[0, 0, 0], size=[2, 1, 1], name="box")
        r = t("gui_surface")
        s = update_surface(None, r)
        assert len(s.faces) == 6 and s.triangles.shape[0] > 0 and set(np.unique(s.face_ids)) == set(range(1, 7))
        assert s.points[:, 0].max() == pytest.approx(2.0) and s.source == "geometry"
        t("assign_patch", name="inlet", select={"on_plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}})
        r2 = t("gui_surface", known=r["key"])
        assert r2["unchanged"] and "points" not in r2 and r2["patches"][0]["name"] == "inlet"
        t("mesh_settings", size_max=0.5)
        t("mesh_generate")
        r3 = t("gui_surface", known=r["key"])
        assert not r3.get("unchanged") and r3["source"] == "mesh" and r3["key"] != r["key"]
        t("transform", entities=["box"], op="translate", vector=[1, 0, 0])
        r4 = t("gui_surface", known=r3["key"])
        assert r4["source"] == "geometry" and update_surface(None, r4).points[:, 0].min() == pytest.approx(1.0)
        with pytest.raises(ToolFailure):
            t("batch", steps=[{"tool": "gui_surface", "args": {}}])
    finally:
        e.close()


def test_secret_registry_is_global():
    v = "sk-ant-another-dummy-99999999999"
    Secret(v)
    assert v not in redact(f"x {v} y")
    assert secrets.MASK in redact(v)
