"""モデリング(AI)の画面のスクリーンショットを作る(docs/gui_screenshots/24〜31)。

    uv run --project python --extra gui python -m gsis_tools.gui.screenshots_ai [--out DIR] [--interactive]

模擬の AI(:mod:`gsis_tools.gui.ai.demo`。HTTP の層の模擬で、実際の API は呼ばない)が、本物の MCP サーバーの
ツールで管(論文 §4.1)の計算領域を作る様子を、2 回の指示(形状とパッチ → メッシュと書き出し)で撮る。
API キーはダミー(設定の画面では伏せ字)。設定と作業フォルダは一時フォルダ(利用者の設定を変えない)。
設定の画面は、Anthropic 形式の既定(Claude Opus 5.5)、OpenAI 形式の API の種類(自動なら公式の接続先は Responses)、
OpenAI の応答の欄(store など)、作業の欄(会話のリセットの後に効く項目の注記)を撮る。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

DUMMY_KEY = "sk-ant-api03-SCREENSHOT-DUMMY-KEY-0000000000"
DUMMY_OPENAI_KEY = "sk-proj-SCREENSHOT-DUMMY-KEY-00000000000000"


class _MemoryKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, s, u):
        return self.data.get((s, u))

    def set_password(self, s, u, p):
        self.data[(s, u)] = p

    def delete_password(self, s, u):
        self.data.pop((s, u), None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="モデリング(AI)の画面のスクリーンショットを作る")
    ap.add_argument("--out", default=None, help="書き出すフォルダ(既定 docs/gui_screenshots)")
    ap.add_argument("--interactive", action="store_true", help="通常の表示で窓を出して撮る")
    ap.add_argument("--size", default="1600x980", help="窓の大きさ(既定 1600x980)")
    args = ap.parse_args(argv)
    if not args.interactive:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["GSIS_GUI_3D"] = "static"
    tmp = Path(tempfile.mkdtemp(prefix="gsis_gui_ai_shots_"))
    os.environ["GSIS_GUI_SETTINGS"] = str(tmp / "settings.ini")
    os.environ["GSIS_GUI_AI_WORKSPACE"] = str(tmp / "ai_workspace")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

    from .app import create_app

    app, ThemeManager, AppContext = create_app([sys.argv[0]])
    from PySide6.QtWidgets import QMessageBox

    from .ai.demo import TUBE_REQUEST_MESH, TUBE_REQUEST_SHAPE, ScriptedApi, tube_script_two_steps
    from .ai.secrets import KeyResolver, KeyStore, Secret
    from .main_window import MainWindow
    from .paths import repo_root

    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    out = Path(args.out) if args.out else repo_root() / "docs" / "gui_screenshots"
    out.mkdir(parents=True, exist_ok=True)
    w, h = (int(v) for v in args.size.lower().split("x"))

    tm = ThemeManager(app, mode="light")
    ctx = AppContext(tm)
    tm.apply()
    win = MainWindow(ctx)
    tm.apply()
    win.resize(w, h)
    win.move(40, 40)
    page = win.ai_page
    resolver = KeyResolver(KeyStore(_MemoryKeyring()), environ={})
    resolver.set_session("anthropic", page.settings.base_url, Secret(DUMMY_KEY))
    page.set_resolver(resolver)
    part1, part2 = tube_script_two_steps(fine=True)
    api = ScriptedApi("anthropic", part1 + part2)
    page.http_client_factory = api.client_factory
    win.show()

    def pump(sec: float = 0.3) -> None:
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.01)

    def wait(cond, timeout: float = 300.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.03)
        return False

    shots: list[str] = []

    def shot(name: str, widget=None) -> None:
        pump(0.7)
        path = out / f"{name}.png"
        (widget or win).grab().save(str(path))
        shots.append(path.name)
        print(f"書き出し: {path}")

    def send(text: str) -> None:
        n = page.done_count
        page.send_text(text)
        if not wait(lambda: page.done_count > n and not page.busy, 300):
            print("待ちきれませんでした", file=sys.stderr)

    win.show_page("ai")
    wait(lambda: page.server_state == "ready", 120)
    # 1 回目: 形状とパッチ(面の番号を出し、batch の記録を開く)
    send(TUBE_REQUEST_SHAPE)
    page.labels_check.setChecked(True)
    cards = page.chat.tool_cards()
    if len(cards) > 1:
        cards[1].set_open(True)
    page.tabs.setCurrentIndex(0)
    shot("24_ai_shape_patches")
    if len(cards) > 1:
        cards[1].set_open(False)
    page.labels_check.setChecked(False)
    # 2 回目: メッシュと書き出し
    send(TUBE_REQUEST_MESH)
    pump(0.5)
    page.chat._to_bottom()
    shot("25_ai_mesh_export")
    # 断面と面の選択(入口の面を選ぶ)。メッシュの辺は消して見やすくする
    page.edges_check.setChecked(False)
    page.section_combo.setCurrentIndex(page.section_combo.findData("y"))
    if page.surface is not None:
        inlet = next((p["faces"] for p in page.surface.patches if p["name"] == "inlet"), [])
        if inlet:
            page.pick_face(inlet[0])
    page.tabs.setCurrentIndex(1)
    shot("26_ai_section_faces")
    page.edges_check.setChecked(True)
    page.section_combo.setCurrentIndex(0)
    page.select_faces([])
    page.tabs.setCurrentIndex(0)
    # 設定の画面(キーは伏せ字): Anthropic 形式の既定(Claude Opus 5.5)
    dlg = page.make_settings_dialog()
    dlg.key_edit.setText(DUMMY_KEY)
    dlg.resize(820, 700)
    dlg.show()
    pump(0.5)
    shot("27_ai_settings", dlg)
    # OpenAI 形式: API の種類(自動なら公式の接続先は Responses)、応答の欄(store など)、作業の欄(注記)
    dlg.key_edit.clear()
    dlg.format_combo.setCurrentIndex(dlg.format_combo.findData("openai"))
    dlg.key_edit.setText(DUMMY_OPENAI_KEY)
    pump(0.3)
    shot("29_ai_settings_openai", dlg)
    dlg.tabs.setCurrentIndex(1)
    shot("30_ai_settings_openai_reply", dlg)
    dlg.tabs.setCurrentIndex(2)
    shot("31_ai_settings_work", dlg)
    texts = "\n".join(dlg.snapshot_texts())
    if DUMMY_KEY in texts or DUMMY_OPENAI_KEY in texts:  # pragma: no cover - 伏せ字の確かめ
        raise SystemExit("設定の画面にキーが出ています")
    dlg.reject()
    # ダーク
    win.mode_combo.setCurrentIndex(win.mode_combo.findData("dark"))
    pump(0.6)
    shot("28_dark_ai")
    win.mode_combo.setCurrentIndex(win.mode_combo.findData("light"))
    pump(0.3)

    win.close()
    app.processEvents()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(shots)} 枚: {', '.join(shots)}(模擬の AI へのリクエスト {len(api.requests)} 回)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
