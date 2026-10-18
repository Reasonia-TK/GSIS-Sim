"""MCP サーバーを子プロセスとして起動し、stdio で initialize → tools/list → tools/call を通す試験。"""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("mcp")
pytest.importorskip("gmsh")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

EXPECTED_TOOLS = {
    "model_new", "model_info", "model_save", "model_load", "undo", "batch", "add_box", "add_cylinder", "add_sphere",
    "add_cone", "add_torus", "add_profile", "extrude", "revolve", "boolean", "transform", "remove", "import_cad",
    "measure", "list_faces", "check_geometry", "assign_patch", "remove_patch", "list_patches", "render",
    "mesh_settings", "mesh_refine", "mesh_generate", "mesh_quality", "export_pmesh", "export",
}


def _server_args(ws, *extra):
    return ["-m", "gsis_tools.mcp", "--workspace", str(ws), *extra]


def _text(res) -> dict:
    return json.loads(res.content[0].text)


async def _session_flow(ws):
    params = StdioServerParameters(command=sys.executable, args=_server_args(ws))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            assert init.server_info.name == "gsis-gmsh"
            assert "assign_patch" in (init.instructions or "")
            tools = (await s.list_tools()).tools
            names = {t.name for t in tools}
            assert names == EXPECTED_TOOLS  # run_python は既定で出さない
            ann = {t.name: t.annotations for t in tools}
            assert ann["measure"].read_only_hint and not ann["add_box"].read_only_hint
            schema = next(t for t in tools if t.name == "add_cylinder").input_schema
            assert set(schema["required"]) == {"base", "axis", "radius"}

            res = await s.call_tool("model_new", {"name": "tube", "unit": "mm"})
            assert not res.is_error
            res = await s.call_tool("batch", {"steps": [
                {"tool": "add_cylinder", "args": {"base": [0, 0, 0], "axis": [8, 0, 0], "radius": 8, "name": "up"}},
                {"tool": "add_cylinder", "args": {"base": [8, 0, 0], "axis": [10, 0, 0], "radius": 1}},
                {"tool": "add_cylinder", "args": {"base": [18, 0, 0], "axis": [4, 0, 0], "radius": 4}},
                {"tool": "boolean", "args": {"op": "fuse", "objects": ["up"], "tools": ["cylinder1", "cylinder2"]}},
            ]})
            assert not res.is_error, res.content[0].text
            m = _text(await s.call_tool("measure", {"expected_volume": 586 * math.pi}))
            assert abs(m["rel_error"]) < 1e-9

            # 失敗した batch は元に戻り、番号と理由を返す
            res = await s.call_tool("batch", {"steps": [
                {"tool": "add_box", "args": {"corner": [30, 0, 0], "size": [1, 1, 1]}},
                {"tool": "boolean", "args": {"op": "cut", "objects": ["up"], "tools": ["no_such_name"]}},
            ]})
            assert res.is_error
            err = _text(res)
            assert err["failed"]["index"] == 1 and err["rolled_back"] is True
            assert len(_text(await s.call_tool("model_info", {}))["volumes"]) == 1

            for name, sel in (("inlet", {"on_plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}}),
                              ("outlet", {"on_plane": {"point": [22, 0, 0], "normal": [1, 0, 0]}}),
                              ("wall", {"unassigned": True})):
                assert not (await s.call_tool("assign_patch", {"name": name, "select": sel})).is_error
            assert _text(await s.call_tool("list_patches", {}))["complete"]

            # STEP の書き出し(OpenCASCADE が標準出力に書いても、通信路は壊れない)
            res = await s.call_tool("export", {"path": "cad/tube.step"})
            assert not res.is_error and (ws / "cad" / "tube.step").is_file()
            res = await s.call_tool("render", {"inline": True, "width": 300, "height": 240})
            assert not res.is_error and [c.type for c in res.content] == ["text", "image"]
            assert (ws / _text(res)["path"]).is_file()

            await s.call_tool("mesh_settings", {"size_max": 1.5})
            g = _text(await s.call_tool("mesh_generate", {}))
            assert g["tetrahedra"] > 1000
            e = _text(await s.call_tool("export_pmesh", {"path": "tube.pmesh"}))
            assert e["check"] == "OK" and (ws / "tube.pmesh").is_file()

            # 作業フォルダの外・未知のツール・引数の誤り・無効な run_python
            res = await s.call_tool("export", {"path": "../outside.step"})
            assert res.is_error and "'..'" in _text(res)["error"]
            res = await s.call_tool("no_such_tool", {})
            assert res.is_error
            res = await s.call_tool("add_box", {"corner": [0, 0], "size": [1, 1, 1]})
            assert res.is_error and "corner" in _text(res)["error"]
            res = await s.call_tool("run_python", {"code": "print(1)"})
            assert res.is_error and "--allow-python" in _text(res)["error"]
    return True


def test_stdio_session(tmp_path):
    ws = tmp_path / "ws"
    assert asyncio.run(_session_flow(ws))
    assert not (tmp_path / "outside.step").exists()


async def _allow_python(ws):
    params = StdioServerParameters(command=sys.executable, args=_server_args(ws, "--allow-python"))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert "run_python" in names
            res = await s.call_tool("run_python", {"code": "occ.addBox(0, 0, 0, 1, 1, 2)\nprint('done')"})
            assert not res.is_error and _text(res)["stdout"] == "done\n"
            m = _text(await s.call_tool("measure", {}))
            assert m["total_volume"] == pytest.approx(2.0)


def test_stdio_allow_python(tmp_path):
    asyncio.run(_allow_python(tmp_path / "ws"))


def test_stdio_raw_jsonrpc_lines(tmp_path):
    """生の JSON-RPC(1 行 1 メッセージ)で、標準出力のすべての行が JSON であること。

    STEP の書き出しと読み込みでは OpenCASCADE が C の標準出力に統計を書く。それが通信路に混ざらないこと。
    """
    p = subprocess.Popen([sys.executable, *_server_args(tmp_path / "ws")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines: list[bytes] = []
    errs: list[bytes] = []
    threading.Thread(target=lambda: lines.extend(iter(p.stdout.readline, b"")), daemon=True).start()
    threading.Thread(target=lambda: errs.extend(iter(p.stderr.readline, b"")), daemon=True).start()

    def send(obj):
        p.stdin.write((json.dumps(obj) + "\n").encode())
        p.stdin.flush()

    def wait(n, timeout=60.0):
        t0 = time.time()
        while len(lines) < n and time.time() - t0 < timeout:
            time.sleep(0.02)
        assert len(lines) >= n, (lines, errs)

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "raw", "version": "0"}}})
        wait(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        calls = [
            ("add_sphere", {"center": [0, 0, 0], "radius": 1}),
            ("export", {"path": "raw.step"}),
            ("import_cad", {"path": "raw.step"}),
            ("measure", {}),
        ]
        for i, (name, args) in enumerate(calls, start=2):
            send({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
            wait(i)
        send({"jsonrpc": "2.0", "id": 99, "method": "tools/list"})
        wait(len(calls) + 2)
    finally:
        p.stdin.close()
        p.wait(timeout=60)
    msgs = [json.loads(line) for line in lines]  # すべての行が JSON(そうでなければここで失敗)
    assert [m.get("id") for m in msgs] == [1, 2, 3, 4, 5, 99]
    assert all(not m["result"].get("isError") for m in msgs[1:5])
    measured = json.loads(msgs[4]["result"]["content"][0]["text"])
    assert measured["total_volume"] == pytest.approx(2 * 4 / 3 * math.pi, rel=1e-6)
    assert all(line.endswith(b"\n") and not line.endswith(b"\r\n") for line in lines)
    assert p.returncode == 0
