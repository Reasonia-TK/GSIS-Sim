"""一般形状の四面体生成。engine を隔離プロセスで再利用し、停止を受け付ける。"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .boundaries import remap_boundaries, validate_boundaries
from .general_settings import estimate_vram, velocity_grid


class MeshCancelled(ValueError):
    pass


def validate_options(options, boundaries):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", options.get("name", "")):
        raise ValueError("メッシュ名は英字で始まる英数字・_・- にしてください")
    for key in ("size_max", "size_min", "growth"):
        v = options.get(key)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"{key} は有限の正の数にしてください")
    if options["size_min"] > options["size_max"] or not 1 < options["growth"] <= 3:
        raise ValueError("最小寸法 ≤ 最大寸法、成長率は 1 より大きく 3 以下にしてください")
    names = {b["patch"] for b in boundaries}
    for name, size in options.get("patch_sizes", {}).items():
        if name not in names or not isinstance(size, (int, float)) or not math.isfinite(size):
            raise ValueError("パッチの細かさの指定が不正です")
        if not options["size_min"] <= size <= options["size_max"]:
            raise ValueError(f"{name} の寸法は全体の最小寸法〜最大寸法にしてください")


def mesh_memory(project, cells):
    s = project.settings
    n, _ = velocity_grid(s)
    return estimate_vram(cells, math.prod(n), chunk=s.velocity_chunk,
                         free_bytes=int(s.free_vram_gib * 2**30), mode=s.mode)


def generate_mesh(project, options, cancel=None):
    """GUI のワーカースレッドから呼ぶ。停止した出力は採用せず、以前のファイルを保つ。"""
    validate_options(options, project.boundaries)
    validate_boundaries(project.boundaries, project.geometry["faces"])
    if project.blocked_reason("mesh"):
        raise ValueError(project.blocked_reason("mesh"))
    domain = project.asset(project.geometry["domain"])
    mesh_dir = project.asset("mesh")
    mesh_dir.mkdir(parents=True, exist_ok=True)
    # 同じボリュームで最後に置き換える。子プロセスの途中出力はプロジェクトに採用しない。
    with tempfile.TemporaryDirectory(prefix=".generating_", dir=mesh_dir) as td:
        work = Path(td)
        request = dict(domain=str(domain), geometry=project.geometry, boundaries=project.boundaries, options=options)
        req = work / "request.json"
        req.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        if cancel is not None and cancel.is_set():
            raise MeshCancelled("メッシュ生成を停止しました")
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with (work / "console.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen([sys.executable, "-m", "gsis_tools.gui.general_mesh", str(req)],
                                    stdout=log, stderr=log, creationflags=creation)
            try:
                while True:
                    try:
                        code = proc.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        if cancel is not None and cancel.is_set():
                            raise MeshCancelled("メッシュ生成を停止しました")
                if cancel is not None and cancel.is_set():
                    raise MeshCancelled("メッシュ生成を停止しました")
                result_file = work / "result.json"
                if code != 0 or not result_file.is_file():
                    error = work / "error.txt"
                    raise ValueError(error.read_text(encoding="utf-8") if error.exists() else f"メッシュ処理が終了しました(終了コード {code})")
                result = json.loads(result_file.read_text(encoding="utf-8"))
                result["memory"] = mesh_memory(project, result["cells"])
                stem = options["name"]
                for src, suffix in (("fluid.pmesh", ".pmesh"), ("quality.md", "_quality.md")):
                    (work / src).replace(mesh_dir / (stem + suffix))
                result["path"] = f"mesh/{stem}.pmesh"
                result["report"] = f"mesh/{stem}_quality.md"
                quality_path = mesh_dir / f"{stem}_quality.json"
                quality_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                result["quality_path"] = f"mesh/{stem}_quality.json"
                return result
            finally:
                if proc.poll() is None:
                    if os.name == "nt":
                        # venv の python.exe は実インタープリタを子に持つ。親だけ止めると gmsh が残る。
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=creation, check=False)
                    else:
                        proc.terminate()
                    proc.wait()


def _worker(request_path):
    from ..mcp.engine import ModelEngine, UNITS
    from ..mcp.workspace import Workspace
    from ..pmesh import read_pmesh
    from ..quality import compute_quality, format_report, summary
    from .geometry import inspect_geometry

    work = request_path.parent
    req = json.loads(request_path.read_text(encoding="utf-8"))
    # CAD の読み込み先も専用の作業領域内に置く。
    import shutil
    shutil.copy2(req["domain"], work / "domain.brep")
    engine = ModelEngine(Workspace(work))
    try:
        geometry, options = req["geometry"], req["options"]
        engine.call("model_new", {"unit": geometry["unit"]})
        engine.call("import_cad", dict(path="domain.brep", name="fluid"))
        faces = inspect_geometry(engine)["faces"]
        boundaries, lost = remap_boundaries(req["boundaries"], faces)
        if lost:
            raise ValueError("BREP の面を照合できません: " + " / ".join(lost))
        validate_boundaries(boundaries, faces)
        for b in boundaries:
            kind = "wall" if "wall" in b["type"] else ("inlet" if "inlet" in b["type"] else
                    "symmetry" if b["type"] == "symmetry" else "outlet")
            engine.call("assign_patch", dict(name=b["patch"], select={"faces": b["faces"]}, kind=kind))
        scale = UNITS[geometry["unit"]]
        engine.call("mesh_settings", dict(size_max=options["size_max"] / scale,
                                          size_min=options["size_min"] / scale, curvature_points=24,
                                          extend_from_boundary=False, threads=1))
        for b in boundaries:
            size = options.get("patch_sizes", {}).get(b["patch"], options["size_max"])
            if size < options["size_max"]:
                # Threshold の傾き dh/dd = growth - 1。細かいセルから段階的に広げる。
                distance = (options["size_max"] - size) / (options["growth"] - 1)
                engine.call("mesh_refine", dict(name=b["patch"], target="faces", select={"faces": b["faces"]},
                    size=size / scale, size_far=options["size_max"] / scale, dist_max=distance / scale))
        engine.call("mesh_generate", dict(dim=3))
        exported = engine.call("export_pmesh", dict(path="fluid.pmesh", patch_order=[b["patch"] for b in boundaries]))
        if exported["check"] != "OK":
            raise ValueError("生成したメッシュが幾何検査を通りませんでした")
        pm = read_pmesh(work / "fluid.pmesh")
        q = compute_quality(pm)
        result = summary(pm, q)
        result["cells"] = pm.n_cells
        by_id = {f["id"]: f for f in geometry["faces"]}
        exact = {b["patch"]: sum(by_id[t]["area"] for t in b["faces"]) for b in req["boundaries"]}
        for name, p in result["patches"].items():
            p["geometry_area"] = exact[name]
            p["area_difference"] = p["area"] - exact[name]
            p["relative_difference"] = (p["area"] - exact[name]) / exact[name]
        (work / "quality.md").write_text(format_report(pm, q, exact_areas=exact,
            exact_volume=geometry["volume"], notes=["SI 単位。gmsh 四面体。成長率は寸法場の傾き dh/dd + 1。"]), encoding="utf-8")
        (work / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    finally:
        engine.close()


if __name__ == "__main__":
    path = Path(sys.argv[1])
    try:
        _worker(path)
    except Exception as exc:
        (path.parent / "error.txt").write_text(str(exc), encoding="utf-8")
        sys.exit(1)
