"""MCP サーバーの作業フォルダの制限(書き出し先・読み込み元)。"""

from __future__ import annotations

import os

import pytest

from gsis_tools.mcp.workspace import PathRejected, Workspace


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "extra").mkdir()
    return Workspace(tmp_path / "ws", read_dirs=[tmp_path / "extra"])


def test_out_path_inside(ws):
    p = ws.out_path("a/b/c.pmesh", (".pmesh",))
    assert p == ws.root / "a" / "b" / "c.pmesh"
    assert p.parent.is_dir()
    q = ws.out_path(str(ws.root / "x.png"), (".png",))
    assert q == ws.root / "x.png"
    assert ws.relative(p) == "a/b/c.pmesh"


@pytest.mark.parametrize(
    "bad",
    [
        "../x.png",
        "a/../../x.png",
        "a/../x.png",  # 解決後に中でも '..' は拒否する
        "",
        "  ",
        "a\x00b.png",
        "a/x.png:stream",
        "con.png",
        "NUL",
        "a/COM1.txt.png",
        "a./x.png",
        "a /x.png",
        "PROGRA~1/x.png",
        "a/b?.png",
        ".gsis_mcp/x.png",
    ],
)
def test_out_path_rejected(ws, bad):
    with pytest.raises(PathRejected):
        ws.out_path(bad, (".png",) if bad.endswith(".png") else None)


def test_out_path_absolute_outside(ws, tmp_path):
    with pytest.raises(PathRejected):
        ws.out_path(str(tmp_path / "outside.png"))
    with pytest.raises(PathRejected):
        ws.out_path(str(ws.root))  # 作業フォルダそのもの


@pytest.mark.skipif(os.name != "nt", reason="Windows のパス")
def test_windows_specific(ws):
    for bad in (r"\\server\share\x.png", r"\\?\C:\x.png", r"\\.\PhysicalDrive0", "C:x.png", r"D:\x.png", r"\x.png"):
        with pytest.raises(PathRejected):
            ws.out_path(bad)


def test_suffix(ws):
    with pytest.raises(PathRejected, match="拡張子"):
        ws.out_path("x.exe", (".pmesh",))
    assert ws.out_path("X.PMESH", (".pmesh",)).name == "X.PMESH"


def test_in_path(ws, tmp_path):
    f = ws.root / "m.step"
    f.write_text("x")
    assert ws.in_path("m.step", (".step",)) == f
    with pytest.raises(PathRejected, match="ファイルがありません"):
        ws.in_path("missing.step", (".step",))
    outside = tmp_path / "o.step"
    outside.write_text("x")
    with pytest.raises(PathRejected, match="読み込める"):
        ws.in_path(str(outside), (".step",))
    extra = tmp_path / "extra" / "e.step"
    extra.write_text("x")
    assert ws.in_path(str(extra), (".step",)) == extra.resolve()
    # 内部用のフォルダは読める(取り込んだ CAD の写し)
    (ws.internal / "imports").mkdir(parents=True, exist_ok=True)
    g = ws.internal / "imports" / "a.step"
    g.write_text("x")
    assert ws.in_path(".gsis_mcp/imports/a.step", (".step",)) == g


@pytest.mark.skipif(os.name != "nt", reason="Windows のジャンクション")
def test_junction_escape(ws, tmp_path):
    import _winapi

    target = tmp_path / "elsewhere_j"
    target.mkdir()
    link = ws.root / "junction"
    _winapi.CreateJunction(str(target), str(link))
    with pytest.raises(PathRejected):
        ws.out_path("junction/x.png")
    with pytest.raises(PathRejected):
        ws.out_path("junction/sub/x.png")
    assert not (target / "sub").exists()  # 外にフォルダを作っていない


def test_symlink_escape(ws, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = ws.root / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("シンボリックリンクを作れない環境")
    with pytest.raises(PathRejected):
        ws.out_path("link/x.png")
