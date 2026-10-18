# gsis-tools

GSIS-PBC の前処理・後処理・検証ツール(メッシュの生成・変換・検査など)、GUI(試作)、AI モデリング用の MCP サーバー。
使い方は `docs/python_tools.md`(ツール)、`docs/gui.md`(GUI)、`docs/mcp.md`(MCP サーバー)を参照。

    uv run --project python gsis-tools --help
    uv run --project python --extra gui gsis-gui
    uv run --project python --extra mcp gsis-mcp --workspace mcp_workspace   # MCP サーバー(stdio)
    uv run --project python pytest python/tests                # extra gui・mcp がなければ、それぞれの試験の多くは飛ばす
    uv run --project python --extra gui pytest python/tests    # GUI の試験も(QT_QPA_PLATFORM=offscreen で)
    uv run --project python --extra mcp pytest python/tests    # MCP の試験も
