"""モデリング(AI)の画面(PySide6、QT_QPA_PLATFORM=offscreen、3D は静止画の方式)。

模擬の AI(HTTP の層の模擬)と本物の MCP サーバーで、画面から管の計算領域を作る流れを通す。設定の画面で
API キーが伏せ字になり、設定ファイルに書かれないこと、面の選択と手でのパッチの付与、元に戻す、停止、再送、
ツールを手で実行する欄、会話の保存と読み込み、書き出したメッシュをメッシュの画面で開くことを確かめる。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GSIS_GUI_3D"] = "static"

pytest.importorskip("PySide6")
pytest.importorskip("pyvista")
pytest.importorskip("tomlkit")
pytest.importorskip("anthropic")
pytest.importorskip("openai")
pytest.importorskip("mcp")
pytest.importorskip("gmsh")

from gsis_tools.gui.ai.demo import TUBE_REQUEST, ScriptedApi, ScriptTurn, tube_script  # noqa: E402
from gsis_tools.gui.ai.secrets import KeyResolver, KeyStore, Secret  # noqa: E402

KEY = "sk-ant-api03-GUI-DUMMY-0123456789abcdefXYZ"


class MemoryKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, s, u):
        return self.data.get((s, u))

    def set_password(self, s, u, p):
        self.data[(s, u)] = p

    def delete_password(self, s, u):
        del self.data[(s, u)]


def wait_until(app, cond, timeout: float = 60.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(cond())


@pytest.fixture(scope="module")
def gui(tmp_path_factory):
    d = tmp_path_factory.mktemp("aigui")
    saved_env = {k: os.environ.get(k) for k in ("GSIS_GUI_SETTINGS", "GSIS_GUI_AI_WORKSPACE")}
    os.environ["GSIS_GUI_SETTINGS"] = str(d / "gsis-gui.ini")
    os.environ["GSIS_GUI_AI_WORKSPACE"] = str(d / "ws")
    from PySide6.QtWidgets import QMessageBox

    from gsis_tools.gui.app import create_app
    from gsis_tools.gui.main_window import MainWindow

    app, ThemeManager, AppContext = create_app()
    tm = ThemeManager(app, mode="light")
    ctx = AppContext(tm)
    tm.apply()
    win = MainWindow(ctx)
    tm.apply()
    win.resize(1500, 950)
    page = win.ai_page
    kr = MemoryKeyring()
    resolver = KeyResolver(KeyStore(kr), environ={})
    page.set_resolver(resolver)
    win.show()
    app.processEvents()
    saved = (QMessageBox.question, QMessageBox.warning, QMessageBox.information)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    yield app, ctx, win, page, kr, d
    win.close()
    app.processEvents()
    QMessageBox.question, QMessageBox.warning, QMessageBox.information = saved
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def use_api(page, api: ScriptedApi) -> None:
    page.http_client_factory = api.client_factory
    if page.backend is not None:
        page.backend.http_client_factory = api.client_factory


def send_and_wait(app, page, text: str, timeout: float = 240.0) -> str:
    n = page.done_count
    assert page.send_text(text)
    assert wait_until(app, lambda: page.done_count > n and not page.busy, timeout)
    return page.last_status


def test_page_in_window(gui):
    app, ctx, win, page, kr, d = gui
    assert [win.nav.item(i).data(0x0100) for i in range(win.nav.count())] == ["geometry", "settings", "boundaries", "mesh", "run", "results"]
    win.show_page("ai")
    assert win.stack.currentWidget() is win.geometry_page
    assert win.geometry_page.tabs.currentIndex() == 1
    assert wait_until(app, lambda: page.server_state == "ready", 120)
    assert page.workspace == d / "ws"
    assert page.tool_combo.count() == 31 and page.tool_combo.findText("gui_surface") < 0  # GUI 専用は出さない
    assert "送るときに読み込みます" in page.header.text()  # 起動しただけではキーを読まない


def test_tube_from_chat_with_mock_ai(gui):
    app, ctx, win, page, kr, d = gui
    page.resolver.set_session("anthropic", page.settings.base_url, Secret(KEY))
    api = ScriptedApi("anthropic", tube_script())
    use_api(page, api)
    win.show_page("ai")
    assert send_and_wait(app, page, TUBE_REQUEST) == "done"
    cards = page.chat.tool_cards()
    assert len(cards) == 13 and all(c.ok for c in cards)
    assert cards[0].name == "model_new" and cards[1].name == "batch"
    assert not cards[1].is_open()
    cards[1].set_open(True)  # 折りたたみ
    assert cards[1].is_open() and "add_cylinder" in cards[1].args_view.toPlainText()
    cards[1].set_open(False)
    # 3D 表示とパッチの一覧
    s = page.surface
    assert s.source == "mesh" and {p["name"] for p in s.patches} == {"inlet", "outlet", "wall"}
    names = [page.patch_table.item(r, 1).text() for r in range(page.patch_table.rowCount())]
    assert names == ["inlet", "outlet", "wall", "(付いていない面)"]
    assert page.faces_table.rowCount() == 7
    scene = page.view.scene()
    assert scene is not None and len(scene.items) >= 2
    assert [n for n, _c in scene.legend][:3] == ["inlet", "outlet", "wall"]
    assert "四面体" in scene.caption
    # トークン数(料金は出さない)
    assert "入力" in page.tokens_label.text() and "出力" in page.tokens_label.text() and "$" not in page.tokens_label.text()
    # AI の書き出しは保持し、共通の形状検査と境界・メッシュの画面へ引き継ぐ。
    assert page.last_pmesh and Path(page.last_pmesh).is_file()
    link = next(w for w in page.chat.items() if hasattr(w, "open_button"))
    link.open_button.click()
    assert "境界条件を確定" in win.statusBar().currentMessage()
    win.geometry_page.adopt_btn.click()
    assert wait_until(app, lambda: win.project.geometry.get("source_kind") == "ai"
                      and not win.geometry_page.busy and not page.busy, 120)
    assert win.project.stages["geometry"] == "done"
    assert win.project.asset(win.project.geometry["ai_state"]).is_file()
    assert win.project.asset(win.project.geometry["ai_conversation"]).is_file()
    for key in ("ai_state", "ai_conversation"):
        assert KEY not in win.project.asset(win.project.geometry[key]).read_text(encoding="utf-8")
    win.project.dirty = False  # この試験の出力は一時フォルダだけに残す。
    win.show_page("ai")


def test_static_section_views(gui):
    app, ctx, win, page, kr, d = gui
    for i in range(page.section_combo.count()):
        page.section_combo.setCurrentIndex(i)
        app.processEvents()
        assert page.view.scene() is not None
    page.labels_check.setChecked(True)
    app.processEvents()
    assert len(page.view.scene().labels) == 7
    page.labels_check.setChecked(False)
    page.section_combo.setCurrentIndex(0)
    img = page.view.screenshot(size=(500, 400))
    assert img is not None and img.shape[0] == 400


def test_pick_face_and_assign_patch_then_undo(gui):
    app, ctx, win, page, kr, d = gui
    outlet_face = next(p["faces"][0] for p in page.surface.patches if p["name"] == "outlet")
    page.pick_face(outlet_face)
    app.processEvents()
    assert page.selected_faces == [outlet_face] and page.faces_edit.text() == str(outlet_face)
    page.input.clear()
    page.insert_selected_faces()
    assert page.input.toPlainText() == f"面 {outlet_face}"
    page.input.clear()
    page.patch_name.setEditText("exit")
    page.patch_kind.setCurrentIndex(page.patch_kind.findData("outlet"))
    assert page.assign_patch_to_selection()
    assert wait_until(app, lambda: not page.busy and any(p["name"] == "exit" for p in page.surface.patches), 60)
    exit_p = next(p for p in page.surface.patches if p["name"] == "exit")
    assert exit_p["faces"] == [outlet_face] and exit_p["kind"] == "outlet"
    assert next(p for p in page.surface.patches if p["name"] == "outlet")["faces"] == []
    card = page.chat.tool_cards()[-1]
    assert card.name == "assign_patch" and card.ok
    assert page.backend.pending_gui_notes() and "exit" in page.backend.pending_gui_notes()[-1]
    # 元に戻す
    assert page.undo()
    assert wait_until(app, lambda: not page.busy and not any(p["name"] == "exit" for p in page.surface.patches), 60)
    assert next(p for p in page.surface.patches if p["name"] == "outlet")["faces"] == [outlet_face]
    # 面の一覧で選ぶ
    page.faces_table.selectRow(0)
    app.processEvents()
    assert page.selected_faces == [int(page.faces_table.item(0, 0).text())]


def test_manual_tool(gui):
    app, ctx, win, page, kr, d = gui
    page.tool_combo.setCurrentText("measure")
    assert page.tool_desc.text()
    page.tool_args.setPlainText("{}")
    assert page.run_manual_tool()
    assert wait_until(app, lambda: not page.busy and "total_volume" in page.tool_result.toPlainText(), 60)
    page.tool_args.setPlainText("{not json")
    assert not page.run_manual_tool() and "JSON" in page.tool_result.toPlainText()


def test_settings_dialog_masks_key_and_never_writes_it(gui):
    app, ctx, win, page, kr, d = gui
    secret = "sk-ant-api03-TYPED-IN-DIALOG-9876543210zyx"
    dlg = page.make_settings_dialog()
    from PySide6.QtWidgets import QLineEdit

    assert dlg.key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert dlg.key_edit.text() == ""  # 前のキーを欄に戻さない
    assert "この画面で入力したキー" in dlg.key_source.text()
    dlg.key_edit.setText(secret)
    assert secret not in dlg.key_edit.displayText()
    assert all(secret not in t for t in dlg.snapshot_texts())
    assert not dlg.save_key_check.isChecked()
    dlg.tool_limit.setValue(25)
    dlg._accept()
    assert page.apply_settings_dialog(dlg)
    s = page.settings
    assert s.tool_limit == 25
    assert page.resolver.session_key("anthropic", s.base_url).reveal() == secret
    assert kr.data == {}  # 保存を選んでいないので、資格情報マネージャーにも書かない
    ini = Path(os.environ["GSIS_GUI_SETTINGS"]).read_text(encoding="utf-8")
    assert "tool_limit=25" in ini and secret not in ini and KEY not in ini
    # 保存を選んだときだけ資格情報マネージャーへ
    dlg2 = page.make_settings_dialog()
    dlg2.key_edit.setText(secret)
    dlg2.save_key_check.setChecked(True)
    dlg2._accept()
    page.apply_settings_dialog(dlg2)
    assert list(kr.data.values()) == [secret]
    assert secret not in Path(os.environ["GSIS_GUI_SETTINGS"]).read_text(encoding="utf-8")
    dlg3 = page.make_settings_dialog()
    assert "入力したキー" in dlg3.key_source.text() or "資格情報マネージャー" in dlg3.key_source.text()
    dlg3._delete_key()
    assert kr.data == {}
    dlg3.reject()
    # OpenAI 形式の欄
    dlg4 = page.make_settings_dialog()
    dlg4.format_combo.setCurrentIndex(1)
    assert dlg4.no_key_check.isVisible() or not dlg4.isVisible()
    assert dlg4.model_combo.currentText() == ""  # OpenAI の既定のモデルは固定しない
    dlg4.reject()
    # 作業フォルダ・キーのファイルにキーが出ていない
    for p in (d / "ws").rglob("*"):
        if p.is_file():
            data = p.read_bytes()
            assert secret.encode() not in data and KEY.encode() not in data, p


def test_fetch_models_in_dialog(gui):
    app, ctx, win, page, kr, d = gui
    api = ScriptedApi("anthropic", [], models=["claude-fable-5-1", "claude-opus-5-5", "claude-x-test"])
    use_api(page, api)
    dlg = page.make_settings_dialog()
    dlg._fetch()
    assert wait_until(app, lambda: dlg.fetch_btn.isEnabled(), 30)
    items = [dlg.model_combo.itemText(i) for i in range(dlg.model_combo.count())]
    assert "claude-x-test" in items and "読みました" in dlg.models_status.text()
    assert api.requests[-1]["path"] == "/v1/models"
    dlg.reject()


def test_stop_and_resend(gui):
    app, ctx, win, page, kr, d = gui
    page.reset_conversation()
    app.processEvents()
    api = ScriptedApi("anthropic", [ScriptTurn(text="遅い", delay=30),
                                    ScriptTurn(status=529, error_body={"type": "error", "error": {
                                        "type": "overloaded_error", "message": "Overloaded"}}),
                                    ScriptTurn(text="再送で届きました")])
    page.settings.max_retries = 0
    page.backend.update_settings(page.settings)
    use_api(page, api)
    n = page.done_count
    assert page.send_text("遅い応答")
    assert wait_until(app, lambda: page.busy and len(api.requests) >= 1, 30)
    assert page.stop_btn.isEnabled()
    page.stop_btn.click()
    assert wait_until(app, lambda: page.done_count > n and not page.busy, 30)
    assert page.last_status == "stopped"
    # API のエラー(混雑)→ 再送のボタン
    assert send_and_wait(app, page, "もう一度") == "error"
    note = next(w for w in reversed(page.chat.items()) if hasattr(w, "retry_button"))
    n = page.done_count
    note.retry_button.click()
    assert wait_until(app, lambda: page.done_count > n and not page.busy, 30)
    assert page.last_status == "done"
    assert "再送で届きました" in page.backend.conv.records[-1]["text"]


def test_conversation_save_reset_load(gui, tmp_path):
    app, ctx, win, page, kr, d = gui
    records = list(page.backend.conv.records)
    assert records and any(r.get("role") == "user" for r in records)
    out = page.save_conversation(tmp_path / "conv.json")
    assert out is not None and out.is_file()
    assert page.reset_conversation()
    assert wait_until(app, lambda: len(page.chat.items()) <= 1, 10)
    page.ensure_backend().load_conversation(out)
    assert wait_until(app, lambda: not page.busy and len(page.backend.conv.records) == len(records) + 1, 60)
    assert page.backend.conv.records[:len(records)] == records
    texts = []
    for w in page.chat.items():
        texts += [lab.text() for lab in w.findChildren(type(page.tokens_label))]
    assert any("再送で届きました" in t for t in texts)


def test_openai_format_from_gui(gui):
    app, ctx, win, page, kr, d = gui
    page.reset_conversation()
    s = page.settings.copy()
    s.format = "openai"
    s.current.base_url = "http://localhost:11434/v1"
    s.current.model = "demo-model"
    s.current.no_key = True
    page.settings = s
    page.backend.update_settings(s)
    api = ScriptedApi("openai", [ScriptTurn(text="測ります", calls=[("measure", {})]), ScriptTurn(text="体積を測りました")])
    use_api(page, api)
    assert send_and_wait(app, page, "体積を測って") == "done"
    assert api.requests[0]["path"] == "/v1/chat/completions" and "authorization" not in api.requests[0]["headers"]
    assert page.chat.tool_cards()[-1].name == "measure" and page.chat.tool_cards()[-1].ok
    assert "キーなし" in page.header.text()


def test_openai_responses_settings_and_run(gui):
    """設定の画面で OpenAI の API の種類(自動: 公式の接続先は Responses)を見せ、会話の形が変わるときはリセットする。"""
    app, ctx, win, page, kr, d = gui
    key = "sk-proj-GUI-DUMMY-responses-0123456789abcdef"
    assert page.backend.conv.format == "openai-chat" and not page.backend.conv.empty  # 前の試験の会話
    dlg = page.make_settings_dialog()
    assert dlg.format_combo.currentData() == "openai" and dlg.api_combo.currentData() == "auto"
    assert "Chat Completions API" in dlg.api_status.text() and "公式以外の接続先" in dlg.api_status.text()
    assert not dlg.store_check.isChecked()
    texts = dlg.snapshot_texts()
    assert sum("会話のリセットの後に効く" in t for t in texts) >= 2  # 画像・上限の文
    assert any("会話の途中です" in t for t in texts) and any("会話の途中では変えられない" in t for t in texts)
    dlg.base_combo.setEditText("https://api.openai.com/v1")
    assert "Responses API" in dlg.api_status.text() and "公式の接続先なので" in dlg.api_status.text()
    dlg.api_combo.setCurrentIndex(dlg.api_combo.findData("chat"))
    assert "Chat Completions API" in dlg.api_status.text() and "なので" not in dlg.api_status.text()
    dlg.api_combo.setCurrentIndex(dlg.api_combo.findData("auto"))
    dlg.no_key_check.setChecked(False)
    dlg.key_edit.setText(key)
    assert all(key not in t for t in dlg.snapshot_texts())
    dlg._accept()
    assert page.apply_settings_dialog(dlg)  # 会話の形が変わるので、リセットを尋ねる(試験では「はい」)
    assert page.settings.provider_kind() == "openai-responses"
    assert wait_until(app, lambda: page.backend.conv.empty, 10)
    assert "Responses" in page.header.text()
    api = ScriptedApi("openai", [ScriptTurn(thinking="体積を測る", text="測ります", calls=[("measure", {})]),
                                 ScriptTurn(text="体積を測りました")])
    use_api(page, api)
    assert send_and_wait(app, page, "体積を測って") == "done"
    req = api.requests[0]
    assert req["path"] == "/v1/responses" and req["headers"]["authorization"] == f"Bearer {key}"
    assert req["json"]["store"] is False and all(t["strict"] is False for t in req["json"]["tools"])
    assert page.backend.conv.format == "openai-responses"
    sent = api.requests[1]["json"]["input"]
    assert [x.get("type") for x in sent][:4] == [None, "reasoning", "message", "function_call"]
    assert sent[-1]["type"] == "function_call_output" and sent[-1]["call_id"] == sent[3]["call_id"]
    assert page.chat.tool_cards()[-1].name == "measure" and page.chat.tool_cards()[-1].ok
    ini = Path(os.environ["GSIS_GUI_SETTINGS"]).read_text(encoding="utf-8")
    assert key not in ini and "store=false" in ini.lower()
    for p in (d / "ws").rglob("*"):
        if p.is_file():
            assert key.encode() not in p.read_bytes(), p


def test_pick_face_at_offscreen():
    """3D 表示のクリックの位置から面の番号を選ぶ処理(VTK のセルの picker。オフスクリーンで確かめる)。"""
    import numpy as np
    import pyvista as pv

    from gsis_tools.gui.pages.ai_page import pick_face_at

    box = pv.Box().triangulate()
    n = np.asarray(box.cell_normals)
    axis = np.argmax(np.abs(n), axis=1)
    fid = 1 + 2 * axis + (n[np.arange(len(n)), axis] > 0)  # -x:1 +x:2 -y:3 +y:4 -z:5 +z:6
    box.cell_data["face_id"] = fid.astype(np.int32)
    p = pv.Plotter(off_screen=True, window_size=(400, 300))
    try:
        p.add_mesh(box)
        p.add_mesh(pv.Line((1.2, 0, -2), (1.2, 0, 2)), pickable=False, line_width=8)  # 選べない線は無視する
        p.view_vector((1.0, 0.25, 0.15))
        p.render()

        def at(pt):
            r = p.renderer
            r.SetWorldPoint(*pt, 1.0)
            r.WorldToDisplay()
            x, y, _ = r.GetDisplayPoint()
            return x, y

        assert pick_face_at(p, *at((1.0, 0.3, 0.3))) == 2  # +x の面
        assert pick_face_at(p, *at((1.0, 0.0, 0.0))) == 2  # 線の上でも、その奥の面を選ぶ
        assert pick_face_at(p, 2, 2) is None  # 何もないところ
    finally:
        p.close()


def test_ai_text_is_not_html(gui):
    """AI の文章の中の生の HTML は使わない(文字として出す)。Markdown の書式は使う。ツール名も逃がす。"""
    app, ctx, win, page, kr, d = gui
    from gsis_tools.gui.widgets.chat_view import ToolCard, safe_markdown

    h = safe_markdown('**太字** <img src="file:///C:/x.png"> <b>html</b>')
    assert "<img" not in h and "&lt;img" in h and "&lt;b&gt;html&lt;/b&gt;" in h and "font-weight:700" in h
    card = ToolCard("x", "<i>bad</i>", {})
    assert "&lt;i&gt;" in card.title.text()


def test_dark_theme_restyle(gui):
    app, ctx, win, page, kr, d = gui
    ctx.theme.set_mode("dark")
    app.processEvents()
    assert page.view.scene() is not None
    ctx.theme.set_mode("light")
    app.processEvents()
