"""CAD から流体領域を作り、面と体積を検査する。MCP の engine を直接再利用(Qt なし)。"""

from __future__ import annotations

import hashlib
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..mcp.engine import CAD_SUFFIXES, UNITS, ModelEngine
from ..mcp.workspace import Workspace
from .ai.surface import SurfaceData, update_surface


@dataclass
class GeometryResult:
    metadata: dict[str, Any]
    surface: SurfaceData


def prepare_geometry(source: Path, directory: Path, *, unit: str = "mm", flow: str = "internal",
                     box_factor: float = 3.0) -> GeometryResult:
    """CAD の写しと流体領域の BREP を作る。検査・表示の座標と長さ・面積・体積は SI。

    STEP / IGES の単位情報は OCC が解釈する。BREP は単位情報がないため選択した単位で解釈する。
    """
    source, directory = Path(source).resolve(), Path(directory).resolve()
    if unit not in UNITS or flow not in ("internal", "external"):
        raise ValueError("単位または流れの種類が不正です")
    if not math.isfinite(box_factor) or box_factor <= 1:
        raise ValueError("外部流の箱の倍率は 1 より大きくしてください")
    if source.suffix.lower() not in CAD_SUFFIXES:
        raise ValueError("STEP・IGES・BREP を選んでください")
    geom = directory / "geometry"
    geom.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    stored = geom / f"source_{digest}{source.suffix.lower()}"
    if source != stored:
        shutil.copy2(source, stored)
    engine = ModelEngine(Workspace(directory))
    try:
        engine.call("model_new", {"unit": unit})
        imported = stored
        if stored.suffix.lower() in (".igs", ".iges"):
            imported = _normalise_iges(engine, stored, unit)
            engine.call("model_new", {"unit": unit})
        engine.call("import_cad", {"path": imported.relative_to(directory).as_posix(), "name": "solid"})
        g = engine.gmsh
        initial = inspect_geometry(engine)
        if not initial["valid"]:
            raise ValueError("閉じた立体が 1 つ必要です: " + "・".join(initial["errors"]))
        if flow == "external":
            bbox = np.asarray(g.model.getBoundingBox(3, engine._volumes()[0]), float)
            size = bbox[3:] - bbox[:3]
            corner = (bbox[3:] + bbox[:3]) / 2 - box_factor * size / 2
            engine.call("add_box", {"corner": corner.tolist(), "size": (size * box_factor).tolist(), "name": "farfield"})
            engine.call("boolean", {"op": "cut", "objects": ["farfield"], "tools": ["solid"], "name": "fluid"})
        report = inspect_geometry(engine)
        if not report["valid"]:
            raise ValueError("流体領域を作れません: " + "・".join(report["errors"]))
        # 内容ごとの名前にし、前に受理した形状を失敗時に上書きしない。
        suffix = hashlib.sha256(f"{digest}:{unit}:{flow}:{box_factor}".encode()).hexdigest()[:16]
        domain = f"geometry/domain_{suffix}.brep"
        engine.call("export", {"path": domain})
        surface = update_surface(None, engine.call("gui_surface"))
        scale = UNITS[unit]
        surface.points = surface.points * scale
        surface.unit = "m"
        for f in surface.faces.values():
            f["area"] *= scale**2
            f["c"] = (np.asarray(f["c"]) * scale).tolist()
        metadata = dict(source=stored.relative_to(directory).as_posix(), domain=domain, unit=unit, flow=flow,
                        box_factor=box_factor, **report)
        return GeometryResult(metadata, surface)
    except Exception as exc:
        # gmsh は独自の型でなく Exception を投げる。GUI・CLI の双方で入力エラーとして扱う。
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"CAD の読み込み・検査に失敗しました: {exc}") from exc
    finally:
        engine.close()


def _normalise_iges(engine, source, unit):
    """IGES の閉じた面を縫い合わせる。小さい面・辺の除去や形状修復はしない。

    OCC の IGES 読み込みは初回に OCCTargetUnit=MM を拒む版があるため、既定の mm で読み、
    選択した座標単位へ変換して BREP を介して engine に渡す。既存 MCP の振る舞いは変えない。
    """
    g = engine.gmsh
    previous = g.option.getString("Geometry.OCCTargetUnit")
    g.option.setString("Geometry.OCCTargetUnit", "")
    try:
        shapes = g.model.occ.importShapes(str(source), highestDimOnly=True)
        factor = 1e-3 / UNITS[unit]
        if factor != 1:
            g.model.occ.dilate(shapes, 0, 0, 0, factor, factor, factor)
        g.model.occ.synchronize()
        if not g.model.getEntities(3):
            g.model.occ.healShapes(tolerance=1e-8, fixDegenerated=False, fixSmallEdges=False,
                                  fixSmallFaces=False, sewFaces=True, makeSolids=True)
            g.model.occ.synchronize()
        target = source.with_suffix(".normalised.brep")
        g.write(str(target))
        return target
    finally:
        g.option.setString("Geometry.OCCTargetUnit", previous)


def inspect_geometry(engine: ModelEngine) -> dict[str, Any]:
    g = engine.gmsh
    check = engine.call("check_geometry")
    scale = UNITS[engine.unit]
    volumes = engine._volumes()
    errors = []
    if len(volumes) != 1:
        errors.append(f"立体の数: {len(volumes)}(必要: 1)")
    for issue in check["issues"]:
        if issue["kind"] in ("no_volume", "non_positive_volume", "free_surfaces", "overlapping_volumes"):
            errors.append(issue.get("message", issue["kind"]))
    volume = sum(float(engine.occ.getMass(3, v)) for v in volumes) * scale**3
    if not volume > 0:
        errors.append("体積が正ではありません")
    bbox = np.asarray(g.model.getBoundingBox(-1, -1), float) * scale
    sizes = bbox[3:] - bbox[:3]
    faces = []
    for tag, f in sorted(engine.face_infos().items()):
        faces.append(dict(id=tag, area=float(f.area * scale**2), centroid=(f.centroid * scale).tolist(),
                          normal=None if f.normal is None else f.normal.tolist(), type=f.type))
    total_area = sum(f["area"] for f in faces)
    small_faces = [f["id"] for f in faces if f["area"] < 1e-6 * total_area]
    diagonal = float(np.linalg.norm(sizes))
    small_edges = [int(t) for _, t in g.model.getEntities(1)
                   if g.model.getType(1, t) != "Unknown" and float(engine.occ.getMass(1, t)) * scale < 1e-5 * diagonal]
    warnings = []
    if small_faces:
        warnings.append(f"小さい面(全表面積の 1e-6 未満): {small_faces}")
    if small_edges:
        warnings.append(f"短い辺(対角長の 1e-5 未満): {small_edges}")
    return dict(valid=not errors, errors=errors, warnings=warnings, volume=volume, bbox=bbox.tolist(),
                length_ref=float(sizes.min()), faces=faces)
