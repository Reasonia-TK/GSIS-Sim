"""既存ファイルを変更せず、管の例題を独立したプロジェクトへ複製する。"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import tomlkit

from ..pmesh import read_pmesh, read_header
from .casefile import derive, load_case
from .general_settings import GeneralSettings
from .project import Project, save_project


def create_tube_project(root: Path, directory: Path, delta: int) -> Project:
    if delta not in (1, 10, 100, 1000):
        raise ValueError("管の例題は δ = 1・10・100・1000 です")
    directory = Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("例題の保存先は空のフォルダにしてください")
    source = root / f"cases/tube_vacuum/gsis/tube_delta{delta}_gsis.toml"
    doc = tomlkit.parse(source.read_text(encoding="utf-8")).unwrap()
    model = load_case(source).model
    d = derive(model)
    settings = GeneralSettings(mode=model.mode, gas="He", length_auto=False, velocity_auto=False,
        **doc["gas"], L_ref=model.L_ref, T0=model.T0, wall_temperature=model.T0, p_ref=d.p_ref,
        T_min=model.T0, T_max=model.T0, vel_n=model.vel_n, vel_range=model.vel_range,
        kinetic=doc.get("kinetic", {}), macro=doc.get("macro", {}),
        **{k: doc["gsis"][k] for k in ("n_cis", "max_pbc", "cfl_pbc", "tolerance")},
        gsis_extra={k: v for k, v in doc["gsis"].items() if k not in ("n_cis", "max_pbc", "cfl_pbc", "tolerance")})
    mesh = root / "cases/tube_vacuum/mesh/tube_hex.pmesh"
    (directory / "mesh").mkdir(parents=True)
    shutil.copy2(mesh, directory / "mesh/tube_hex.pmesh")
    p = Project(name=f"tube_delta{delta}_gsis", directory=directory, settings=settings,
                geometry=dict(source_kind="existing_mesh", example="tube_vacuum", unit="m", flow="internal",
                              box_factor=3, length_ref=model.L_ref, accepted=True, warnings=[], faces=[]),
                mesh=dict(path="mesh/tube_hex.pmesh", cells=read_header(mesh)["n_cells"], validated=True))
    settings.estimated_cells = p.mesh["cells"]
    p.boundaries = [dict(b, faces=[i + 1]) for i, b in enumerate(doc["boundary"])]
    for b in p.boundaries:
        if b["type"] == "pressure_inlet":
            b.setdefault("pressure", d.p_ref)
            b.setdefault("tangential_velocity", "extrapolate")
    # CAD 面の代わりにメッシュのパッチを選択単位にする。
    result = mesh_geometry(p)
    p.geometry = result.metadata
    for stage in ("geometry", "settings", "boundaries", "mesh"):
        p.complete(stage)
    save_project(p)
    return p


def mesh_geometry(project):
    from .ai.surface import SurfaceData
    from .geometry import GeometryResult

    pm = read_pmesh(project.asset(project.mesh["path"]))
    geom = pm.geometry()
    triangles, ids, faces = [], [], []
    for i, patch in enumerate(pm.patches, 1):
        rows = np.arange(patch.start, patch.stop)
        areas = geom.face_areas[rows]
        normal = np.sum(geom.face_normals[rows] * areas[:, None], axis=0) / np.sum(areas)
        faces.append(dict(id=i, area=float(np.sum(areas)), type="mesh_patch", boundary=True,
                          centroid=np.average(geom.face_centres[rows], axis=0, weights=areas).tolist(),
                          normal=normal.tolist()))
        for f in rows:
            nodes = pm.face_nodes[pm.face_offsets[f]:pm.face_offsets[f + 1]]
            for j in range(1, len(nodes) - 1):
                triangles.append([nodes[0], nodes[j], nodes[j + 1]])
                ids.append(i)
    metadata = dict(project.geometry, faces=faces, volume=float(np.sum(geom.cell_volumes)))
    surface = SurfaceData(key=str(project.directory), unit="m", points=pm.points,
                          triangles=np.asarray(triangles), face_ids=np.asarray(ids),
                          faces={f["id"]: f for f in faces}, volumes=1)
    return GeometryResult(metadata, surface)
