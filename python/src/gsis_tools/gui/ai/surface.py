"""GUI 専用のツール ``gui_surface`` の応答を読み、3D 表示のデータ(pyvista)を作る。

応答の配列はリトルエンディアンの生のバイト列を base64 にしたもの(点 float32 (N, 3)、三角形 int32 (M, 3)、
三角形ごとの面の番号 int32 (M,))。形状とメッシュが変わっていないときは配列が省かれ(``unchanged``)、
パッチの対応だけが来るので、前の三角形を使い続ける。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

import numpy as np

_DTYPES = {"float32": "<f4", "int32": "<i4", "float64": "<f8", "int64": "<i8"}


def unpack_array(d: dict[str, Any] | None) -> np.ndarray | None:
    if not d:
        return None
    dt = np.dtype(_DTYPES.get(str(d.get("dtype")), str(d.get("dtype"))))
    raw = base64.b64decode(d.get("data") or "")
    arr = np.frombuffer(raw, dtype=dt)
    return arr.reshape(tuple(int(x) for x in d.get("shape") or [arr.size]))


@dataclass
class SurfaceData:
    """3D 表示の表面(モデルの単位)。"""

    key: str = ""
    unit: str = "mm"
    model: str = ""
    source: str = "geometry"  # "geometry"(粗い面のメッシュ)/ "mesh"(生成したメッシュの表面)
    points: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    triangles: np.ndarray = field(default_factory=lambda: np.zeros((0, 3), np.int64))
    face_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    faces: dict[int, dict[str, Any]] = field(default_factory=dict)  # 面の番号 → 種類・境界か・面積・重心
    patches: list[dict[str, Any]] = field(default_factory=list)  # [{name, kind, faces}]
    volumes: int = 0
    steps: int = 0
    mesh: dict[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.triangles.shape[0] == 0

    def patch_of(self) -> dict[int, str]:
        return {int(f): p["name"] for p in self.patches for f in p.get("faces", [])}

    def unassigned_faces(self) -> list[int]:
        pof = self.patch_of()
        return [t for t, f in sorted(self.faces.items()) if f.get("boundary") and t not in pof]

    def patch_rows(self) -> list[dict[str, Any]]:
        """パッチの一覧の行(面の数と面積の合計)。"""
        rows = []
        for p in self.patches:
            fs = [int(f) for f in p.get("faces", [])]
            area = sum(float(self.faces.get(f, {}).get("area", 0.0)) for f in fs)
            rows.append({"name": p["name"], "kind": p.get("kind", ""), "faces": fs, "area": area})
        return rows


def update_surface(prev: SurfaceData | None, data: dict[str, Any]) -> SurfaceData:
    """``gui_surface`` の応答から表示のデータを作る(配列がなければ前のものを使う)。"""
    s = SurfaceData(key=str(data.get("key") or ""), unit=str(data.get("unit") or "mm"),
                    model=str(data.get("model") or ""), source=str(data.get("source") or "geometry"),
                    patches=list(data.get("patches") or []), volumes=int(data.get("volumes") or 0),
                    steps=int(data.get("steps") or 0), mesh=dict(data.get("mesh") or {}))
    s.faces = {int(f["id"]): f for f in data.get("faces") or []}
    if data.get("unchanged") and prev is not None:
        s.points, s.triangles, s.face_ids = prev.points, prev.triangles, prev.face_ids
    else:
        p = unpack_array(data.get("points"))
        t = unpack_array(data.get("triangles"))
        f = unpack_array(data.get("face_ids"))
        if p is not None and t is not None and f is not None:
            s.points = p.astype(float, copy=False).reshape(-1, 3)
            s.triangles = t.astype(np.int64, copy=False).reshape(-1, 3)
            s.face_ids = f.astype(np.int64, copy=False).reshape(-1)
    return s


def face_border_lines(points: np.ndarray, tris: np.ndarray, fids: np.ndarray) -> np.ndarray:
    """面の境目の辺(隣り合う三角形の面の番号が違う辺と、開いた縁)。(L, 2) の点の番号。"""
    if tris.shape[0] == 0:
        return np.zeros((0, 2), np.int64)
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    f = np.concatenate([fids, fids, fids])
    e = np.sort(e, axis=1)
    order = np.lexsort((f, e[:, 1], e[:, 0]))
    e, f = e[order], f[order]
    key_change = np.ones(e.shape[0], bool)
    key_change[1:] = np.any(e[1:] != e[:-1], axis=1)
    starts = np.flatnonzero(key_change)
    counts = np.diff(np.append(starts, e.shape[0]))
    # 同じ辺で、面の番号の最小と最大が違えば境目
    fmin = f[starts]
    fmax = f[np.append(starts[1:], e.shape[0]) - 1]
    border = (fmin != fmax) | (counts == 1)
    return e[starts[border]]


def build_polydata(s: SurfaceData, patch_index: dict[str, int]) -> dict[str, Any]:
    """pyvista の PolyData: ``boundary``(境界の面。セルの値 patch_id = パッチの番号、付いていない面は -1、
    face_id)、``internal``(体積の間の面・体積のない面)、``lines``(面の境目)。"""
    import pyvista as pv

    out: dict[str, Any] = {"boundary": None, "internal": None, "lines": None}
    if s.empty:
        return out
    pof = s.patch_of()
    fid = s.face_ids
    bnd_faces = {t for t, f in s.faces.items() if f.get("boundary")}
    is_b = np.isin(fid, list(bnd_faces)) if bnd_faces else np.zeros(fid.shape, bool)
    pid_of_face = {t: patch_index.get(pof[t], -1) if t in pof else -1 for t in s.faces}
    pts = np.asarray(s.points, float)

    def poly(mask: np.ndarray):
        if not mask.any():
            return None
        tri = s.triangles[mask]
        cells = np.hstack([np.full((tri.shape[0], 1), 3, np.int64), tri]).ravel()
        m = pv.PolyData(pts, cells)
        m.cell_data["face_id"] = fid[mask].astype(np.int32)
        m.cell_data["patch_id"] = np.array([pid_of_face.get(int(t), -1) for t in fid[mask]], np.int32)
        return m

    out["boundary"] = poly(is_b)
    out["internal"] = poly(~is_b)
    lines = face_border_lines(pts, s.triangles, fid)  # 面の境目(メッシュのときは辺も別に描く)
    if lines.shape[0]:
        cells = np.hstack([np.full((lines.shape[0], 1), 2, np.int64), lines]).ravel()
        out["lines"] = pv.PolyData(pts, lines=cells)
    return out


def face_label_points(s: SurfaceData, only_boundary: bool = True) -> tuple[np.ndarray, list[str]]:
    """面の番号を描く位置と文字。位置は、その面の三角形の重心の平均に最も近い三角形の重心
    (曲面の重心は面の外にあることがあるので、面の上の点にする)。"""
    if s.empty:
        return np.zeros((0, 3)), []
    tc = s.points[s.triangles].mean(axis=1)
    fid = s.face_ids
    pts, labels = [], []
    for t, f in sorted(s.faces.items()):
        if only_boundary and not f.get("boundary"):
            continue
        m = fid == t
        if not m.any():
            continue
        c = tc[m]
        mean = c.mean(axis=0)
        pts.append(c[int(np.argmin(((c - mean) ** 2).sum(axis=1)))])
        labels.append(str(t))
    return np.array(pts, float).reshape(-1, 3), labels
