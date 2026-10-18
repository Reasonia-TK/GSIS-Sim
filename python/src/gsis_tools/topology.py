"""セルと面の対応(外向きの面の並び)とセルの種類の判定。VTU 出力と品質計算で使う。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .pmesh import PolyMesh

# セルの種類の番号
CELL_TET, CELL_PYRAMID, CELL_PRISM, CELL_HEX, CELL_POLY = 0, 1, 2, 3, 4
CELL_TYPE_NAMES = {
    CELL_TET: "tetrahedron",
    CELL_PYRAMID: "pyramid",
    CELL_PRISM: "prism",
    CELL_HEX: "hexahedron",
    CELL_POLY: "polyhedron",
}


@dataclass
class CellFaces:
    """セルごとの面(外向きの節点順)。エントリーはセル順に並ぶ。

    - ``cells[e]``, ``faces[e]``, ``sign[e]`` … エントリー e のセル・面・向き(owner 側 +1)
    - ``sizes[e]`` … 面の節点数、``nodes[ptr[e]:ptr[e+1]]`` … セルから見て外向きの節点順
    - ``cell_ptr[c]:cell_ptr[c+1]`` … セル c のエントリーの範囲
    """

    cells: np.ndarray
    faces: np.ndarray
    sign: np.ndarray
    sizes: np.ndarray
    ptr: np.ndarray
    nodes: np.ndarray
    cell_ptr: np.ndarray


def outward_cell_faces(mesh: PolyMesh) -> CellFaces:
    nf = mesh.n_faces
    ni = mesh.n_internal_faces
    cells = np.concatenate([mesh.owner, mesh.neighbour]).astype(np.int64)
    faces = np.concatenate([np.arange(nf), np.arange(ni)])
    sign = np.concatenate([np.ones(nf, dtype=np.int8), -np.ones(ni, dtype=np.int8)])
    order = np.argsort(cells, kind="stable")
    cells, faces, sign = cells[order], faces[order], sign[order]
    fsizes = mesh.face_sizes()
    sizes = fsizes[faces]
    ptr = np.zeros(sizes.shape[0] + 1, dtype=np.int64)
    np.cumsum(sizes, out=ptr[1:])
    e_of = np.repeat(np.arange(sizes.shape[0]), sizes)
    p = np.arange(ptr[-1]) - ptr[e_of]
    src0 = mesh.face_offsets[faces][e_of]
    rev = sign[e_of] < 0
    src = np.where(rev, src0 + (sizes[e_of] - 1 - p), src0 + p)
    nodes = mesh.face_nodes[src].astype(np.int64)
    cell_ptr = np.zeros(mesh.n_cells + 1, dtype=np.int64)
    np.cumsum(np.bincount(cells, minlength=mesh.n_cells), out=cell_ptr[1:])
    return CellFaces(cells, faces, sign, sizes, ptr, nodes, cell_ptr)


def classify_cells(mesh: PolyMesh, cf: CellFaces | None = None) -> np.ndarray:
    """面の構成からセルの種類を判定する(四面体・ピラミッド・プリズム・六面体・その他の多面体)。"""
    cf = cf or outward_cell_faces(mesh)
    n = mesh.n_cells
    nf = np.diff(cf.cell_ptr)
    n3 = np.bincount(cf.cells, weights=(cf.sizes == 3), minlength=n)
    n4 = np.bincount(cf.cells, weights=(cf.sizes == 4), minlength=n)
    t = np.full(n, CELL_POLY, dtype=np.int8)
    t[(nf == 4) & (n3 == 4)] = CELL_TET
    t[(nf == 5) & (n3 == 4) & (n4 == 1)] = CELL_PYRAMID
    t[(nf == 5) & (n3 == 2) & (n4 == 3)] = CELL_PRISM
    t[(nf == 6) & (n4 == 6)] = CELL_HEX
    return t


def padded_cell_faces(cf: CellFaces, cells: np.ndarray, n_faces: int, width: int = 4) -> np.ndarray:
    """セル(全て面の数が n_faces)の外向きの面を (n, n_faces, width) にまとめる(足りない所は -1)。"""
    e = cf.cell_ptr[cells][:, None] + np.arange(n_faces)[None, :]
    out = np.full(e.shape + (width,), -1, dtype=np.int64)
    sz = cf.sizes[e]
    for q in range(width):
        m = sz > q
        out[..., q][m] = cf.nodes[cf.ptr[e[m]] + q]
    return out
