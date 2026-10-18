"""MCP のツールだけで 3 つの計算領域を作り、境界パッチ付きの .pmesh を書き出して検査する(フェーズ 7 の確認)。

MCP サーバー(gsis-mcp)を子プロセスとして起動し、stdio の MCP クライアント(公式 Python SDK)で
initialize → tools/list → tools/call を行う。エージェントが MCP で行う操作を、そのままスクリプトにしたもの。

    uv run --project python --extra mcp python cases/mcp_demo/run_demo.py            # 3 つとも
    uv run --project python --extra mcp python cases/mcp_demo/run_demo.py --case dgl # 1 つだけ
    uv run --project python --extra mcp python cases/mcp_demo/run_demo.py --launch uv # サーバーを uv run ... gsis-mcp で起動

作業フォルダは cases/mcp_demo/(サーバーはこの外に書かない)。出力はケースごとのフォルダ:

- ``<case>/<case>.pmesh`` … ソルバー用のメッシュ(m)と、品質レポート ``<case>_quality.md``
- ``<case>/<case>_state.json`` … MCP の状態(手順の履歴。model_load で作り直せる)
- ``<case>/*.png`` … 形状・断面・メッシュの画像
- ``<case>/transcript.jsonl`` … MCP の呼び出しと応答の記録
- ``results.json``・``results.md`` … 体積の誤差、パッチ、セル数、gsis-tools mesh-info の検査の結果

ケース(寸法は docs/paper_notes.md §7):

- tube: 円管 → 真空(論文 §4.1)。管半径 R = 1 mm、上流槽 半径 8・長さ 8、管 長さ 10、下流槽 半径 4・長さ 4。
  パッチ inlet(x = 0)・outlet(x = 22)・wall
- dgl: 動的ガスロック。子午面の断面(docs/eval_solidcraft.md 付録 A の 16 頂点、mm。ウェハの切り欠きが
  回転軸上にある)を z 軸まわりに 360° 回す。体積をパップスの定理の値と比べる。パッチ inlet(r = 100 の環状の
  入口)・outlet_top(z = 220)・outlet_bottom(z = 0)・wall(ウェハを含む)
- sphere: 球まわり。半径 5 m の球から直径 1 m の球を抜く。パッチ far_field・sphere
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# DGL の子午面の断面 (r, z) [mm](docs/eval_solidcraft.md 付録 A)
DGL_PROFILE = [[0, 0], [100, 0], [100, 100], [3.5, 100], [5.2, 108.5], [100, 108.5], [100, 111.5], [5.8, 111.5],
               [7.5, 120], [100, 120], [100, 220], [0, 220], [0, 90], [50, 90], [50, 88], [0, 88]]


def shoelace(pts):
    """多角形の面積と重心の x(u)。"""
    a = cx = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
    a *= 0.5
    return abs(a), cx / (6.0 * a)


class Client:
    """MCP の呼び出しを記録しながら行う。"""

    def __init__(self, session: ClientSession, log: Path):
        self.s = session
        self.log = log
        log.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(log, "w", encoding="utf-8", newline="\n")
        self.calls = 0

    async def __call__(self, tool: str, **args):
        t0 = time.perf_counter()
        res = await self.s.call_tool(tool, args)
        dt = time.perf_counter() - t0
        text = res.content[0].text if res.content else ""
        self.calls += 1
        rec = {"tool": tool, "args": args, "is_error": res.is_error, "seconds": round(dt, 3), "result": json.loads(text)}
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        short = text if len(text) < 300 else text[:300] + "…"
        print(f"  {tool:<15} {dt:6.2f} s  {'ERROR ' if res.is_error else ''}{short}")
        if res.is_error:
            raise RuntimeError(f"{tool} が失敗しました: {text}")
        return rec["result"]

    def close(self):
        self.fh.close()


# ---------------------------------------------------------------------------
# ケース
# ---------------------------------------------------------------------------


async def case_tube(c: Client) -> dict:
    """円管 → 真空(R = 1 mm)。"""
    await c("model_new", name="tube", unit="mm")
    await c("batch", steps=[
        {"tool": "add_cylinder", "args": {"base": [0, 0, 0], "axis": [8, 0, 0], "radius": 8, "name": "upstream"}},
        {"tool": "add_cylinder", "args": {"base": [8, 0, 0], "axis": [10, 0, 0], "radius": 1, "name": "pipe"}},
        {"tool": "add_cylinder", "args": {"base": [18, 0, 0], "axis": [4, 0, 0], "radius": 4, "name": "downstream"}},
        {"tool": "boolean", "args": {"op": "fuse", "objects": ["upstream"], "tools": ["pipe", "downstream"], "name": "tube"}},
        {"tool": "measure", "args": {"expected_volume": 586 * math.pi, "rtol": 1e-9}},
    ])
    exact = 586 * math.pi
    m = await c("measure", expected_volume=exact)
    await c("check_geometry", mesh_test=True)
    await c("list_faces")
    await c("assign_patch", name="inlet", select={"on_plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}})
    await c("assign_patch", name="outlet", select={"on_plane": {"point": [22, 0, 0], "normal": [1, 0, 0]}})
    await c("assign_patch", name="wall", select={"unassigned": True})
    lp = await c("list_patches")
    await c("render", path="tube/tube_geometry.png")
    await c("render", path="tube/tube_section.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]})
    # メッシュ: 管の中と出入口の近くを細かく(管の中 0.12 R、槽の遠方 0.6 R)
    await c("mesh_settings", size_max=0.6, curvature_points=0)
    await c("mesh_refine", name="pipe", target="cylinder", base=[7.0, 0, 0], axis=[12.0, 0, 0], radius=1.4, size=0.12)
    await c("mesh_refine", name="mouths", target="faces", select={"type": "cylinder", "radius": 1},
            size=0.12, size_far=0.6, dist_min=0.3, dist_max=5.0)
    g = await c("mesh_generate", max_cells=1_000_000)
    q = await c("mesh_quality")
    await c("render", target="mesh", path="tube/tube_mesh_section.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]})
    await c("render", target="mesh", path="tube/tube_mesh_mouth.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]},
            focus=[5, -3, -3, 11, 3, 3], view="front")
    e = await c("export_pmesh", path="tube/tube.pmesh", report="tube/tube_quality.md")
    await c("model_save", path="tube/tube_state.json")
    return {"exact_volume_mm3": exact, "measure": m, "patches": lp, "mesh": g, "quality": q, "pmesh": e,
            "exact_areas_m2": {"inlet": 64 * math.pi * 1e-6, "outlet": 16 * math.pi * 1e-6}}


async def case_dgl(c: Client) -> dict:
    """動的ガスロック(子午面の断面を z 軸まわりに回す。ウェハの切り欠きは軸上)。"""
    area, rc = shoelace(DGL_PROFILE)
    pappus = 2 * math.pi * rc * area
    await c("model_new", name="dgl", unit="mm")
    p = await c("add_profile", plane="xz", outer={"points": DGL_PROFILE}, name="meridian")
    r = await c("revolve", entities=["meridian"], axis_point=[0, 0, 0], axis_dir=[0, 0, 1], angle_deg=360, name="dgl")
    m = await c("measure", expected_volume=pappus)
    chk = await c("check_geometry", mesh_test=True)
    await c("list_faces")
    await c("assign_patch", name="inlet", select={"type": "cylinder", "radius": 100, "in_box": [-101, -101, 108, 101, 101, 112]})
    await c("assign_patch", name="outlet_top", select={"on_plane": {"point": [0, 0, 220], "normal": [0, 0, 1]}})
    await c("assign_patch", name="outlet_bottom", select={"on_plane": {"point": [0, 0, 0], "normal": [0, 0, 1]}})
    await c("assign_patch", name="wall", select={"unassigned": True})
    lp = await c("list_patches")
    await c("render", path="dgl/dgl_geometry.png")
    await c("render", path="dgl/dgl_section.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]})
    await c("render", path="dgl/dgl_section_slot.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]},
            focus=[-2, -1, 84, 60, 1, 124], view="front", labels="patches")
    # STEP の往復(書き出して別のモデルに読み込み、体積と面の数を確かめる)
    await c("export", path="dgl/dgl.step")
    await c("model_save", path="dgl/dgl_state.json")
    await c("model_new", name="dgl_step", unit="mm")
    imp = await c("import_cad", path="dgl/dgl.step", name="dgl")
    imp_m = await c("measure", expected_volume=pappus)
    await c("model_load", path="dgl/dgl_state.json")
    # メッシュ: 中央の通路・環状の流路(高さ 3 mm)・ウェハのまわりを細かく
    await c("mesh_settings", size_max=6.0, size_min=0.5, curvature_points=12)
    await c("mesh_refine", name="passage", target="cylinder", base=[0, 0, 97], axis=[0, 0, 26], radius=10, size=1.0)
    await c("mesh_refine", name="slot", target="box", box=[-101, -101, 108, 101, 101, 112], size=1.6, thickness=6)
    await c("mesh_refine", name="wafer", target="faces", select={"in_box": [-51, -51, 87.5, 51, 51, 90.5]},
            size=1.6, size_far=6.0, dist_min=0.5, dist_max=20)
    g = await c("mesh_generate", max_cells=2_000_000)
    q = await c("mesh_quality")
    await c("render", target="mesh", path="dgl/dgl_mesh_section.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]})
    e = await c("export_pmesh", path="dgl/dgl.pmesh", report="dgl/dgl_quality.md")
    await c("model_save", path="dgl/dgl_state.json")
    return {"profile_area_mm2": area, "profile_centroid_r_mm": rc, "pappus_volume_mm3": pappus, "profile": p,
            "revolve": r, "measure": m, "check": chk, "step_import": {"import": imp, "measure": imp_m},
            "patches": lp, "mesh": g, "quality": q, "pmesh": e,
            "exact_areas_m2": {"inlet": 2 * math.pi * 100 * 3 * 1e-6, "outlet_top": math.pi * 100**2 * 1e-6,
                               "outlet_bottom": math.pi * 100**2 * 1e-6}}


async def case_sphere(c: Client) -> dict:
    """球まわり(半径 5 m の球から直径 1 m の球を抜く)。"""
    exact = 4.0 / 3.0 * math.pi * (5.0**3 - 0.5**3)
    await c("model_new", name="sphere", unit="m")
    await c("add_sphere", center=[0, 0, 0], radius=5.0, name="far")
    await c("add_sphere", center=[0, 0, 0], radius=0.5, name="body")
    await c("boolean", op="cut", objects=["far"], tools=["body"], name="domain")
    m = await c("measure", expected_volume=exact)
    await c("check_geometry", mesh_test=True)
    await c("list_faces")
    await c("assign_patch", name="far_field", select={"type": "sphere", "radius": 5.0}, kind="patch")
    await c("assign_patch", name="sphere", select={"type": "sphere", "radius": 0.5}, kind="wall")
    lp = await c("list_patches")
    await c("render", path="sphere/sphere_section.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]})
    # メッシュ: 球の近くを D/25、遠方場で 0.5 m(球からの距離で線形に)
    await c("mesh_settings", size_max=0.5, curvature_points=0)
    await c("mesh_refine", name="near_body", target="faces", select={"patch": "sphere"},
            size=0.04, size_far=0.5, dist_min=0.0, dist_max=4.0)
    g = await c("mesh_generate", max_cells=1_500_000)
    q = await c("mesh_quality")
    await c("render", target="mesh", path="sphere/sphere_mesh_section.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]})
    await c("render", target="mesh", path="sphere/sphere_mesh_body.png", section={"point": [0, 0, 0], "normal": [0, -1, 0]},
            focus=[-1, -1, -1, 1, 1, 1], view="front")
    e = await c("export_pmesh", path="sphere/sphere.pmesh", report="sphere/sphere_quality.md")
    await c("model_save", path="sphere/sphere_state.json")
    return {"exact_volume_m3": exact, "measure": m, "patches": lp, "mesh": g, "quality": q, "pmesh": e,
            "exact_areas_m2": {"far_field": 4 * math.pi * 25, "sphere": 4 * math.pi * 0.25}}


CASES = {"tube": case_tube, "dgl": case_dgl, "sphere": case_sphere}


# ---------------------------------------------------------------------------


def mesh_info(pmesh: Path) -> dict:
    """gsis-tools mesh-info で .pmesh を検査する。"""
    cmd = [sys.executable, "-m", "gsis_tools", "mesh-info", str(pmesh)]
    t0 = time.perf_counter()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), env=env)
    out = p.stdout
    m = re.search(r"cells=(\d+)", out)
    return {"returncode": p.returncode, "ok": p.returncode == 0 and "検査: OK" in out,
            "cells": int(m.group(1)) if m else None, "seconds": round(time.perf_counter() - t0, 2),
            "stdout_tail": out.strip().splitlines()[-8:]}


async def run(cases: list[str], launch: str) -> dict:
    if launch == "uv":
        params = StdioServerParameters(command="uv", args=["run", "--project", str(ROOT / "python"), "--extra", "mcp",
                                                            "gsis-mcp", "--workspace", str(HERE)], cwd=str(ROOT))
    else:
        params = StdioServerParameters(command=sys.executable, args=["-m", "gsis_tools.mcp", "--workspace", str(HERE)],
                                       cwd=str(ROOT))
    results: dict = {}
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            tools = await s.list_tools()
            results["server"] = {"name": init.server_info.name, "version": init.server_info.version,
                                 "protocol": init.protocol_version, "tools": len(tools.tools),
                                 "tools_list_bytes": len(tools.model_dump_json(by_alias=True, exclude_none=True))}
            print(f"server {init.server_info.name} {init.server_info.version}, protocol {init.protocol_version}, "
                  f"{len(tools.tools)} tools")
            for name in cases:
                print(f"== {name}")
                c = Client(s, HERE / name / "transcript.jsonl")
                t0 = time.perf_counter()
                try:
                    res = await CASES[name](c)
                finally:
                    c.close()
                res["calls"] = c.calls
                res["seconds"] = round(time.perf_counter() - t0, 1)
                results[name] = res
    for name in cases:
        info = mesh_info(HERE / name / f"{name}.pmesh")
        results[name]["mesh_info"] = info
        print(f"== gsis-tools mesh-info {name}: {'OK' if info['ok'] else 'NG'} ({info['cells']} cells)")
    return results


def write_summary(results: dict) -> None:
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    prev = {}
    jpath = HERE / "results.json"
    if jpath.exists():
        try:
            prev = json.loads(jpath.read_text(encoding="utf-8"))
        except ValueError:
            prev = {}
    prev.update(results)
    prev["generated"] = now
    jpath.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    lines = ["# MCP のデモの結果(run_demo.py が書く)", "", f"- 実行: {now}"]
    sv = prev.get("server", {})
    if sv:
        lines.append(f"- サーバー: {sv.get('name')} {sv.get('version')}、プロトコル {sv.get('protocol')}、"
                     f"ツール {sv.get('tools')} 個(tools/list {sv.get('tools_list_bytes', 0) / 1024:.1f} KB)")
    lines += ["", "| ケース | 形状の体積(解析値との相対誤差) | メッシュの体積の相対差 | パッチ(面の数) | セル数 | "
              "mesh-info | MCP の呼び出し / 時間 |", "|---|---|---|---|---|---|---|"]
    for name in CASES:
        r = prev.get(name)
        if not r:
            continue
        rel = r["measure"]["rel_error"]
        mrel = r["quality"]["volume_rel_diff"]
        pats = "、".join(f"{p['name']}({p['faces']})" for p in r["pmesh"]["patches"])
        mi = r.get("mesh_info", {})
        lines.append(f"| {name} | {rel:+.1e} | {mrel:+.2e} | {pats} | {r['pmesh']['cells']:,} | "
                     f"{'OK' if mi.get('ok') else 'NG'} | {r['calls']} 回 / {r['seconds']} s |")
    lines += ["", "パッチの面積(.pmesh、m²)と解析値:", ""]
    for name in CASES:
        r = prev.get(name)
        if not r:
            continue
        ex = r.get("exact_areas_m2", {})
        parts = []
        for p in r["pmesh"]["patches"]:
            if p["name"] in ex:
                parts.append(f"{p['name']} {p['area_m2']:.6g}(解析値 {ex[p['name']]:.6g}、{(p['area_m2'] / ex[p['name']] - 1):+.1e})")
            else:
                parts.append(f"{p['name']} {p['area_m2']:.6g}")
        lines.append(f"- {name}: " + "、".join(parts))
    d = prev.get("dgl")
    if d:
        lines += ["", f"DGL: 断面の面積 {d['profile_area_mm2']:.6g} mm²、重心の半径 {d['profile_centroid_r_mm']:.6g} mm、"
                      f"パップスの定理の体積 {d['pappus_volume_mm3']:.10g} mm³、回転体の体積 {d['measure']['total_volume']:.10g} mm³"
                      f"(相対誤差 {d['measure']['rel_error']:+.1e})、面 {d['revolve']['faces']} 枚、殻 "
                      f"{d['measure']['volumes'][0]['shells']}(外側とウェハの空洞)。STEP に書いて読み直した体積の相対誤差 "
                      f"{d['step_import']['measure']['rel_error']:+.1e}、面 {d['step_import']['import']['faces']} 枚"]
    lines.append("")
    (HERE / "results.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", choices=[*CASES, "all"], default="all")
    ap.add_argument("--launch", choices=("python", "uv"), default="python",
                    help="python: この Python で -m gsis_tools.mcp、uv: uv run --project python --extra mcp gsis-mcp")
    a = ap.parse_args()
    cases = list(CASES) if a.case == "all" else [a.case]
    results = asyncio.run(run(cases, a.launch))
    write_summary(results)
    ok = all(results[c]["mesh_info"]["ok"] for c in cases)
    print("結果:", HERE / "results.md", "すべて OK" if ok else "NG あり")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
