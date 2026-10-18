"""内部メッシュ形式 ``.pmesh``(v1)の読み書き・幾何量・検査。

形式・規約・幾何量の定義はすべて ``docs/pmesh_format.md`` に従う。

- :class:`PolyMesh` … 面ベース(owner / neighbour)の任意多面体メッシュ
- :func:`write_pmesh` / :func:`read_pmesh` … ファイルの読み書き(既定で :func:`check_mesh` を通す)
- :func:`face_geometry` / :func:`cell_geometry` / :func:`compute_geometry` … §4 の幾何量(numpy でベクトル化)
- :func:`check_mesh` … §3 の規約と §4 の検査
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MAGIC = b"PMESH\x00\x00\x00"
FORMAT_NAME = "pmesh"
FORMAT_VERSION = 1
ALIGNMENT = 64
LENGTH_UNITS = ("m",)
PATCH_KINDS = ("wall", "inlet", "outlet", "symmetry", "patch")
CLOSURE_TOLERANCE = 1e-10

# 形式で許される dtype(すべてリトルエンディアン)
DTYPES: dict[str, np.dtype] = {
    "float64": np.dtype("<f8"),
    "int64": np.dtype("<i8"),
    "int32": np.dtype("<i4"),
}
# 配列の並び(書き出し順)と、書き手が使う dtype(docs/pmesh_format.md §2 の例と同じ)
ARRAY_NAMES = ("points", "face_offsets", "face_nodes", "owner", "neighbour")
WRITE_DTYPES = {
    "points": "float64",
    "face_offsets": "int64",
    "face_nodes": "int32",
    "owner": "int32",
    "neighbour": "int32",
}
# 読み手が受け付ける dtype(座標は浮動小数点、番号は整数)
_READ_DTYPES = {
    "points": ("float64",),
    "face_offsets": ("int64", "int32"),
    "face_nodes": ("int32", "int64"),
    "owner": ("int32", "int64"),
    "neighbour": ("int32", "int64"),
}


class PMeshError(ValueError):
    """pmesh の形式違反・規約違反。"""


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------


@dataclass
class Patch:
    """境界パッチ。面番号 ``[start, start + count)`` を持つ。"""

    name: str
    kind: str
    start: int
    count: int

    @property
    def stop(self) -> int:
        return self.start + self.count

    @property
    def faces(self) -> slice:
        return slice(self.start, self.start + self.count)


@dataclass
class MeshGeometry:
    """§4 の定義で計算した幾何量。"""

    face_area_vectors: np.ndarray  # S_f (n_faces, 3)
    face_centres: np.ndarray  # x_f (n_faces, 3)
    cell_volumes: np.ndarray  # V_i (n_cells,)
    cell_centres: np.ndarray  # x_i (n_cells, 3)
    cell_closure: np.ndarray  # |Σ S_out| / Σ |S_f|(セルごと)

    @property
    def face_areas(self) -> np.ndarray:
        return np.linalg.norm(self.face_area_vectors, axis=1)

    @property
    def face_normals(self) -> np.ndarray:
        a = self.face_areas
        with np.errstate(invalid="ignore", divide="ignore"):
            return self.face_area_vectors / a[:, None]


@dataclass
class PolyMesh:
    """面ベースの任意多面体メッシュ(docs/pmesh_format.md の内容そのもの)。

    配列は生成時に形式の dtype(``points`` は float64、``face_offsets`` は int64、
    ``face_nodes`` / ``owner`` / ``neighbour`` は int32)へ変換する。
    幾何量は :meth:`geometry` で計算し、結果を保持する(配列を書き換えたら
    ``geometry(recompute=True)`` で計算し直す)。
    """

    points: np.ndarray
    face_offsets: np.ndarray
    face_nodes: np.ndarray
    owner: np.ndarray
    neighbour: np.ndarray
    n_cells: int
    patches: list[Patch]
    metadata: dict[str, str] = field(default_factory=dict)
    length_unit: str = "m"
    _geometry: MeshGeometry | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.points = np.ascontiguousarray(self.points, dtype=np.float64).reshape(-1, 3)
        self.face_offsets = np.ascontiguousarray(self.face_offsets, dtype=np.int64)
        self.face_nodes = np.ascontiguousarray(self.face_nodes, dtype=np.int32)
        self.owner = np.ascontiguousarray(self.owner, dtype=np.int32)
        self.neighbour = np.ascontiguousarray(self.neighbour, dtype=np.int32)
        self.n_cells = int(self.n_cells)
        self.patches = [p if isinstance(p, Patch) else Patch(**p) for p in self.patches]
        self.metadata = {str(k): v for k, v in (self.metadata or {}).items()}

    # --- 個数 ---
    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])

    @property
    def n_faces(self) -> int:
        return int(self.face_offsets.shape[0]) - 1

    @property
    def n_internal_faces(self) -> int:
        return int(self.neighbour.shape[0])

    @property
    def n_boundary_faces(self) -> int:
        return self.n_faces - self.n_internal_faces

    # --- 参照 ---
    def face_sizes(self) -> np.ndarray:
        """各面の節点数。"""
        return np.diff(self.face_offsets)

    def face(self, f: int) -> np.ndarray:
        """面 ``f`` の節点番号。"""
        return self.face_nodes[self.face_offsets[f] : self.face_offsets[f + 1]]

    def patch(self, name: str) -> Patch:
        for p in self.patches:
            if p.name == name:
                return p
        raise KeyError(f"パッチ '{name}' はありません(あるのは {[p.name for p in self.patches]})")

    def patch_names(self) -> list[str]:
        return [p.name for p in self.patches]

    def boundary_patch_ids(self) -> np.ndarray:
        """境界面(``n_internal_faces`` 以降)ごとのパッチ番号(``patches`` の添字)。"""
        ids = np.full(self.n_boundary_faces, -1, dtype=np.int32)
        for k, p in enumerate(self.patches):
            ids[p.start - self.n_internal_faces : p.stop - self.n_internal_faces] = k
        return ids

    def cell_face_counts(self) -> np.ndarray:
        """セルごとの面の数。"""
        counts = np.bincount(self.owner, minlength=self.n_cells)
        counts += np.bincount(self.neighbour, minlength=self.n_cells)
        return counts[: self.n_cells]

    def geometry(self, recompute: bool = False) -> MeshGeometry:
        """§4 の幾何量(初回に計算して保持する)。"""
        if self._geometry is None or recompute:
            self._geometry = compute_geometry(self)
        return self._geometry

    def copy(self) -> "PolyMesh":
        return PolyMesh(
            points=self.points.copy(),
            face_offsets=self.face_offsets.copy(),
            face_nodes=self.face_nodes.copy(),
            owner=self.owner.copy(),
            neighbour=self.neighbour.copy(),
            n_cells=self.n_cells,
            patches=[Patch(p.name, p.kind, p.start, p.count) for p in self.patches],
            metadata=dict(self.metadata),
            length_unit=self.length_unit,
        )

    def summary(self) -> str:
        """個数とパッチの一覧(1 行ずつ)。"""
        lines = [
            f"points={self.n_points} faces={self.n_faces} internal_faces={self.n_internal_faces} "
            f"boundary_faces={self.n_boundary_faces} cells={self.n_cells}",
        ]
        for p in self.patches:
            lines.append(f"  patch {p.name!r} kind={p.kind} start={p.start} count={p.count}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 幾何量(§4)
# ---------------------------------------------------------------------------


def face_geometry(
    points: np.ndarray, face_offsets: np.ndarray, face_nodes: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """面積ベクトル S_f と面中心 x_f(docs/pmesh_format.md §4)。

    三角形はそのまま、4 節点以上は節点平均を仮の中心とした扇形分割で計算する。
    面積が 0 の面では面中心が NaN になる(検査でエラーになる)。
    """
    points = np.asarray(points, dtype=np.float64)
    offsets = np.asarray(face_offsets, dtype=np.int64)
    nodes = np.asarray(face_nodes, dtype=np.int64)
    n_faces = offsets.shape[0] - 1
    S = np.zeros((n_faces, 3))
    xf = np.zeros((n_faces, 3))
    if n_faces == 0:
        return S, xf
    sizes = np.diff(offsets)
    starts = offsets[:-1]
    if np.any(sizes < 3):
        raise PMeshError("3 節点未満の面があるため幾何量を計算できません")

    # n > 3 の面: 扇形分割(全ての面について計算し、三角形は後で置き換える)
    fid = np.repeat(np.arange(n_faces), sizes)
    p = points[nodes]
    nxt = np.arange(nodes.shape[0]) + 1
    nxt[offsets[1:] - 1] = starts
    pn = p[nxt]
    xbar = np.add.reduceat(p, starts, axis=0) / sizes[:, None]
    xb = xbar[fid]
    nk = np.cross(p - xb, pn - xb)
    S = 0.5 * np.add.reduceat(nk, starts, axis=0)
    mag = np.linalg.norm(S, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        nhat = S / mag[:, None]
        w = np.einsum("ij,ij->i", nk, nhat[fid])
        ck = (p + pn + xb) / 3.0
        wsum = np.add.reduceat(w, starts)
        xf = np.add.reduceat(w[:, None] * ck, starts, axis=0) / wsum[:, None]

    # 三角形: 面積ベクトルと重心をそのまま使う
    tri = sizes == 3
    if np.any(tri):
        s0 = starts[tri]
        p0 = points[nodes[s0]]
        p1 = points[nodes[s0 + 1]]
        p2 = points[nodes[s0 + 2]]
        S[tri] = 0.5 * np.cross(p1 - p0, p2 - p0)
        xf[tri] = (p0 + p1 + p2) / 3.0
    return S, xf


def _cell_face_entries(
    n_faces: int, owner: np.ndarray, neighbour: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(セル, 面, 向き)の組。owner 側は +1、neighbour 側は -1。"""
    n_int = neighbour.shape[0]
    cells = np.concatenate([owner, neighbour]).astype(np.int64)
    faces = np.concatenate([np.arange(n_faces), np.arange(n_int)])
    sign = np.concatenate([np.ones(n_faces), -np.ones(n_int)])
    return cells, faces, sign


def cell_geometry(
    n_cells: int,
    owner: np.ndarray,
    neighbour: np.ndarray,
    S: np.ndarray,
    xf: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """セルの体積 V_i・重心 x_i・閉性の指標(docs/pmesh_format.md §4)。

    閉性の指標はセルごとの ``|Σ_f S_out| / Σ_f |S_f|``。
    """
    n_faces = S.shape[0]
    cells, faces, sign = _cell_face_entries(n_faces, owner, neighbour)
    nf = np.bincount(cells, minlength=n_cells).astype(np.float64)
    xfe = xf[faces]
    with np.errstate(invalid="ignore", divide="ignore"):
        cbar = np.stack(
            [np.bincount(cells, weights=xfe[:, k], minlength=n_cells) for k in range(3)], axis=1
        ) / nf[:, None]
        Sout = S[faces] * sign[:, None]
        v = np.einsum("ij,ij->i", Sout, xfe - cbar[cells]) / 3.0
        pc = 0.75 * xfe + 0.25 * cbar[cells]
        V = np.bincount(cells, weights=v, minlength=n_cells)
        xc = (
            np.stack(
                [np.bincount(cells, weights=v * pc[:, k], minlength=n_cells) for k in range(3)],
                axis=1,
            )
            / V[:, None]
        )
        sumS = np.stack(
            [np.bincount(cells, weights=Sout[:, k], minlength=n_cells) for k in range(3)], axis=1
        )
        sumA = np.bincount(cells, weights=np.linalg.norm(S, axis=1)[faces], minlength=n_cells)
        closure = np.linalg.norm(sumS, axis=1) / sumA
    return V, xc, closure


def compute_geometry(mesh: PolyMesh) -> MeshGeometry:
    """メッシュ全体の幾何量。"""
    S, xf = face_geometry(mesh.points, mesh.face_offsets, mesh.face_nodes)
    V, xc, closure = cell_geometry(mesh.n_cells, mesh.owner, mesh.neighbour, S, xf)
    return MeshGeometry(S, xf, V, xc, closure)


# ---------------------------------------------------------------------------
# 検査(§3, §4)
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """:func:`check_mesh` の結果。``errors`` が空なら規約を満たす。"""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_error(self, prefix: str = "メッシュの検査でエラー") -> None:
        if self.errors:
            raise PMeshError(prefix + ":\n  - " + "\n  - ".join(self.errors))

    def format(self) -> str:
        lines = ["検査: " + ("OK" if self.ok else f"エラー {len(self.errors)} 件")]
        lines += [f"  [エラー] {e}" for e in self.errors]
        lines += [f"  [警告] {w}" for w in self.warnings]
        for k, v in self.info.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


def _first(idx: np.ndarray, k: int = 5) -> str:
    idx = np.asarray(idx).ravel()
    s = ", ".join(str(int(i)) for i in idx[:k])
    return s + (", ..." if idx.size > k else "")


def check_mesh(
    mesh: PolyMesh, *, geometry: bool = True, closure_tol: float = CLOSURE_TOLERANCE
) -> CheckResult:
    """docs/pmesh_format.md §3(1・2・4・5・6・7)と §4 の検査。

    - エラー: 配列の長さ・番号の範囲、面の順序とパッチの範囲、``owner < neighbour`` と
      上三角順、面の節点数(3 以上)と重複、セルの面の数(4 以上)、パッチ名の重複と種類、
      セルの閉性、体積が正であること
    - 警告: 内部面の向き(``S_f·(x_N - x_O) <= 0`` の面の数)、どの面からも参照されない節点、
      面の数が 0 のパッチ、文字列でない metadata
    """
    r = CheckResult()
    E, W = r.errors, r.warnings

    # --- 配列の形と番号の範囲 ---
    pts = mesh.points
    if pts.ndim != 2 or pts.shape[1] != 3:
        E.append(f"points の形が (n, 3) ではありません: {pts.shape}")
        return r
    if not np.all(np.isfinite(pts)):
        E.append("points に有限でない値があります")
    if mesh.length_unit not in LENGTH_UNITS:
        E.append(f"length_unit が {LENGTH_UNITS} のいずれでもありません: {mesh.length_unit!r}")

    fo = mesh.face_offsets.astype(np.int64)
    fn = mesh.face_nodes.astype(np.int64)
    own = mesh.owner.astype(np.int64)
    nei = mesh.neighbour.astype(np.int64)
    n_points, n_cells = mesh.n_points, mesh.n_cells
    n_faces = fo.shape[0] - 1
    n_int = nei.shape[0]

    if fo.ndim != 1 or n_faces < 0:
        E.append("face_offsets の長さが 1 未満です")
        return r
    if fo[0] != 0:
        E.append(f"face_offsets[0] が 0 ではありません: {fo[0]}")
    if fo[-1] != fn.shape[0]:
        E.append(f"face_offsets[-1] ({fo[-1]}) が face_nodes の長さ ({fn.shape[0]}) と一致しません")
    sizes = np.diff(fo)
    if np.any(sizes < 0):
        E.append("face_offsets が単調増加ではありません")
        return r
    if own.shape[0] != n_faces:
        E.append(f"owner の長さ ({own.shape[0]}) が面の数 ({n_faces}) と一致しません")
    if n_int > n_faces:
        E.append(f"内部面の数 ({n_int}) が面の数 ({n_faces}) を超えています")
    if n_cells < 1:
        E.append(f"セルの数が 1 未満です: {n_cells}")
    if E:
        return r

    bad = np.flatnonzero((fn < 0) | (fn >= n_points))
    if bad.size:
        E.append(f"face_nodes に範囲外の節点番号があります({bad.size} 個。位置 {_first(bad)})")
    bad = np.flatnonzero((own < 0) | (own >= n_cells))
    if bad.size:
        E.append(f"owner に範囲外のセル番号があります({bad.size} 面。面 {_first(bad)})")
    bad = np.flatnonzero((nei < 0) | (nei >= n_cells))
    if bad.size:
        E.append(f"neighbour に範囲外のセル番号があります({bad.size} 面。面 {_first(bad)})")
    if E:
        return r

    # --- §3-4 面: 3 節点以上、同じ節点を重複して含まない ---
    bad = np.flatnonzero(sizes < 3)
    if bad.size:
        E.append(f"節点が 3 未満の面があります({bad.size} 面。面 {_first(bad)})")
    else:
        fid = np.repeat(np.arange(n_faces), sizes)
        order = np.lexsort((fn, fid))
        fs, ns = fid[order], fn[order]
        dup = np.flatnonzero((fs[1:] == fs[:-1]) & (ns[1:] == ns[:-1]))
        if dup.size:
            faces_dup = np.unique(fs[dup])
            E.append(f"同じ節点を重複して含む面があります({faces_dup.size} 面。面 {_first(faces_dup)})")

    # --- §3-2 内部面: owner < neighbour、(owner, neighbour) の辞書順 ---
    if n_int:
        o_i = own[:n_int]
        bad = np.flatnonzero(o_i >= nei)
        if bad.size:
            E.append(f"内部面で owner < neighbour になっていません({bad.size} 面。面 {_first(bad)})")
        if n_int > 1:
            o0, o1 = o_i[:-1], o_i[1:]
            n0, n1 = nei[:-1], nei[1:]
            bad = np.flatnonzero((o1 < o0) | ((o1 == o0) & (n1 < n0)))
            if bad.size:
                E.append(
                    f"内部面が (owner, neighbour) の辞書順に並んでいません({bad.size} 箇所。面 {_first(bad + 1)})"
                )
            same = np.count_nonzero((o1 == o0) & (n1 == n0))
            if same:
                r.info["同じセル対を結ぶ内部面の重複"] = int(same)

    # --- §3-1 境界面とパッチ ---
    names = [p.name for p in mesh.patches]
    if len(set(names)) != len(names):
        dups = sorted({n for n in names if names.count(n) > 1})
        E.append(f"パッチ名が重複しています: {dups}")
    for p in mesh.patches:
        if not isinstance(p.name, str) or not p.name:
            E.append(f"パッチ名が空か文字列ではありません: {p.name!r}")
        if p.kind not in PATCH_KINDS:
            E.append(f"パッチ '{p.name}' の kind が {PATCH_KINDS} のいずれでもありません: {p.kind!r}")
        if p.count < 0:
            E.append(f"パッチ '{p.name}' の count が負です: {p.count}")
        elif p.count == 0:
            W.append(f"パッチ '{p.name}' の面の数が 0 です")
    expect = n_int
    for p in mesh.patches:
        if p.start != expect:
            E.append(f"パッチ '{p.name}' の start ({p.start}) が {expect} ではありません(境界面を連続して覆っていない)")
            break
        expect = p.start + p.count
    else:
        if expect != n_faces:
            E.append(f"パッチが境界面を過不足なく覆っていません(最後のパッチの終わり {expect}、面の数 {n_faces})")
    if not mesh.patches and n_int != n_faces:
        E.append("境界面があるのにパッチがありません")

    # --- §3-5 セル: 4 面以上 ---
    nfc = np.bincount(own, minlength=n_cells) + np.bincount(nei, minlength=n_cells)
    bad = np.flatnonzero(nfc < 4)
    if bad.size:
        E.append(f"面が 4 未満のセルがあります({bad.size} セル。セル {_first(bad)})")

    # --- §3-6 参照されない節点(推奨事項なので警告) ---
    used = np.zeros(n_points, dtype=bool)
    used[fn] = True
    n_unused = int(n_points - np.count_nonzero(used))
    if n_unused:
        W.append(f"どの面からも参照されない節点があります({n_unused} 個)")

    # --- metadata は文字列のみ(参考情報なので警告。write_pmesh は文字列に直して書く)---
    for k, v in mesh.metadata.items():
        if not isinstance(v, str):
            W.append(f"metadata の値が文字列ではありません: {k!r}")

    r.info.update(
        {
            "n_points": n_points,
            "n_faces": n_faces,
            "n_internal_faces": n_int,
            "n_cells": n_cells,
        }
    )
    if E or not geometry:
        return r

    # --- §4 幾何の検査 ---
    g = mesh.geometry(recompute=True)
    finite = np.isfinite(g.cell_volumes) & np.all(np.isfinite(g.cell_centres), axis=1)
    bad = np.flatnonzero(~finite)
    if bad.size:
        E.append(f"体積または重心が有限でないセルがあります({bad.size} セル。セル {_first(bad)}。面積 0 の面など)")
    with np.errstate(invalid="ignore"):
        bad = np.flatnonzero(~(g.cell_closure <= closure_tol))
    if bad.size:
        vals = g.cell_closure[bad]
        worst = float(np.max(vals[np.isfinite(vals)])) if np.any(np.isfinite(vals)) else float("nan")
        E.append(
            f"閉じていないセルがあります({bad.size} セル。セル {_first(bad)}。"
            f"最大 |ΣS|/Σ|S| = {worst:.3e}、許容 {closure_tol:g})"
        )
    with np.errstate(invalid="ignore"):
        bad = np.flatnonzero(~(g.cell_volumes > 0))
    if bad.size:
        E.append(f"体積が正でないセルがあります({bad.size} セル。セル {_first(bad)})")
    if n_int:
        d = g.cell_centres[nei] - g.cell_centres[own[:n_int]]
        dot = np.einsum("ij,ij->i", g.face_area_vectors[:n_int], d)
        bad = np.flatnonzero(~(dot > 0))
        if bad.size:
            W.append(
                f"内部面の向きの検査 S_f·(x_N - x_O) > 0 に違反する面があります({bad.size} 面。面 {_first(bad)})"
            )
        r.info["内部面の向きの違反"] = int(bad.size)
    r.info["最大 |ΣS|/Σ|S|"] = float(np.nanmax(g.cell_closure)) if n_cells else 0.0
    r.info["体積の合計"] = float(np.sum(g.cell_volumes))
    return r


# ---------------------------------------------------------------------------
# 読み書き
# ---------------------------------------------------------------------------


def _align(n: int) -> int:
    return (n + ALIGNMENT - 1) // ALIGNMENT * ALIGNMENT


def _arrays_for_write(mesh: PolyMesh) -> dict[str, np.ndarray]:
    return {
        "points": np.ascontiguousarray(mesh.points, dtype=DTYPES[WRITE_DTYPES["points"]]),
        "face_offsets": np.ascontiguousarray(mesh.face_offsets, dtype=DTYPES[WRITE_DTYPES["face_offsets"]]),
        "face_nodes": np.ascontiguousarray(mesh.face_nodes, dtype=DTYPES[WRITE_DTYPES["face_nodes"]]),
        "owner": np.ascontiguousarray(mesh.owner, dtype=DTYPES[WRITE_DTYPES["owner"]]),
        "neighbour": np.ascontiguousarray(mesh.neighbour, dtype=DTYPES[WRITE_DTYPES["neighbour"]]),
    }


def default_metadata(source: str | None = None) -> dict[str, str]:
    from . import __version__

    md = {
        "generator": f"gsis_tools {__version__}",
        "created": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if source:
        md["source"] = source
    return md


def write_pmesh(
    path: str | Path,
    mesh: PolyMesh,
    *,
    check: bool = True,
    metadata: dict[str, str] | None = None,
) -> Path:
    """``.pmesh`` を書く。

    ``check=True`` なら書く前に :func:`check_mesh` を通し、エラーがあれば :class:`PMeshError`。
    ``metadata`` は ``mesh.metadata`` に上書きで足す(``generator`` と ``created`` は自動で入れる)。
    """
    path = Path(path)
    if check:
        res = check_mesh(mesh)
        res.raise_if_error("書き出し前の検査でエラー")
        for w in res.warnings:
            logger.warning("%s: %s", path.name, w)
    md = default_metadata()
    md.update(mesh.metadata)
    if metadata:
        md.update(metadata)
    md = {str(k): str(v) for k, v in md.items()}
    arrays = _arrays_for_write(mesh)

    def header(offsets: dict[str, int]) -> bytes:
        h = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "length_unit": mesh.length_unit,
            "n_points": mesh.n_points,
            "n_faces": mesh.n_faces,
            "n_internal_faces": mesh.n_internal_faces,
            "n_cells": mesh.n_cells,
            "patches": [
                {"name": p.name, "kind": p.kind, "start": int(p.start), "count": int(p.count)}
                for p in mesh.patches
            ],
            "arrays": {
                name: {
                    "dtype": WRITE_DTYPES[name],
                    "shape": list(arrays[name].shape),
                    "offset": int(offsets[name]),
                }
                for name in ARRAY_NAMES
            },
            "metadata": md,
        }
        return json.dumps(h, ensure_ascii=False, indent=2).encode("utf-8")

    # ヘッダの長さがオフセットの桁数に依存するので、変わらなくなるまで繰り返す
    offsets = {name: 0 for name in ARRAY_NAMES}
    for _ in range(16):
        hb = header(offsets)
        pos = _align(16 + len(hb))
        new = {}
        for name in ARRAY_NAMES:
            new[name] = pos
            pos = _align(pos + arrays[name].nbytes)
        if new == offsets:
            break
        offsets = new
    else:  # pragma: no cover
        raise RuntimeError("ヘッダの配置が決まりません")
    hb = header(offsets)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(MAGIC)
        fh.write(np.uint64(len(hb)).astype("<u8").tobytes())
        fh.write(hb)
        pos = 16 + len(hb)
        for name in ARRAY_NAMES:
            off = offsets[name]
            fh.write(b"\x00" * (off - pos))
            data = arrays[name].tobytes()
            fh.write(data)
            pos = off + len(data)
    return path


def _require_int(h: dict, key: str) -> int:
    v = h.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise PMeshError(f"ヘッダの {key!r} が整数ではありません: {v!r}")
    if v < 0:
        raise PMeshError(f"ヘッダの {key!r} が負です: {v}")
    return v


def read_header(path: str | Path) -> dict[str, Any]:
    """JSON ヘッダだけを読む。"""
    with open(path, "rb") as fh:
        head = fh.read(16)
        if len(head) < 16 or head[:8] != MAGIC:
            raise PMeshError(f"{path}: pmesh のマジックがありません")
        hlen = int(np.frombuffer(head[8:16], dtype="<u8")[0])
        hb = fh.read(hlen)
    if len(hb) != hlen:
        raise PMeshError(f"{path}: ヘッダが途中で切れています")
    try:
        return json.loads(hb.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PMeshError(f"{path}: JSON ヘッダを読めません: {exc}") from exc


def read_pmesh(path: str | Path, *, check: bool = True) -> PolyMesh:
    """``.pmesh`` を読む。

    形式の違反(マジック・版・dtype・形・オフセット)は常に :class:`PMeshError`。
    ``check=True`` なら :func:`check_mesh` を行い、エラーがあれば :class:`PMeshError`、
    警告はログに出す。
    """
    path = Path(path)
    buf = path.read_bytes()
    if len(buf) < 16 or buf[:8] != MAGIC:
        raise PMeshError(f"{path}: pmesh のマジックがありません")
    hlen = int(np.frombuffer(buf[8:16], dtype="<u8")[0])
    if 16 + hlen > len(buf):
        raise PMeshError(f"{path}: ヘッダが途中で切れています")
    try:
        h = json.loads(buf[16 : 16 + hlen].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PMeshError(f"{path}: JSON ヘッダを読めません: {exc}") from exc
    if not isinstance(h, dict):
        raise PMeshError(f"{path}: JSON ヘッダがオブジェクトではありません")
    if h.get("format") != FORMAT_NAME:
        raise PMeshError(f"{path}: format が {FORMAT_NAME!r} ではありません: {h.get('format')!r}")
    if h.get("version") != FORMAT_VERSION:
        raise PMeshError(f"{path}: 未知の version です: {h.get('version')!r}(対応は {FORMAT_VERSION})")
    unit = h.get("length_unit")
    if unit not in LENGTH_UNITS:
        raise PMeshError(f"{path}: 未知の length_unit です: {unit!r}")
    n_points = _require_int(h, "n_points")
    n_faces = _require_int(h, "n_faces")
    n_int = _require_int(h, "n_internal_faces")
    n_cells = _require_int(h, "n_cells")

    patches_raw = h.get("patches")
    if not isinstance(patches_raw, list):
        raise PMeshError(f"{path}: patches が配列ではありません")
    patches = []
    for p in patches_raw:
        if not isinstance(p, dict) or not {"name", "kind", "start", "count"} <= set(p):
            raise PMeshError(f"{path}: パッチの記述が不正です: {p!r}")
        patches.append(Patch(str(p["name"]), str(p["kind"]), _require_int(p, "start"), _require_int(p, "count")))

    arrays_h = h.get("arrays")
    if not isinstance(arrays_h, dict):
        raise PMeshError(f"{path}: arrays がありません")
    data: dict[str, np.ndarray] = {}
    for name in ARRAY_NAMES:
        a = arrays_h.get(name)
        if not isinstance(a, dict):
            raise PMeshError(f"{path}: 配列 {name!r} の記述がありません")
        dt = a.get("dtype")
        if dt not in DTYPES:
            raise PMeshError(f"{path}: 配列 {name!r} の dtype が未知です: {dt!r}")
        if dt not in _READ_DTYPES[name]:
            raise PMeshError(f"{path}: 配列 {name!r} に dtype {dt!r} は使えません")
        shape = a.get("shape")
        if not isinstance(shape, list) or not all(isinstance(s, int) and s >= 0 for s in shape):
            raise PMeshError(f"{path}: 配列 {name!r} の shape が不正です: {shape!r}")
        off = a.get("offset")
        if not isinstance(off, int) or off % ALIGNMENT != 0 or off < 16 + hlen:
            raise PMeshError(f"{path}: 配列 {name!r} の offset が不正です({ALIGNMENT} の倍数でヘッダより後): {off!r}")
        dtype = DTYPES[dt]
        count = int(np.prod(shape)) if shape else 1
        nbytes = count * dtype.itemsize
        if off + nbytes > len(buf):
            raise PMeshError(f"{path}: 配列 {name!r} がファイルの外にはみ出しています")
        data[name] = np.frombuffer(buf, dtype=dtype, count=count, offset=off).reshape(shape).copy()

    # 形の検査
    expected = {
        "points": [n_points, 3],
        "face_offsets": [n_faces + 1],
        "owner": [n_faces],
        "neighbour": [n_int],
    }
    for name, shp in expected.items():
        if list(data[name].shape) != shp:
            raise PMeshError(f"{path}: 配列 {name!r} の shape {list(data[name].shape)} が {shp} ではありません")
    if data["face_nodes"].ndim != 1:
        raise PMeshError(f"{path}: face_nodes が 1 次元ではありません")
    if n_faces + 1 > 0 and int(data["face_offsets"][-1]) != data["face_nodes"].shape[0]:
        raise PMeshError(f"{path}: face_offsets[-1] が face_nodes の長さと一致しません")
    # 配列同士の重なり
    spans = sorted(
        (int(arrays_h[n]["offset"]), int(arrays_h[n]["offset"]) + data[n].nbytes, n) for n in ARRAY_NAMES
    )
    for (s0, e0, n0), (s1, _e1, n1) in zip(spans[:-1], spans[1:]):
        if s1 < e0:
            raise PMeshError(f"{path}: 配列 {n0!r} と {n1!r} が重なっています")

    md_raw = h.get("metadata", {})  # 省略可。読み手は中身を解釈しない
    if not isinstance(md_raw, dict):
        raise PMeshError(f"{path}: metadata がオブジェクトではありません")
    md = {}
    for k, v in md_raw.items():
        if isinstance(v, str):
            md[k] = v
        else:
            logger.warning("%s: metadata の %r が文字列ではないので読み飛ばします", path.name, k)

    mesh = PolyMesh(
        points=data["points"],
        face_offsets=data["face_offsets"],
        face_nodes=data["face_nodes"],
        owner=data["owner"],
        neighbour=data["neighbour"],
        n_cells=n_cells,
        patches=patches,
        metadata=md,
        length_unit=unit,
    )
    if check:
        res = check_mesh(mesh)
        res.raise_if_error(f"{path}: 検査でエラー")
        for w in res.warnings:
            logger.warning("%s: %s", path.name, w)
    return mesh
