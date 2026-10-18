"""メッシュの設定(gmsh のオプションと寸法の場)、セル数の見積り、メッシュの要約。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

ALG3D = {"delaunay": 1, "frontal": 4, "hxt": 10}
ALG2D = {"frontal-delaunay": 6, "delaunay": 5, "meshadapt": 1}
# セル数の見積り N ≈ C ∫ dV / s³ の C(s は gmsh のメッシュサイズ)。geometry/tube.py と同じ実測値
COUNT_COEFF = {"delaunay": 4.2, "frontal": 4.2, "hxt": 3.6}

DEFAULT_SETTINGS: dict[str, Any] = {
    "size_max": None,  # None ならモデルの外接箱の対角の 1/20
    "size_min": 0.0,
    "curvature_points": 0,  # 円周あたりの要素数(0 で使わない)
    "algorithm_3d": "delaunay",
    "algorithm_2d": "frontal-delaunay",
    "optimize": "netgen",  # "netgen" / "gmsh" / "none"
    "extend_from_boundary": True,
    "threads": None,
}


def effective_size_max(settings: dict, diag: float) -> float:
    s = settings.get("size_max")
    return float(s) if s else float(diag) / 20.0


def _sampling(gmsh, faces: list[int], size: float, given: int | None) -> int:
    if given:
        return int(given)
    ext = 0.0
    for f in faces:
        b = gmsh.model.getBoundingBox(2, f)
        ext = max(ext, math.dist(b[:3], b[3:]))
    return int(min(300, max(20, math.ceil(ext / max(2.0 * size, 1e-300)))))


def apply_settings(gmsh, settings: dict, refinements: dict[str, dict], face_lists: dict[str, list[int]],
                   diag: float, threads: int) -> dict[str, Any]:
    """gmsh のオプションと寸法の場を設定する。戻り値は実際に使った値。"""
    opt = gmsh.option
    size_max = effective_size_max(settings, diag)
    size_min = float(settings.get("size_min") or 0.0)
    opt.setNumber("Mesh.MeshSizeMax", size_max)
    opt.setNumber("Mesh.MeshSizeMin", size_min)
    opt.setNumber("Mesh.MeshSizeFromCurvature", int(settings.get("curvature_points") or 0))
    opt.setNumber("Mesh.MeshSizeFromPoints", 0)
    opt.setNumber("Mesh.MeshSizeExtendFromBoundary", 1 if settings.get("extend_from_boundary", True) else 0)
    opt.setNumber("Mesh.MeshSizeFactor", 1.0)
    opt.setNumber("Mesh.Algorithm", ALG2D[settings.get("algorithm_2d") or "frontal-delaunay"])
    opt.setNumber("Mesh.Algorithm3D", ALG3D[settings.get("algorithm_3d") or "delaunay"])
    opt.setNumber("Mesh.Optimize", 1)
    opt.setNumber("Mesh.ElementOrder", 1)
    opt.setNumber("Mesh.RecombineAll", 0)
    opt.setNumber("General.NumThreads", int(settings.get("threads") or threads))
    F = gmsh.model.mesh.field
    for t in list(F.list()):
        F.remove(t)
    ids = []
    for name, r in refinements.items():
        ids.append(_make_field(gmsh, r, face_lists.get(name, []), size_max))
    if ids:
        fmin = F.add("Min")
        F.setNumbers(fmin, "FieldsList", ids)
        F.setAsBackgroundMesh(fmin)
    return {"size_max": size_max, "size_min": size_min, "fields": len(ids)}


def _make_field(gmsh, r: dict, faces: list[int], size_max: float) -> int:
    F = gmsh.model.mesh.field
    kind = r["target"]
    size = float(r["size"])
    far = float(r.get("size_far") or size_max)
    if kind == "faces":
        d = F.add("Distance")
        F.setNumbers(d, "SurfacesList", faces)
        F.setNumber(d, "Sampling", _sampling(gmsh, faces, size, r.get("sampling")))
        t = F.add("Threshold")
        F.setNumber(t, "InField", d)
        F.setNumber(t, "SizeMin", size)
        F.setNumber(t, "SizeMax", far)
        F.setNumber(t, "DistMin", float(r.get("dist_min") or 0.0))
        F.setNumber(t, "DistMax", float(r["dist_max"]))
        return t
    if kind == "box":
        b = r["box"]
        f = F.add("Box")
        for k, v in zip(("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax"), b):
            F.setNumber(f, k, float(v))
        F.setNumber(f, "VIn", size)
        F.setNumber(f, "VOut", far)
        F.setNumber(f, "Thickness", float(r.get("thickness") or 0.0))
        return f
    if kind == "ball":
        f = F.add("Ball")
        c = r["center"]
        F.setNumber(f, "XCenter", float(c[0]))
        F.setNumber(f, "YCenter", float(c[1]))
        F.setNumber(f, "ZCenter", float(c[2]))
        F.setNumber(f, "Radius", float(r["radius"]))
        F.setNumber(f, "VIn", size)
        F.setNumber(f, "VOut", far)
        F.setNumber(f, "Thickness", float(r.get("thickness") or 0.0))
        return f
    if kind == "cylinder":
        base = np.asarray(r["base"], float)
        axis = np.asarray(r["axis"], float)
        c = base + 0.5 * axis
        half = 0.5 * axis
        f = F.add("Cylinder")
        for k, v in zip(("XCenter", "YCenter", "ZCenter"), c):
            F.setNumber(f, k, float(v))
        for k, v in zip(("XAxis", "YAxis", "ZAxis"), half):
            F.setNumber(f, k, float(v))
        F.setNumber(f, "Radius", float(r["radius"]))
        F.setNumber(f, "VIn", size)
        F.setNumber(f, "VOut", far)
        return f
    raise ValueError(f"未知の細分化の種類: {kind}")  # pragma: no cover


# ---------------------------------------------------------------------------
# セル数の見積り
# ---------------------------------------------------------------------------


def _face_nodes(gmsh, faces: list[int]) -> np.ndarray:
    pts = []
    for f in faces:
        _, coords, _ = gmsh.model.mesh.getNodes(2, f, includeBoundary=True)
        if len(coords):
            pts.append(np.asarray(coords, float).reshape(-1, 3))
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def size_at(gmsh, pts: np.ndarray, settings: dict, refinements: dict[str, dict], face_lists: dict[str, list[int]],
            size_max: float) -> np.ndarray:
    """点での目標のメッシュサイズ(gmsh の場と同じ式。面からの距離は 2 次元メッシュの節点で測る)。"""
    s = np.full(pts.shape[0], size_max)
    for name, r in refinements.items():
        size = float(r["size"])
        far = float(r.get("size_far") or size_max)
        kind = r["target"]
        if kind == "faces":
            nodes = _face_nodes(gmsh, face_lists.get(name, []))
            if nodes.shape[0] == 0:
                continue
            from scipy.spatial import cKDTree

            d, _ = cKDTree(nodes).query(pts)
            d0, d1 = float(r.get("dist_min") or 0.0), float(r["dist_max"])
            t = np.clip((d - d0) / max(d1 - d0, 1e-300), 0.0, 1.0)
            v = size + (far - size) * t
        elif kind == "box":
            b = np.asarray(r["box"], float)
            th = float(r.get("thickness") or 0.0)
            dd = np.maximum(np.maximum(b[:3] - pts, pts - b[3:]), 0.0)
            d = np.linalg.norm(dd, axis=1)
            v = np.where(d <= 0, size, far if th <= 0 else size + (far - size) * np.clip(d / th, 0, 1))
        elif kind == "ball":
            c = np.asarray(r["center"], float)
            th = float(r.get("thickness") or 0.0)
            d = np.linalg.norm(pts - c, axis=1) - float(r["radius"])
            v = np.where(d <= 0, size, far if th <= 0 else size + (far - size) * np.clip(d / th, 0, 1))
        elif kind == "cylinder":
            base = np.asarray(r["base"], float)
            axis = np.asarray(r["axis"], float)
            L = float(np.linalg.norm(axis))
            a = axis / L
            rel = pts - base
            t = rel @ a
            rad = np.linalg.norm(rel - t[:, None] * a, axis=1)
            inside = (t >= 0) & (t <= L) & (rad <= float(r["radius"]))
            v = np.where(inside, size, far)
        else:  # pragma: no cover
            continue
        s = np.minimum(s, v)
    smin = float(settings.get("size_min") or 0.0)
    return np.maximum(s, smin) if smin > 0 else s


def estimate_cells(gmsh, settings: dict, refinements: dict[str, dict], face_lists: dict[str, list[int]],
                   size_max: float, n_target: int = 800, seed: int = 0) -> int:
    """四面体の数の見積り(N ≈ C V 平均(1/s³)。点は体積の中から乱数で選ぶ)。"""
    occ = gmsh.model.occ
    vols = [t for _, t in gmsh.model.getEntities(3)]
    if not vols:
        return 0
    V = sum(float(occ.getMass(3, v)) for v in vols)
    bbs = np.array([gmsh.model.getBoundingBox(3, v) for v in vols])
    lo, hi = bbs[:, :3].min(axis=0), bbs[:, 3:].max(axis=0)
    rng = np.random.default_rng(seed)
    inside = []
    tries = 0
    while len(inside) < n_target and tries < 8 * n_target:
        p = lo + (hi - lo) * rng.random(3)
        tries += 1
        for v in vols:
            if gmsh.model.isInside(3, v, list(p)):
                inside.append(p)
                break
    coeff = COUNT_COEFF.get(settings.get("algorithm_3d") or "delaunay", 4.2)
    if not inside:
        return int(coeff * V / size_max**3)
    pts = np.array(inside)
    s = size_at(gmsh, pts, settings, refinements, face_lists, size_max)
    return int(coeff * V * float(np.mean(1.0 / s**3)))


# ---------------------------------------------------------------------------
# メッシュの要約
# ---------------------------------------------------------------------------


def node_index(gmsh) -> tuple[np.ndarray, np.ndarray]:
    tags, coords, _ = gmsh.model.mesh.getNodes()
    tags = np.asarray(tags, dtype=np.int64)
    X = np.asarray(coords, dtype=float).reshape(-1, 3)
    idx = np.full(int(tags.max()) + 1 if tags.size else 1, -1, dtype=np.int64)
    idx[tags] = np.arange(tags.size)
    return X, idx


def tetrahedra(gmsh) -> tuple[np.ndarray, np.ndarray]:
    """全ての四面体の要素番号と節点(gmsh の節点番号)。"""
    etags, enodes = gmsh.model.mesh.getElementsByType(4)
    return np.asarray(etags, dtype=np.int64), np.asarray(enodes, dtype=np.int64).reshape(-1, 4)


def surface_triangles(gmsh, face: int) -> np.ndarray:
    et, _, en = gmsh.model.mesh.getElements(2, face)
    out = []
    for t, n in zip(et, en):
        if int(t) == 2:
            out.append(np.asarray(n, dtype=np.int64).reshape(-1, 3))
    return np.concatenate(out) if out else np.zeros((0, 3), dtype=np.int64)


def mesh_stats(gmsh, dim: int, patch_faces: dict[str, list[int]], boundary_faces: list[int], geom_volume: float,
               rnd) -> dict[str, Any]:
    X, idx = node_index(gmsh)
    out: dict[str, Any] = {"dim": dim, "nodes": int(X.shape[0])}
    if dim == 3:
        etags, conn = tetrahedra(gmsh)
        out["tetrahedra"] = int(etags.size)
        if etags.size:
            P = X[idx[conn]]
            vol = np.abs(np.einsum("ij,ij->i", np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), P[:, 3] - P[:, 0])) / 6.0
            mv = float(vol.sum())
            out["mesh_volume"] = rnd(mv, 10)
            if geom_volume > 0:
                out["volume_rel_diff"] = rnd((mv - geom_volume) / geom_volume, 3)
            q = np.asarray(gmsh.model.mesh.getElementQualities(etags, "minSICN"))
            out["quality_minSICN"] = {"min": rnd(float(q.min()), 3), "p01": rnd(float(np.percentile(q, 1)), 3),
                                      "below_0.1": int((q < 0.1).sum())}
            h = np.cbrt(vol)
            out["cell_size_hV"] = {"min": rnd(float(h.min()), 4), "mean": rnd(float(h.mean()), 4),
                                   "max": rnd(float(h.max()), 4)}
    tri_count = {}
    for name, faces in patch_faces.items():
        n = 0
        for f in faces:
            n += surface_triangles(gmsh, f).shape[0]
        tri_count[name] = n
    assigned = {f for fs in patch_faces.values() for f in fs}
    un = [f for f in boundary_faces if f not in assigned]
    if un:
        tri_count["(未割り当て)"] = int(sum(surface_triangles(gmsh, f).shape[0] for f in un))
    out["boundary_triangles"] = tri_count
    return out
