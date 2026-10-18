"""ParaView で見るための VTU(VTK XML UnstructuredGrid)の書き出し。

- :func:`write_vtu` … セル(体積要素)とセルデータ
- :func:`write_boundary_vtu` … 境界面(パッチ番号などの面データ付き)

セルは面の構成から種類を判定し、四面体・ピラミッド・プリズム・六面体は VTK の標準の
セル型で、それ以外は ``VTK_POLYHEDRON``(``faces`` / ``faceoffsets`` 配列)で書く。
データは追記形式の生バイナリ(``header_type="UInt64"``)。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from xml.sax.saxutils import quoteattr

import numpy as np

from .pmesh import PolyMesh
from .topology import (
    CELL_HEX,
    CELL_POLY,
    CELL_PRISM,
    CELL_PYRAMID,
    CELL_TET,
    CellFaces,
    classify_cells,
    outward_cell_faces,
    padded_cell_faces,
)

VTK_TRIANGLE, VTK_POLYGON, VTK_QUAD = 5, 7, 9
VTK_TETRA, VTK_HEXAHEDRON, VTK_WEDGE, VTK_PYRAMID, VTK_POLYHEDRON = 10, 12, 13, 14, 42

_VTK_TYPE_NAMES = {
    np.dtype("float64"): "Float64",
    np.dtype("float32"): "Float32",
    np.dtype("int64"): "Int64",
    np.dtype("int32"): "Int32",
    np.dtype("int16"): "Int16",
    np.dtype("int8"): "Int8",
    np.dtype("uint8"): "UInt8",
    np.dtype("uint16"): "UInt16",
    np.dtype("uint32"): "UInt32",
    np.dtype("uint64"): "UInt64",
}


def _as_vtk_array(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a)
    if a.dtype == np.bool_:
        a = a.astype(np.uint8)
    if a.dtype.kind == "f" and a.dtype != np.float32:
        a = a.astype(np.float64)
    if a.dtype not in _VTK_TYPE_NAMES:
        a = a.astype(np.float64 if a.dtype.kind == "f" else np.int64)
    return np.ascontiguousarray(a.astype(a.dtype.newbyteorder("<")))


class _Appended:
    """追記データ(各ブロックの先頭に UInt64 のバイト数)。"""

    def __init__(self) -> None:
        self.blocks: list[bytes] = []
        self.offset = 0

    def add(self, a: np.ndarray) -> int:
        data = a.tobytes()
        off = self.offset
        self.blocks.append(np.uint64(len(data)).astype("<u8").tobytes())
        self.blocks.append(data)
        self.offset += 8 + len(data)
        return off


def _data_array(app: _Appended, name: str, a: np.ndarray) -> str:
    a = _as_vtk_array(a)
    ncomp = 1 if a.ndim == 1 else int(np.prod(a.shape[1:]))
    off = app.add(a)
    return (
        f'<DataArray type="{_VTK_TYPE_NAMES[a.dtype.newbyteorder("=")]}" Name={quoteattr(name)} '
        f'NumberOfComponents="{ncomp}" format="appended" offset="{off}"/>'
    )


def _write_unstructured(
    path: Path,
    points: np.ndarray,
    connectivity: np.ndarray,
    offsets: np.ndarray,
    types: np.ndarray,
    cell_data: Mapping[str, np.ndarray],
    faces: np.ndarray | None = None,
    faceoffsets: np.ndarray | None = None,
    comment: str | None = None,
) -> Path:
    n_cells = types.shape[0]
    app = _Appended()
    xml = ['<?xml version="1.0"?>']
    if comment:
        xml.append("<!-- " + comment.replace("--", "- -") + " -->")
    xml += [
        '<VTKFile type="UnstructuredGrid" version="1.0" byte_order="LittleEndian" header_type="UInt64">',
        "<UnstructuredGrid>",
        f'<Piece NumberOfPoints="{points.shape[0]}" NumberOfCells="{n_cells}">',
        "<Points>",
        _data_array(app, "Points", np.asarray(points, dtype=np.float64).reshape(-1, 3)),
        "</Points>",
        "<Cells>",
        _data_array(app, "connectivity", np.asarray(connectivity, dtype=np.int64)),
        _data_array(app, "offsets", np.asarray(offsets, dtype=np.int64)),
        _data_array(app, "types", np.asarray(types, dtype=np.uint8)),
    ]
    if faces is not None:
        xml.append(_data_array(app, "faces", np.asarray(faces, dtype=np.int64)))
        xml.append(_data_array(app, "faceoffsets", np.asarray(faceoffsets, dtype=np.int64)))
    xml.append("</Cells>")
    if cell_data:
        xml.append("<CellData>")
        for name, a in cell_data.items():
            a = np.asarray(a)
            if a.shape[0] != n_cells:
                raise ValueError(f"セルデータ {name!r} の長さ {a.shape[0]} がセル数 {n_cells} と一致しません")
            xml.append(_data_array(app, name, a))
        xml.append("</CellData>")
    xml += ["</Piece>", "</UnstructuredGrid>", '<AppendedData encoding="raw">']
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(("\n".join(xml) + "\n_").encode("utf-8"))
        for b in app.blocks:
            fh.write(b)
        fh.write(b"\n</AppendedData>\n</VTKFile>\n")
    return path


def _opposite_nodes(base: np.ndarray, sides: np.ndarray) -> np.ndarray:
    """柱状のセル(プリズム・六面体)で、底面の各節点と辺で結ばれた反対側の節点。

    ``base`` (n, k) は底面の節点、``sides`` (n, m, 4) はそれ以外の面(-1 埋め)。
    見つからなければ -1。
    """
    n, k = base.shape
    opp = np.full((n, k), -1, dtype=np.int64)
    in_base = np.zeros(sides.shape, dtype=bool)
    for i in range(k):
        in_base |= sides == base[:, i, None, None]
    for j in range(sides.shape[1]):
        f = sides[:, j, :]
        quad = f[:, 3] >= 0
        ib = in_base[:, j, :]
        for pos in range(4):
            nx, pv = (pos + 1) % 4, (pos - 1) % 4
            to_next = quad & ib[:, pos] & ~ib[:, nx]
            to_prev = quad & ib[:, pos] & ~ib[:, pv]
            for i in range(k):
                hit = f[:, pos] == base[:, i]
                s = hit & to_next
                opp[s, i] = f[s, nx]
                s = hit & to_prev
                opp[s, i] = f[s, pv]
    return opp


def _ranges(starts: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """``concatenate([arange(s, s + l) for s, l in zip(starts, lengths)])`` のベクトル版。"""
    starts = np.asarray(starts, dtype=np.int64)
    lengths = np.asarray(lengths, dtype=np.int64)
    excl = np.cumsum(lengths) - lengths
    return np.repeat(starts, lengths) + (np.arange(int(lengths.sum())) - np.repeat(excl, lengths))


def _all_distinct(a: np.ndarray) -> np.ndarray:
    s = np.sort(a, axis=1)
    return np.all(s[:, 1:] != s[:, :-1], axis=1) & np.all(a >= 0, axis=1)


def vtk_cells(mesh: PolyMesh, cf: CellFaces | None = None, ctype: np.ndarray | None = None):
    """VTK のセル表現(connectivity, offsets, types, faces, faceoffsets)。多面体がなければ faces は None。"""
    cf = cf or outward_cell_faces(mesh)
    ctype = classify_cells(mesh, cf) if ctype is None else ctype.copy()
    n = mesh.n_cells
    conn_rows: dict[int, np.ndarray] = {}
    vtk_type = np.full(n, VTK_POLYHEDRON, dtype=np.uint8)

    # 四面体: 外向きの面 (a, b, c) を (a, c, b) にすると法線が残りの節点を向く
    idx = np.flatnonzero(ctype == CELL_TET)
    if idx.size:
        F = padded_cell_faces(cf, idx, 4)
        b = F[:, 0, :3]
        f1 = F[:, 1, :3]
        apex_mask = ~np.any(f1[:, :, None] == b[:, None, :], axis=2)
        ok = apex_mask.sum(axis=1) == 1
        apex = np.where(ok, f1[np.arange(idx.size), np.argmax(apex_mask, axis=1)], -1)
        rows = np.stack([b[:, 0], b[:, 2], b[:, 1], apex], axis=1)
        ok &= _all_distinct(rows)
        conn_rows[CELL_TET] = (idx[ok], rows[ok])
        ctype[idx[~ok]] = CELL_POLY

    # ピラミッド: 底面(四角形)を反転 + 頂点
    idx = np.flatnonzero(ctype == CELL_PYRAMID)
    if idx.size:
        F = padded_cell_faces(cf, idx, 5)
        qpos = np.argmax(F[:, :, 3] >= 0, axis=1)
        q = F[np.arange(idx.size), qpos, :]
        tpos = (qpos + 1) % 5
        t = F[np.arange(idx.size), tpos, :3]
        apex_mask = ~np.any(t[:, :, None] == q[:, None, :], axis=2)
        ok = apex_mask.sum(axis=1) == 1
        apex = np.where(ok, t[np.arange(idx.size), np.argmax(apex_mask, axis=1)], -1)
        rows = np.stack([q[:, 0], q[:, 3], q[:, 2], q[:, 1], apex], axis=1)
        ok &= _all_distinct(rows)
        conn_rows[CELL_PYRAMID] = (idx[ok], rows[ok])
        ctype[idx[~ok]] = CELL_POLY

    # プリズム: VTK_WEDGE の参照座標は gmsh と同じで、底面 (0,1,2) の法線は反対側の三角形
    # (3,4,5) を向く。外向きの三角形 (a, b, c) を (a, c, b) にし、3,4,5 はそれぞれの反対側
    idx = np.flatnonzero(ctype == CELL_PRISM)
    if idx.size:
        F = padded_cell_faces(cf, idx, 5)
        tpos = np.argmax(F[:, :, 3] < 0, axis=1)
        t = F[np.arange(idx.size), tpos, :3]
        base = np.stack([t[:, 0], t[:, 2], t[:, 1]], axis=1)
        opp = _opposite_nodes(base, F)
        rows = np.concatenate([base, opp], axis=1)
        ok = _all_distinct(rows)
        conn_rows[CELL_PRISM] = (idx[ok], rows[ok])
        ctype[idx[~ok]] = CELL_POLY

    # 六面体: 底面を反転(法線が反対の面を向く)+ 反対側の節点
    idx = np.flatnonzero(ctype == CELL_HEX)
    if idx.size:
        F = padded_cell_faces(cf, idx, 6)
        q = F[:, 0, :]
        base = np.stack([q[:, 0], q[:, 3], q[:, 2], q[:, 1]], axis=1)
        opp = _opposite_nodes(base, F[:, 1:, :])
        rows = np.concatenate([base, opp], axis=1)
        ok = _all_distinct(rows)
        conn_rows[CELL_HEX] = (idx[ok], rows[ok])
        ctype[idx[~ok]] = CELL_POLY

    vtk_of = {CELL_TET: VTK_TETRA, CELL_PYRAMID: VTK_PYRAMID, CELL_PRISM: VTK_WEDGE, CELL_HEX: VTK_HEXAHEDRON}
    npts = np.zeros(n, dtype=np.int64)
    for t, (ids, rows) in conn_rows.items():
        vtk_type[ids] = vtk_of[t]
        npts[ids] = rows.shape[1]

    # 多面体: connectivity はセルの節点(重複なし)、faces は [面数, (節点数, 節点...)...]
    poly = np.flatnonzero(ctype == CELL_POLY)
    faces = faceoffsets = None
    poly_nodes: dict[str, np.ndarray] = {}
    if poly.size:
        e_cnt = np.diff(cf.cell_ptr)[poly]  # セルごとの面の数
        ent = _ranges(cf.cell_ptr[poly], e_cnt)  # セル順のエントリー番号
        ent_cell = np.repeat(poly, e_cnt)
        sz = cf.sizes[ent]
        node_vals = cf.nodes[_ranges(cf.ptr[ent], sz)]
        pairs = np.unique(np.stack([np.repeat(ent_cell, sz), node_vals], axis=1), axis=0)
        npts[poly] = np.bincount(pairs[:, 0], minlength=n)[poly]
        poly_nodes["cell"] = pairs[:, 0]
        poly_nodes["node"] = pairs[:, 1]
        # faces ストリームの配置: セルの先頭に面数、各面の先頭に節点数
        first = np.cumsum(e_cnt) - e_cnt  # 各セルの最初のエントリー(ent の添字)
        ent_len = 1 + sz
        cell_len = 1 + np.add.reduceat(ent_len, first)
        cell_start = np.cumsum(cell_len) - cell_len
        excl = np.cumsum(ent_len) - ent_len
        ent_pos = np.repeat(cell_start, e_cnt) + 1 + (excl - np.repeat(excl[first], e_cnt))
        stream = np.empty(int(cell_len.sum()), dtype=np.int64)
        stream[cell_start] = e_cnt
        stream[ent_pos] = sz
        stream[_ranges(ent_pos + 1, sz)] = node_vals
        faces = stream
        faceoffsets = np.full(n, -1, dtype=np.int64)
        faceoffsets[poly] = cell_start + cell_len

    offsets = np.cumsum(npts)
    connectivity = np.empty(int(offsets[-1]) if n else 0, dtype=np.int64)
    starts = offsets - npts
    for t, (ids, rows) in conn_rows.items():
        k = rows.shape[1]
        connectivity[(starts[ids][:, None] + np.arange(k)[None, :]).ravel()] = rows.ravel()
    if poly.size:
        pc = poly_nodes["cell"]
        rank = np.arange(pc.size) - np.searchsorted(pc, pc, side="left")
        connectivity[starts[pc] + rank] = poly_nodes["node"]
    return connectivity, offsets, vtk_type, faces, faceoffsets


def write_vtu(
    path: str | Path,
    mesh: PolyMesh,
    cell_data: Mapping[str, np.ndarray] | None = None,
    *,
    include_volume: bool = True,
    all_polyhedra: bool = False,
) -> Path:
    """セルの VTU を書く。``cell_data`` は名前 → (n_cells,) または (n_cells, k) の配列。

    ``include_volume`` なら ``cell_volume``(pmesh §4 の定義)と ``cell_id`` を足す。
    ``all_polyhedra`` なら全てのセルを ``VTK_POLYHEDRON`` で書く(確認用)。
    """
    ctype = np.full(mesh.n_cells, CELL_POLY, dtype=np.int8) if all_polyhedra else None
    conn, offsets, types, faces, faceoffsets = vtk_cells(mesh, ctype=ctype)
    data: dict[str, np.ndarray] = {}
    if include_volume:
        data["cell_id"] = np.arange(mesh.n_cells, dtype=np.int64)
        data["cell_volume"] = mesh.geometry().cell_volumes
    if cell_data:
        data.update(cell_data)
    return _write_unstructured(
        Path(path), mesh.points, conn, offsets, types, data, faces, faceoffsets,
        comment=f"gsis_tools: cells={mesh.n_cells}",
    )


def write_boundary_vtu(
    path: str | Path,
    mesh: PolyMesh,
    face_data: Mapping[str, np.ndarray] | None = None,
) -> Path:
    """境界面の VTU を書く。面データとして ``patch_id``(``patches`` の添字)、``owner``、
    ``face_area`` と、``face_data``(名前 → (n_boundary_faces,) の配列)を持つ。

    パッチ名と番号の対応はファイル先頭のコメントにも書く。
    """
    ni, nf = mesh.n_internal_faces, mesh.n_faces
    fo = mesh.face_offsets
    nodes = mesh.face_nodes[fo[ni] : fo[nf]].astype(np.int64)
    sizes = np.diff(fo[ni:])
    used = np.unique(nodes)
    remap = np.full(mesh.n_points, -1, dtype=np.int64)
    remap[used] = np.arange(used.size)
    types = np.where(sizes == 3, VTK_TRIANGLE, np.where(sizes == 4, VTK_QUAD, VTK_POLYGON)).astype(np.uint8)
    g = mesh.geometry()
    data: dict[str, np.ndarray] = {
        "patch_id": mesh.boundary_patch_ids(),
        "owner": mesh.owner[ni:].astype(np.int64),
        "face_area": g.face_areas[ni:],
        "face_normal": g.face_normals[ni:],
    }
    if face_data:
        for k, v in face_data.items():
            data[k] = np.asarray(v)
    names = ", ".join(f"{i}={p.name}" for i, p in enumerate(mesh.patches))
    return _write_unstructured(
        Path(path), mesh.points[used], remap[nodes], np.cumsum(sizes), types, data,
        comment=f"gsis_tools boundary: patch_id {names}",
    )
