"""GUI の AI モデリング(Qt なし)。

GUI の「モデリング(AI)」の画面の裏側。AI(LLM)が gmsh の MCP ツールで計算領域を作る反復を動かす。

- :mod:`.settings` … 設定(形式・接続先・モデル・上限など。API キーは入れない)
- :mod:`.secrets` … API キーの読み込み(環境変数・OS の資格情報マネージャー・その場の入力)と伏せ字
- :mod:`.types` … 形式によらない型(ツールの定義・呼び出し・結果・トークン数・API のエラー)
- :mod:`.providers` … Anthropic 形式(Messages API)と OpenAI 形式(Responses API・Chat Completions API)の違いを閉じ込める層
- :mod:`.mcp_client` … MCP サーバー(``gsis-mcp``)を子プロセスで起動してつなぐ
- :mod:`.session` … MCP サーバーとモデルの状態(手順ごとの自動保存、落ちたときの起動し直しと読み込み)
- :mod:`.agent` … AI の反復(ツール呼び出しの上限、停止、エラーの扱い)
- :mod:`.conversation` … 会話(形式ごとの履歴と表示用の記録)の保存と読み込み
- :mod:`.prompt` … AI への指示(システムプロンプト)
- :mod:`.backend` … 上をまとめて別スレッドの asyncio で動かす(GUI のスレッドを止めない)
- :mod:`.surface` … GUI 専用のツール ``gui_surface`` の応答(3D 表示の三角形)の読み込み
- :mod:`.demo` … 模擬の AI(試験とスクリーンショット用。決まった応答を返す HTTP の模擬)

画面は :mod:`gsis_tools.gui.pages.ai_page`。使い方は ``docs/gui.md``。
"""

from __future__ import annotations
