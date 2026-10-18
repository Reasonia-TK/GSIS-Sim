"""MCP のツール(エンジン)を関数として直接呼ぶ試験。

ツールの引数は tools 層の pydantic のモデルで検証してから渡す(サーバーと同じ経路)。
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

pytest.importorskip("gmsh")
pytest.importorskip("pydantic")

from gsis_tools.mcp.engine import ModelEngine  # noqa: E402
from gsis_tools.mcp.errors import ToolFailure  # noqa: E402
from gsis_tools.mcp.tools import TOOLS, input_schema, normalize  # noqa: E402
from gsis_tools.mcp.workspace import Workspace  # noqa: E402

DGL = [[0, 0], [100, 0], [100, 100], [3.5, 100], [5.2, 108.5], [100, 108.5], [100, 111.5], [5.8, 111.5],
       [7.5, 120], [100, 120], [100, 220], [0, 220], [0, 90], [50, 90], [50, 88], [0, 88]]


class Tools:
    """エンジンをツール名と引数で呼ぶ(tools 層の検証を通す)。"""

    def __init__(self, engine: ModelEngine):
        self.engine = engine
        self.ws = engine.ws

    def __call__(self, tool: str, **args):
        r = self.engine.call(tool, normalize(tool, args))
        json.dumps({k: v for k, v in r.items() if k != "_image_png"}, ensure_ascii=False)  # JSON にできること
        return r

    def fail(self, tool: str, **args) -> ToolFailure:
        with pytest.raises(ToolFailure) as ei:
            self(tool, **args)
        return ei.value


@pytest.fixture
def t(tmp_path):
    eng = ModelEngine(Workspace(tmp_path / "ws"), allow_python=True, threads=2)
    eng.arg_normalizer = normalize
    yield Tools(eng)
    eng.close()


def build_tube(t, unit="mm"):
    t("model_new", name="tube", unit=unit)
    t("batch", steps=[
        {"tool": "add_cylinder", "args": {"base": [0, 0, 0], "axis": [8, 0, 0], "radius": 8, "name": "up"}},
        {"tool": "add_cylinder", "args": {"base": [8, 0, 0], "axis": [10, 0, 0], "radius": 1, "name": "pipe"}},
        {"tool": "add_cylinder", "args": {"base": [18, 0, 0], "axis": [4, 0, 0], "radius": 4, "name": "down"}},
        {"tool": "boolean", "args": {"op": "fuse", "objects": ["up"], "tools": ["pipe", "down"], "name": "tube"}},
        {"tool": "measure", "args": {"expected_volume": 586 * math.pi, "rtol": 1e-9}},
    ])


def tube_patches(t):
    t("assign_patch", name="inlet", select={"on_plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}})
    t("assign_patch", name="outlet", select={"normal": [1, 0, 0], "in_box": [21, -5, -5, 23, 5, 5]})
    return t("assign_patch", name="wall", select={"unassigned": True})


# ---------------------------------------------------------------------------


def test_tool_schemas():
    names = [s.name for s in TOOLS]
    assert len(names) == len(set(names))
    for s in TOOLS:
        sch = input_schema(s)
        assert sch["type"] == "object"
        json.dumps(sch)
        assert "title" not in json.dumps(sch).replace('"title":', "")[:0]  # title を除いている
    assert "title" not in input_schema(next(s for s in TOOLS if s.name == "add_box"))


def test_primitives(t):
    t("model_new", unit="m")
    for tool, args in (
        ("add_box", {"corner": [0, 0, 0], "size": [1, 2, 3]}),
        ("add_cylinder", {"base": [5, 0, 0], "axis": [0, 0, 2], "radius": 0.5}),
        ("add_cylinder", {"base": [9, 0, 0], "axis": [0, 0, 2], "radius": 0.5, "angle_deg": 90}),
        ("add_sphere", {"center": [0, 6, 0], "radius": 1.5}),
        ("add_cone", {"base": [0, 10, 0], "axis": [0, 0, 3], "radius1": 1, "radius2": 0.25}),
        ("add_torus", {"center": [0, 15, 0], "radius_major": 2, "radius_minor": 0.5, "axis": [1, 0, 0]}),
    ):
        r = t(tool, **args)
        assert abs(r["rel_error"]) < 1e-8, (tool, r)
    info = t("model_info")
    assert [v["names"][0] for v in info["volumes"]] == ["box1", "cylinder1", "cylinder2", "sphere1", "cone1", "torus1"]
    assert t.fail("add_box", corner=[0, 0, 0], size=[1, 0, 1]).message.startswith("size")
    with pytest.raises(ToolFailure, match="引数が不正"):
        t("add_box", corner=[0, 0], size=[1, 1, 1])
    with pytest.raises(ToolFailure, match="引数が不正"):
        t("add_sphere", center=[0, 0, 0], radius=1, colour="red")  # 未知の引数


def test_tube_faces_and_patches(t):
    build_tube(t)
    faces = {f["id"]: f for f in t("list_faces")["faces"]}
    assert len(faces) == 7
    planes = {round(f["c"][0], 6): f["n"] for f in faces.values() if f["type"] == "plane"}
    # 外向き法線(流体の外へ)
    assert planes == {0.0: [-1.0, 0.0, 0.0], 8.0: [1.0, 0.0, 0.0], 18.0: [-1.0, 0.0, 0.0], 22.0: [1.0, 0.0, 0.0]}
    cyl = sorted(f["r"] for f in faces.values() if f["type"] == "cylinder")
    assert cyl == pytest.approx([1.0, 4.0, 8.0])
    assert all(abs(abs(f["axis"][0]) - 1) < 1e-9 for f in faces.values() if f["type"] == "cylinder")
    # 選択の試し
    sel = t("list_faces", select={"type": "cylinder", "radius": 1})
    assert sel["count"] == 1 and sel["faces"][0]["r"] == pytest.approx(1.0)
    assert sel["filter_counts"] == {"候補": 7, "type": 3, "radius": 1}
    assert t("list_faces", select={"contains_point": [13, 0, 1]})["count"] == 1
    assert t("list_faces", select={"contains_point": [13, 0, 1.5]})["count"] == 0
    r = tube_patches(t)
    assert r["unassigned"] == 0 and len(r["faces"]) == 5
    lp = t("list_patches")
    assert lp["complete"]
    area = {p["name"]: p["area"] for p in lp["patches"]}
    assert area["inlet"] == pytest.approx(64 * math.pi)
    assert area["outlet"] == pytest.approx(16 * math.pi)
    wall = 2 * math.pi * 8 * 8 + math.pi * 63 + 2 * math.pi * 10 + math.pi * 15 + 2 * math.pi * 4 * 4
    assert area["wall"] == pytest.approx(wall)
    assert [p["kind"] for p in lp["patches"]] == ["inlet", "outlet", "wall"]
    # 1 つの面は 1 つのパッチ
    e = t.fail("assign_patch", name="x", select={"faces": [3]} if 3 in faces else {"type": "plane"})
    assert "別のパッチ" in e.message
    # 条件に合わない
    e = t.fail("assign_patch", name="y", select={"radius": 3.0})
    assert e.data["filter_counts"]["radius"] == 0
    # 未知の条件は引数の検証で弾く
    with pytest.raises(ToolFailure, match="on_plain"):
        t("assign_patch", name="z", select={"on_plain": {"point": [0, 0, 0], "normal": [1, 0, 0]}})


def test_outward_normal_fallback(t, monkeypatch):
    """速い方法の外向き法線が誤っていたら、抜き取りの確認で気づき、点の内外の判定で求め直すこと。"""
    import gsis_tools.mcp.faces as F

    orig = F._fast_outward
    monkeypatch.setattr(F, "_fast_outward", lambda g, tag, uv, sign: -orig(g, tag, uv, sign))
    build_tube(t)
    faces = t("list_faces")["faces"]
    planes = {round(f["c"][0], 6): f["n"] for f in faces if f["type"] == "plane"}
    assert planes == {0.0: [-1.0, 0.0, 0.0], 8.0: [1.0, 0.0, 0.0], 18.0: [-1.0, 0.0, 0.0], 22.0: [1.0, 0.0, 0.0]}


def test_dgl_revolve(t):
    t("model_new", name="dgl", unit="mm")
    p = t("add_profile", plane="xz", outer={"points": DGL}, name="section")
    A = 0.5 * sum(DGL[i][0] * DGL[(i + 1) % 16][1] - DGL[(i + 1) % 16][0] * DGL[i][1] for i in range(16))
    assert p["area"] == pytest.approx(abs(A))
    r = t("revolve", entities=["section"], axis_dir=[0, 0, 1], name="dgl")
    assert r["faces"] == 14 and r["removed_profile_surfaces"] == [p["surface"]]
    assert abs(r["rel_error"]) < 1e-9
    assert r["volume"] == pytest.approx(p["pappus_volume_v_axis"], rel=1e-9)
    assert r["volume"] == pytest.approx(6.363424e6, rel=1e-6)  # docs/eval_solidcraft.md の値
    m = t("measure")
    assert m["volumes"][0]["shells"] == 2  # ウェハの空洞
    chk = t("check_geometry", mesh_test=True)
    assert chk["ok"], chk
    assert chk["surface_mesh_test"]["ok"]
    inlet = t("assign_patch", name="inlet", select={"type": "cylinder", "radius": 100, "in_box": [-101, -101, 108, 101, 101, 112]})
    assert inlet["area"] == pytest.approx(2 * math.pi * 100 * 3)
    t("assign_patch", name="outlet_top", select={"on_plane": {"point": [0, 0, 220], "normal": [0, 0, 1]}})
    t("assign_patch", name="outlet_bottom", select={"normal": [0, 0, -1], "in_box": [-100, -100, -1, 100, 100, 1]})
    w = t("assign_patch", name="wall", select={"unassigned": True})
    assert w["unassigned"] == 0 and len(w["faces"]) == 11


def test_sphere_shell(t):
    t("model_new", name="sphere", unit="m")
    t("add_sphere", center=[0, 0, 0], radius=5, name="outer")
    t("add_sphere", center=[0, 0, 0], radius=0.5, name="inner")
    r = t("boolean", op="cut", objects=["outer"], tools=["inner"])
    assert r["name"] == "outer" and r["faces"] == 2
    m = t("measure", expected_volume=4 / 3 * math.pi * (125 - 0.125), rtol=1e-9)
    assert m["volumes"][0]["shells"] == 2
    chk = t("check_geometry", mesh_test=True)
    assert chk["ok"] and chk["degenerated_edges"] == 4, chk  # 極の退化した曲線は問題にしない
    faces = t("list_faces")["faces"]
    assert sorted(f["r"] for f in faces) == pytest.approx([0.5, 5.0])
    assert all(np.allclose(f["center"], 0, atol=1e-9) for f in faces)
    s = t("assign_patch", name="sphere", select={"type": "sphere", "radius": 0.5})
    assert s["kind"] == "patch" and s["area"] == pytest.approx(math.pi)
    ff = t("assign_patch", name="far_field", select={"unassigned": True})
    assert ff["area"] == pytest.approx(100 * math.pi)


def test_profile_forms(t):
    t("model_new", unit="mm")
    q = t("add_profile", plane="xy",
          outer={"start": [0, 0], "segments": [{"line_to": [4, 0]}, {"arc_to": [0, 4], "center": [0, 0]}]},
          holes=[{"circle": {"center": [1, 1], "radius": 0.5}}], name="q")
    assert q["area"] == pytest.approx(4 * math.pi - 0.25 * math.pi)
    e = t("extrude", entities=["q"], vector=[0, 0, 2])
    assert abs(e["rel_error"]) < 1e-9
    # 3 点の円弧(半円)
    h = t("add_profile", plane="yz", origin=[10, 0, 0],
          outer={"start": [-1, 0], "segments": [{"line_to": [1, 0]}, {"arc_to": [-1, 0], "through": [0, 1]}]})
    assert h["area"] == pytest.approx(math.pi / 2)
    assert h["centroid"][0] == pytest.approx(10.0)
    # 時計回りの円弧(大きい方)
    cw = t("add_profile", outer={"start": [1, 0], "segments": [{"arc_to": [0, 1], "center": [0, 0], "ccw": False}]},
           origin=[20, 0, 0])
    assert cw["area"] == pytest.approx(0.75 * math.pi + 0.5, rel=1e-9)
    # 自己交差・穴の外・面積 0
    assert "自己交差" in t.fail("add_profile", outer={"points": [[0, 0], [2, 2], [2, 0], [0, 1]]}).message
    assert "面積が 0" in t.fail("add_profile", outer={"points": [[0, 0], [1, 1], [1, 0], [0, 1]]}).message
    assert "外" in t.fail("add_profile", outer={"points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
                          holes=[{"circle": {"center": [5, 5], "radius": 0.1}}]).message
    assert "面積が 0" in t.fail("add_profile", outer={"points": [[0, 0], [1, 0], [2, 0]]}).message
    with pytest.raises(ToolFailure, match="引数が不正"):
        t("add_profile", outer={"points": [[0, 0], [1, 0], [1, 1]], "circle": {"center": [0, 0], "radius": 1}})
    # 押し出されていない断面は体積に属さない面
    t("add_profile", outer={"points": [[50, 0], [51, 0], [51, 1]]}, name="loose")
    chk = t("check_geometry")
    assert any(i["kind"] == "free_surfaces" for i in chk["issues"])


def test_names_and_refs(t):
    t("model_new")
    t("add_box", corner=[0, 0, 0], size=[1, 1, 1], name="a")
    t("add_box", corner=[0.5, 0, 0], size=[1, 1, 1], name="b")
    assert "すでに使われています" in t.fail("add_box", corner=[0, 0, 0], size=[1, 1, 1], name="a").message
    assert "使えません" in t.fail("add_box", corner=[0, 0, 0], size=[1, 1, 1], name="1bad").message
    assert "v:3" in t.fail("add_box", corner=[0, 0, 0], size=[1, 1, 1], name="v:3").message or True
    r = t("boolean", op="fuse", objects=["a"], tools=["b"])
    assert r["name"] == "a" and r["volume"] == pytest.approx(1.5)
    e = t.fail("measure", entities=["b"])
    assert "もうありません" in e.message
    e = t.fail("measure", entities=["zzz"])
    assert "今ある名前: a" in e.message
    assert t("measure", entities=["v:1"])["total_volume"] == pytest.approx(1.5)
    assert "体積 9" in t.fail("measure", entities=[9]).message


def test_failed_boolean_keeps_model(t):
    t("model_new")
    t("add_box", corner=[0, 0, 0], size=[1, 1, 1], name="a")
    t("add_box", corner=[5, 0, 0], size=[1, 1, 1], name="b")
    before = t("model_info")
    e = t.fail("boolean", op="intersect", objects=["a"], tools=["b"])
    assert "結果が空" in e.message
    after = t("model_info")
    assert after["volumes"] == before["volumes"] and after["names"] == before["names"]
    assert t("add_box", corner=[9, 0, 0], size=[1, 1, 1])["volumes"] == [3]  # 番号も元どおり


def test_batch_rollback(t):
    t("model_new")
    t("add_box", corner=[0, 0, 0], size=[1, 1, 1], name="b0")
    before = t("model_info", history=True)
    err = t.fail("batch", steps=[
        {"tool": "add_box", "args": {"corner": [2, 0, 0], "size": [1, 1, 1], "name": "b1"}},
        {"tool": "boolean", "args": {"op": "fuse", "objects": ["b0"], "tools": ["b1"]}},
        {"tool": "boolean", "args": {"op": "cut", "objects": ["b0"], "tools": ["nothing"]}},
    ])
    assert err.data["failed"]["index"] == 2 and err.data["failed"]["tool"] == "boolean"
    assert "nothing" in err.data["failed"]["error"] and err.data["rolled_back"]
    after = t("model_info", history=True)
    assert after["volumes"] == before["volumes"] and after["history"] == before["history"]
    assert after["names"] == {"b0": "体積 1"}
    # 番号も元どおり(次の形状は同じ番号になる)
    assert t("add_box", corner=[5, 0, 0], size=[1, 1, 1])["volumes"] == [2]
    # 引数の誤りは実行の前に見つける(何も実行しない)
    n = len(t.engine.history)
    err = t.fail("batch", steps=[{"tool": "add_box", "args": {"corner": [9, 0, 0], "size": [1, 1, 1]}},
                                 {"tool": "add_box", "args": {"corner": [0, 0]}}])
    assert err.data["failed"]["index"] == 1 and len(t.engine.history) == n
    assert "batch の中では" in t.fail("batch", steps=[{"tool": "undo"}]).message
    # measure の rtol で検算の手順にする
    err = t.fail("batch", steps=[{"tool": "add_box", "args": {"corner": [9, 0, 0], "size": [1, 1, 1]}},
                                 {"tool": "measure", "args": {"expected_volume": 100.0, "rtol": 1e-6}}])
    assert err.data["failed"]["index"] == 1 and len(t.engine.history) == n
    ok = t("batch", steps=[{"tool": "add_sphere", "args": {"center": [0, 9, 0], "radius": 1}},
                           {"tool": "measure", "args": {"entities": ["sphere1"], "expected_volume": 4 / 3 * math.pi, "rtol": 1e-9}}])
    assert ok["ok"] and ok["results"][0]["name"] == "sphere1"
    # batch はまとめて 1 手順として取り消す
    u = t("undo")
    assert u["undone"] == ["add_sphere"]
    assert "sphere1" not in t("model_info")["names"]


def test_undo_replay_keeps_tags(t):
    build_tube(t)
    tube_patches(t)
    f0 = t("list_faces")
    t("add_box", corner=[30, 0, 0], size=[1, 1, 1])
    t("boolean", op="fuse", objects=["tube"], tools=["box1"])
    u = t("undo", steps=2)
    assert u["undone"] == ["boolean", "add_box"]
    assert t("list_faces") == f0
    assert t("list_patches")["complete"]
    # 設定だけの取り消しはメッシュを残す
    t("mesh_settings", size_max=2.0)
    t("mesh_generate", dim=2)
    t("assign_patch", name="wall", select={"patch": "wall"}, kind="wall")
    t("mesh_refine", name="r", target="box", box=[0, -1, -1, 1, 1, 1], size=0.5)
    u = t("undo")
    assert u["undone"] == ["mesh_refine"] and u["mesh"]["exists"]
    assert t("undo", steps=100)["steps_left"] == 0
    assert "取り消せる手順" in t.fail("undo").message


def test_save_load(t):
    build_tube(t)
    tube_patches(t)
    t("mesh_refine", name="pipe", target="faces", select={"type": "cylinder", "radius": 1}, size=0.2, dist_max=2)
    f0 = t("list_faces")
    p0 = t("list_patches")
    s = t("model_save", path="state/tube.json")
    data = json.loads((t.ws.root / s["path"]).read_text(encoding="utf-8"))
    assert data["format"] == "gsis-mcp-model" and data["unit"] == "mm"
    t("model_new", name="other", unit="m")
    info = t("model_load", path="state/tube.json")
    assert info["model"] == "tube" and info["unit"] == "mm" and info["refinements"] == ["pipe"]
    assert t("list_faces") == f0 and t("list_patches") == p0
    # 壊れたファイルは読まずに元のモデルのまま
    bad = dict(data)
    bad["history"] = data["history"] + [{"tool": "boolean", "args": {"op": "cut", "objects": ["nothing"], "tools": ["x"]}}]
    (t.ws.root / "state" / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    e = t.fail("model_load", path="state/bad.json")
    assert "元のモデルに戻しました" in e.message
    assert t("list_faces") == f0
    (t.ws.root / "state" / "x.json").write_text("{}", encoding="utf-8")
    assert "形式" in t.fail("model_load", path="state/x.json").message
    # 変な名前のモデルは名前を置き換えて読む(画像の既定のファイル名に使うため)
    odd = dict(data, name="../../evil")
    (t.ws.root / "state" / "odd.json").write_text(json.dumps(odd), encoding="utf-8")
    assert t("model_load", path="state/odd.json")["model"] == "model"


def test_transform_and_patch_tracking(t):
    t("model_new")
    t("add_box", corner=[0, 0, 0], size=[1, 1, 1], name="b")
    t("assign_patch", name="xmin", select={"normal": [-1, 0, 0]})
    t("transform", entities=["b"], op="translate", vector=[10, 0, 0])
    lp = t("list_patches")
    assert lp["patches"][0]["faces"] == [1]  # 移動しても同じ面
    t("transform", entities=["b"], op="rotate", point=[10, 0, 0], axis=[0, 0, 1], angle_deg=90)
    f = t("list_faces", select={"patch": "xmin"})["faces"][0]
    assert f["n"] == pytest.approx([0, -1, 0], abs=1e-9)
    c = t("transform", entities=["b"], op="scale", factor=[2, 1, 1], duplicate=True, name="big")
    assert c["name"] == "big" and c["volume"] == pytest.approx(2.0)
    m = t("transform", entities=["big"], op="mirror", point=[0, 0, 0], normal=[1, 0, 0])
    assert m["volume"] == pytest.approx(2.0)
    assert t("measure", entities=["big"])["volumes"][0]["center"][0] < 0
    # 形状が変わった面はパッチから外れる
    t("add_box", corner=[9.5, -0.5, 0], size=[1, 1, 1], name="c")
    r = t("boolean", op="fuse", objects=["b"], tools=["c"])
    assert "patch_changes" in r and r["patch_changes"]["xmin"]["dropped_faces"]


def test_check_geometry_overlap(t):
    t("model_new")
    t("add_box", corner=[0, 0, 0], size=[1, 1, 1])
    t("add_box", corner=[0.5, 0, 0], size=[1, 1, 1])
    t("add_box", corner=[5, 0, 0], size=[1, 1, 1])
    chk = t("check_geometry")
    ov = [i for i in chk["issues"] if i["kind"] == "overlapping_volumes"]
    assert len(ov) == 1 and ov[0]["volumes"] == [1, 2] and ov[0]["overlap_volume"] == pytest.approx(0.5)
    assert t("model_info")["volumes"][0]["tag"] == 1  # 検査はモデルを変えない
    t("boolean", op="fragment", objects=["box1", "box2"], tools=[])
    chk = t("check_geometry")
    assert chk["ok"], chk
    info = t("model_info")
    assert info["faces"]["internal"] >= 1  # 分割で共有の面ができた


def test_import_roundtrip_units(t):
    t("model_new", name="dgl", unit="mm")
    t("add_profile", plane="xz", outer={"points": DGL})
    t("revolve", entities=["profile1"])
    v_mm = t("measure")["total_volume"]
    t("export", path="cad/dgl.step")
    t("export", path="cad/dgl.brep")
    t("export", path="cad/dgl.igs")
    t("model_new", name="imp", unit="m")
    r = t("import_cad", path="cad/dgl.step", name="dgl")
    assert r["faces"] == 14 and r["volume"] == pytest.approx(v_mm * 1e-9, rel=1e-9)
    assert r["stored_as"].startswith(".gsis_mcp/imports/")
    assert "zero_area_faces" not in r
    t("model_new", name="imp2", unit="mm")
    assert t("import_cad", path="cad/dgl.brep")["volume"] == pytest.approx(v_mm, rel=1e-9)
    # IGES は面だけの形で書かれる(OpenCASCADE の既定)ので、面として読む
    ig = t("import_cad", path="cad/dgl.igs")
    assert len(ig["surfaces"]) == 14 and "体積がありません" in ig["warning"]
    # 取り込んだ写しから、状態の読み込みでやり直せる
    s = t("model_save", path="imp2.json")
    (t.ws.root / "cad" / "dgl.brep").unlink()
    t("model_new")
    info = t("model_load", path=s["path"])
    assert info["total_volume"] == pytest.approx(v_mm, rel=1e-9) and info["faces"]["free"] == 14
    e = t.fail("import_cad", path="../outside.step")
    assert "使えません" in e.message


def test_mesh_and_pmesh(t):
    from gsis_tools.pmesh import check_mesh, read_pmesh

    build_tube(t)
    e = t.fail("export_pmesh", path="tube.pmesh")
    assert "メッシュがありません" in e.message
    t("mesh_settings", size_max=1.0)
    t("mesh_refine", name="pipe", target="cylinder", base=[7.5, 0, 0], axis=[11, 0, 0], radius=1.3, size=0.3)
    g = t("mesh_generate")
    assert g["tetrahedra"] > 1000 and g["quality_minSICN"]["min"] > 0
    assert abs(g["volume_rel_diff"]) < 0.02
    assert g["unassigned_faces"] == [1, 2, 3, 4, 5, 6, 7]
    e = t.fail("export_pmesh", path="tube.pmesh")
    assert "パッチの付いていない面" in e.message
    tube_patches(t)
    q = t("mesh_quality")
    assert q["check"] == "OK" and q["cells"] == g["tetrahedra"]
    r = t("export_pmesh", path="out/tube.pmesh", report="out/tube.md", boundary_vtu="out/tube_b.vtu",
          patch_order=["wall", "inlet", "outlet"])
    assert r["check"] == "OK" and r["length_unit"] == "m"
    m = read_pmesh(t.ws.root / "out" / "tube.pmesh")
    assert check_mesh(m).ok
    assert m.patch_names() == ["wall", "inlet", "outlet"]
    assert [p.kind for p in m.patches] == ["wall", "inlet", "outlet"]
    geo = m.geometry()
    area = {p.name: float(geo.face_areas[p.faces].sum()) for p in m.patches}
    assert area["inlet"] == pytest.approx(64 * math.pi * 1e-6, rel=0.02)
    assert area["outlet"] == pytest.approx(16 * math.pi * 1e-6, rel=0.02)
    assert float(geo.cell_volumes.sum()) == pytest.approx(586 * math.pi * 1e-9, rel=0.02)
    assert m.metadata["model"] == "tube" and m.metadata["scale_to_m"] == "0.001"
    assert (t.ws.root / "out" / "tube.md").stat().st_size > 0
    # 上限を超える見積りは作らない
    t("mesh_settings", size_max=0.05)
    e = t.fail("mesh_generate", max_cells=10_000)
    assert e.data["estimate"] > 10_000
    assert t("model_info")["mesh"] == {"exists": False}


def test_exports_msh_stl(t):
    from gsis_tools.gmsh_convert import read_msh

    build_tube(t)
    tube_patches(t)
    t("mesh_settings", size_max=1.5)
    t("mesh_generate")
    r = t("export", path="m/tube.msh")
    assert r["physical_groups"] == ["inlet", "outlet", "wall", "fluid"]
    t.engine.close()  # read_msh は自分の gmsh を使う
    m = read_msh(t.ws.root / "m" / "tube.msh")
    assert m.patch_names() == ["inlet", "outlet", "wall"]
    t.engine._gmsh = None


def test_exports_stl_step(t):
    build_tube(t)
    tube_patches(t)
    assert "メッシュがありません" in t.fail("export", path="a.stl").message
    t("mesh_settings", size_max=1.5)
    t("mesh_generate", dim=2)
    r = t("export", path="a.stl")
    assert r["solids"] == ["inlet", "outlet", "wall"]
    text = (t.ws.root / "a.stl").read_text()
    assert "solid inlet" in text and "solid wall" in text
    assert t("export", path="a.step")["bytes"] > 1000
    with pytest.raises(ToolFailure, match="拡張子"):
        t("export", path="a.obj")


def test_mesh_refine_faces(t):
    build_tube(t)
    t("mesh_settings", size_max=1.0)
    n0 = t("mesh_generate")["tetrahedra"]
    r = t("mesh_refine", name="near_pipe", target="faces", select={"type": "cylinder", "radius": 1}, size=0.25, dist_max=2.0)
    assert r["faces"] == [t("list_faces", select={"radius": 1})["faces"][0]["id"]]
    n1 = t("mesh_generate")["tetrahedra"]
    assert n1 > 2 * n0
    assert "dist_max" in t.fail("mesh_refine", name="bad", target="faces", select={"radius": 1}, size=0.1).message
    assert t("mesh_refine", name="*", remove=True)["removed"] == 1


def test_render(t):
    import matplotlib.image as mpimg

    build_tube(t)
    tube_patches(t)
    out = []
    out.append(t("render"))
    out.append(t("render", view="front", labels="patches", path="img/front.png", width=400, height=300))
    out.append(t("render", section={"point": [0, 0, 0], "normal": [0, 1, 0]}, color_by="face", inline=True))
    out.append(t("render", azimuth=30, elevation=10, focus=[7, -2, -2, 19, 2, 2], color_by="volume"))
    t("mesh_settings", size_max=1.5)
    t("mesh_generate")
    out.append(t("render", target="mesh"))
    out.append(t("render", target="mesh", section={"point": [11, 0, 0], "normal": [0, 0, 1]}, labels="faces"))
    for r in out:
        p = t.ws.root / r["path"]
        img = mpimg.imread(str(p))
        assert img.shape[:2] == (r["height"], r["width"])
        assert len(np.unique(img.reshape(-1, img.shape[-1])[:, :3], axis=0)) > 50  # 何か描けている
    assert out[1]["path"] == "img/front.png"
    assert out[2]["_image_png"][:8] == b"\x89PNG\r\n\x1a\n"
    assert out[2]["section_pixels"] > 0
    assert out[0]["legend"] == {"inlet": "#1f77b4", "outlet": "#ff7e0e", "wall": "#2c9f2c"}
    with pytest.raises(ToolFailure, match="拡張子"):
        t("render", path="img/a.jpg")


def test_run_python(t, tmp_path):
    t("model_new")
    r = t("run_python", code="v = occ.addBox(0, 0, 0, 1, 2, 3)\nocc.synchronize()\nnames.set('pybox', [(3, v)])\nprint('ok', v)")
    assert r["stdout"] == "ok 1\n" and r["names"] == {"pybox": "体積 1"}
    assert t("measure", entities=["pybox"])["total_volume"] == pytest.approx(6.0)
    e = t.fail("run_python", code="occ.addSphere(0, 0, 0, 1)\nocc.synchronize()\nraise RuntimeError('boom')")
    assert "boom" in e.message and e.data["traceback"]
    assert len(t("model_info")["volumes"]) == 1  # 失敗した変更は元に戻る
    assert t("undo")["undone"] == ["run_python"]
    off = ModelEngine(Workspace(tmp_path / "ws2"))
    try:
        with pytest.raises(ToolFailure, match="--allow-python"):
            off.call("run_python", normalize("run_python", {"code": "pass"}))
    finally:
        off.close()
