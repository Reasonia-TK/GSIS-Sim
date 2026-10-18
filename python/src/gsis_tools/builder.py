"""1 次要素(四面体・六面体・プリズム・ピラミッド)から :class:`PolyMesh` を組み立てる。

gmsh からの変換(:mod:`gsis_tools.gmsh_convert`)と、numpy で直接作る構造格子
(管の六面体メッシュ・平板メッシュ)の両方がこの関数を通る。

処理の流れ:

1. 要素ごとに体積の符号を調べ、負なら節点順を鏡映して向きを直す
2. 要素の面(外向き)を作り、節点の集合で照合して内部面と境界面に分ける
3. 内部面は ``owner < neighbour`` とし、owner から見て外向きの節点順にする
4. (任意)セル番号を逆 Cuthill–McKee 順に付け直す
5. 内部面を (owner, neighbour) の辞書順に、境界面をパッチ順(パッチ内は owner 順)に並べる
6. 境界面を、節点の集合で境界グループ(gmsh の 2 次元 Physical Group など)に割り当てる。
   どのグループにも属さない境界面があればエラー
7. どの面からも参照されない節点を除く
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from .pmesh import Patch, PMeshError, PolyMesh

logger = logging.getLogger(__name__)

# gmsh の節点順(docs: "Node ordering")で体積が正の要素について、右ねじの法線が
# 要素の外を向く面の節点順
ELEMENT_FACES: dict[str, tuple[tuple[int, ...], ...]] = {
    "tet": ((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)),
    "pyramid": ((0, 3, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)),
    "prism": ((0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (0, 3, 5, 2), (1, 2, 5, 4)),
    "hex": ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (0, 4, 7, 3), (1, 2, 6, 5)),
}
ELEMENT_NODES = {"tet": 4, "pyramid": 5, "prism": 6, "hex": 8}
# 向きを反転させる節点の並べ替え(鏡映)
ELEMENT_MIRROR = {
    "tet": (0, 2, 1, 3),
    "pyramid": (0, 3, 2, 1, 4),
    "prism": (0, 2, 1, 3, 5, 4),
    "hex": (0, 3, 2, 1, 4, 7, 6, 5),
}
PATCH_KIND_DEFAULTS = {"wall": "wall", "inlet": "inlet", "outlet": "outlet", "symmetry": "symmetry"}


def guess_patch_kind(name: str) -> str:
    """パッチ名から kind を推定する(参考情報。境界条件の種類は TOML で決める)。"""
    low = name.lower()
    if low in PATCH_KIND_DEFAULTS:
        return PATCH_KIND_DEFAULTS[low]
    if low.startswith("wall"):
        return "wall"
    if low.startswith("sym"):
        return "symmetry"
    if low.startswith("inlet"):
        return "inlet"
    if low.startswith("outlet"):
        return "outlet"
    return "patch"


@dataclass
class BoundaryGroup:
    """境界面のグループ(1 つのパッチになる)。

    ``faces`` は節点番号(``points`` の添字、0 始まり)の配列のリスト。各配列は
    (m, 3)(三角形)か (m, 4)(四角形)。節点の並び順は問わない(集合で照合する)。
    """

    name: str
    kind: str | None = None
    faces: list[np.ndarray] = field(default_factory=list)


def _face_geometry_fixed(P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """節点数が揃った面 (n, k, 3) の面積ベクトルと面中心(pmesh §4 の定義)。"""
    k = P.shape[1]
    if k == 3:
        S = 0.5 * np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        return S, P.mean(axis=1)
    xbar = P.mean(axis=1)
    Pn = np.roll(P, -1, axis=1)
    nk = np.cross(P - xbar[:, None], Pn - xbar[:, None])
    S = 0.5 * nk.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        nhat = S / np.linalg.norm(S, axis=1)[:, None]
        w = np.einsum("nkj,nj->nk", nk, nhat)
        ck = (P + Pn + xbar[:, None]) / 3.0
        xf = (w[..., None] * ck).sum(axis=1) / w.sum(axis=1)[:, None]
    return S, xf


def element_volumes(points: np.ndarray, etype: str, conn: np.ndarray) -> np.ndarray:
    """要素の符号付き体積(外向きの面から pmesh §4 と同じ方法で計算)。"""
    conn = np.asarray(conn, dtype=np.int64)
    c = points[conn].mean(axis=1)
    vol = np.zeros(conn.shape[0])
    for face in ELEMENT_FACES[etype]:
        S, xf = _face_geometry_fixed(points[conn[:, face]])
        vol += np.einsum("ij,ij->i", S, xf - c) / 3.0
    return vol


def _reverse_padded(nodes: np.ndarray, sizes: np.ndarray, mask: np.ndarray) -> None:
    """-1 埋めの面 (F, 4) のうち mask の行の節点順を反転する(先頭の節点は残す)。"""
    t = mask & (sizes == 3)
    nodes[t, 1], nodes[t, 2] = nodes[t, 2].copy(), nodes[t, 1].copy()
    q = mask & (sizes == 4)
    nodes[q, 1], nodes[q, 3] = nodes[q, 3].copy(), nodes[q, 1].copy()


def _pad4(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.int64)
    if arr.ndim != 2 or arr.shape[1] not in (3, 4):
        raise PMeshError(f"境界面の配列は (m, 3) か (m, 4) にしてください: {arr.shape}")
    if arr.shape[1] == 4:
        return arr
    return np.concatenate([arr, np.full((arr.shape[0], 1), -1, dtype=np.int64)], axis=1)


def _sorted_keys(nodes: np.ndarray) -> np.ndarray:
    """面の照合キー(節点番号を昇順に並べたもの。三角形は先頭が -1)。"""
    return np.sort(nodes, axis=1)


def rcm_permutation(n_cells: int, owner: np.ndarray, neighbour: np.ndarray) -> np.ndarray:
    """セルの隣接グラフの逆 Cuthill–McKee 順。戻り値 ``perm[new] = old``。"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import reverse_cuthill_mckee

    if neighbour.size == 0:
        return np.arange(n_cells)
    o = np.asarray(owner, dtype=np.int64)
    n = np.asarray(neighbour, dtype=np.int64)
    data = np.ones(2 * n.size, dtype=np.int8)
    A = coo_matrix((data, (np.concatenate([o, n]), np.concatenate([n, o]))), shape=(n_cells, n_cells)).tocsr()
    return np.asarray(reverse_cuthill_mckee(A, symmetric_mode=True), dtype=np.int64)


def polymesh_from_elements(
    points: np.ndarray,
    elements: Sequence[tuple[str, np.ndarray]],
    boundary: Sequence[BoundaryGroup],
    *,
    renumber: str | None = "rcm",
    fix_orientation: bool = True,
    metadata: dict[str, str] | None = None,
    return_cell_map: bool = False,
) -> PolyMesh | tuple[PolyMesh, np.ndarray]:
    """要素と境界グループから :class:`PolyMesh` を作る。

    Parameters
    ----------
    points:
        節点座標 (n, 3)。
    elements:
        ``(種類, 接続)`` の並び。種類は ``"tet"``・``"pyramid"``・``"prism"``・``"hex"``、
        接続は gmsh の節点順の (要素数, 節点数) 配列(``points`` の添字)。
        セルは(番号の付け直しをしなければ)この並びの順に番号が付く。
    boundary:
        境界グループ。並び順がパッチの順になる。
    renumber:
        ``"rcm"`` ならセル番号を逆 Cuthill–McKee 順に付け直す。``None`` なら要素の順のまま。
    fix_orientation:
        体積が負の要素の節点順を鏡映して直す(直した数はログに出す)。False なら負の要素はエラー。
    return_cell_map:
        True なら ``(mesh, cell_to_element)`` を返す。``cell_to_element[c]`` はセル c の元の要素番号
        (``elements`` を連結した通し番号)。
    """
    points = np.ascontiguousarray(points, dtype=np.float64).reshape(-1, 3)
    n_points = points.shape[0]

    # --- 1, 2: 要素の面(外向き)---
    face_nodes: list[np.ndarray] = []
    face_sizes: list[np.ndarray] = []
    face_cells: list[np.ndarray] = []
    n_cells = 0
    n_flipped = 0
    for etype, conn in elements:
        if etype not in ELEMENT_FACES:
            raise PMeshError(f"未対応の要素の種類です: {etype!r}")
        conn = np.asarray(conn, dtype=np.int64).reshape(-1, ELEMENT_NODES[etype])
        n = conn.shape[0]
        if n == 0:
            continue
        if conn.min() < 0 or conn.max() >= n_points:
            raise PMeshError(f"{etype} の接続に範囲外の節点番号があります")
        vol = element_volumes(points, etype, conn)
        if not np.all(np.isfinite(vol)) or np.any(vol == 0.0):
            bad = np.flatnonzero(~np.isfinite(vol) | (vol == 0.0))
            raise PMeshError(f"体積が 0 の {etype} 要素があります({bad.size} 個。要素 {bad[:5].tolist()})")
        neg = vol < 0
        if np.any(neg):
            if not fix_orientation:
                raise PMeshError(f"体積が負の {etype} 要素があります({int(neg.sum())} 個)")
            conn = conn.copy()
            conn[neg] = conn[neg][:, list(ELEMENT_MIRROR[etype])]
            n_flipped += int(neg.sum())
        cells = n_cells + np.arange(n, dtype=np.int64)
        for face in ELEMENT_FACES[etype]:
            fnodes = conn[:, face]
            face_nodes.append(_pad4(fnodes))
            face_sizes.append(np.full(n, len(face), dtype=np.int64))
            face_cells.append(cells)
        n_cells += n
    if n_cells == 0:
        raise PMeshError("3 次元の要素がありません")
    if n_flipped:
        logger.warning("体積が負の要素 %d 個の節点順を直しました", n_flipped)

    F = np.concatenate(face_nodes)
    Fsize = np.concatenate(face_sizes)
    Fcell = np.concatenate(face_cells)

    # --- 照合: 同じ節点集合の面の組 ---
    keys = _sorted_keys(F)
    order = np.lexsort(keys.T[::-1])
    ks = keys[order]
    new_run = np.ones(ks.shape[0], dtype=bool)
    new_run[1:] = np.any(ks[1:] != ks[:-1], axis=1)
    starts = np.flatnonzero(new_run)
    lens = np.diff(np.append(starts, ks.shape[0]))
    if np.any(lens > 2):
        bad = order[starts[lens > 2]]
        raise PMeshError(
            f"3 つ以上の要素が共有する面があります({int((lens > 2).sum())} 面。例: 節点 {F[bad[0]].tolist()})"
        )
    fa = order[starts[lens == 2]]
    fb = order[starts[lens == 2] + 1]
    ca, cb = Fcell[fa], Fcell[fb]
    if np.any(ca == cb):
        raise PMeshError("同じ要素の 2 つの面が一致しています(つぶれた要素)")
    swap = ca > cb
    own = np.where(swap, cb, ca)
    nei = np.where(swap, ca, cb)
    fown = np.where(swap, fb, fa)  # owner 側の面(owner から見て外向き)
    int_nodes = F[fown].copy()
    int_sizes = Fsize[fown]

    bf = order[starts[lens == 1]]
    bnd_nodes = F[bf].copy()
    bnd_sizes = Fsize[bf]
    bown = Fcell[bf]

    # --- 4: セル番号の付け直し ---
    if renumber == "rcm":
        perm = rcm_permutation(n_cells, own, nei)
    elif renumber is None:
        perm = np.arange(n_cells)
    else:
        raise ValueError(f"未知の renumber: {renumber!r}")
    new_id = np.empty(n_cells, dtype=np.int64)
    new_id[perm] = np.arange(n_cells)
    own, nei, bown = new_id[own], new_id[nei], new_id[bown]
    flip = own > nei
    if np.any(flip):
        own, nei = np.where(flip, nei, own), np.where(flip, own, nei)
        _reverse_padded(int_nodes, int_sizes, flip)

    # --- 5: 内部面の上三角順 ---
    o = np.lexsort((nei, own))
    own, nei, int_nodes, int_sizes = own[o], nei[o], int_nodes[o], int_sizes[o]

    # --- 6: 境界面をグループに割り当てる ---
    nb = bnd_nodes.shape[0]
    bkeys = _sorted_keys(bnd_nodes)
    gk_list, gid_list = [], []
    for gi, grp in enumerate(boundary):
        for arr in grp.faces:
            a = _pad4(arr)
            if a.shape[0] == 0:
                continue
            gk_list.append(_sorted_keys(a))
            gid_list.append(np.full(a.shape[0], gi, dtype=np.int64))
    if gk_list:
        gkeys = np.concatenate(gk_list)
        gids = np.concatenate(gid_list)
    else:
        gkeys = np.zeros((0, 4), dtype=np.int64)
        gids = np.zeros(0, dtype=np.int64)
    allk = np.concatenate([bkeys, gkeys])
    if allk.shape[0]:
        uniq, inv = np.unique(allk, axis=0, return_inverse=True)
        inv = np.asarray(inv).reshape(-1)
    else:
        uniq, inv = np.zeros((0, 4), dtype=np.int64), np.zeros(0, dtype=np.int64)
    binv, ginv = inv[:nb], inv[nb:]
    if gids.size:
        so = np.lexsort((gids, ginv))
        k_s, g_s = ginv[so], gids[so]
        conflict = (k_s[1:] == k_s[:-1]) & (g_s[1:] != g_s[:-1])
        if np.any(conflict):
            i = int(np.flatnonzero(conflict)[0])
            names = (boundary[int(g_s[i])].name, boundary[int(g_s[i + 1])].name)
            raise PMeshError(
                f"複数の境界グループに属する面があります({int(conflict.sum())} 面。例: {names[0]!r} と {names[1]!r})"
            )
    key_group = np.full(uniq.shape[0], -1, dtype=np.int64)
    key_group[ginv] = gids
    bpatch = key_group[binv]
    if np.any(bpatch < 0):
        miss = np.flatnonzero(bpatch < 0)
        P = points[np.where(bnd_nodes[miss[0]] >= 0, bnd_nodes[miss[0]], bnd_nodes[miss[0], 0])]
        raise PMeshError(
            f"どの境界グループにも属さない境界面があります({miss.size} 面。"
            f"例: 中心 {np.round(P.mean(axis=0), 9).tolist()})。2 次元の Physical Group を確認してください"
        )
    on_bnd = np.zeros(uniq.shape[0], dtype=bool)
    on_bnd[binv] = True
    extra = ~on_bnd[ginv]
    if np.any(extra):
        per = np.bincount(gids[extra], minlength=len(boundary))
        detail = ", ".join(f"{boundary[i].name}: {int(c)}" for i, c in enumerate(per) if c)
        logger.warning("境界面ではない面が境界グループに含まれていたので無視しました(%s)", detail)

    bo = np.lexsort((bown, bpatch))
    bnd_nodes, bnd_sizes, bown, bpatch = bnd_nodes[bo], bnd_sizes[bo], bown[bo], bpatch[bo]
    counts = np.bincount(bpatch, minlength=len(boundary))

    n_int = own.shape[0]
    patches = []
    start = n_int
    for gi, grp in enumerate(boundary):
        kind = grp.kind or guess_patch_kind(grp.name)
        c = int(counts[gi])
        if c == 0:
            logger.warning("境界グループ %r に境界面がありません(面の数 0 のパッチになります)", grp.name)
        patches.append(Patch(grp.name, kind, start, c))
        start += c

    # --- 7: 節点の詰め直し ---
    allf = np.concatenate([int_nodes, bnd_nodes])
    sizes = np.concatenate([int_sizes, bnd_sizes])
    valid = allf >= 0
    used = np.zeros(n_points, dtype=bool)
    used[allf[valid]] = True
    new_pid = np.cumsum(used) - 1
    face_nodes_flat = new_pid[allf[valid]]
    offsets = np.zeros(sizes.shape[0] + 1, dtype=np.int64)
    np.cumsum(sizes, out=offsets[1:])

    mesh = PolyMesh(
        points=points[used],
        face_offsets=offsets,
        face_nodes=face_nodes_flat,
        owner=np.concatenate([own, bown]),
        neighbour=nei,
        n_cells=n_cells,
        patches=patches,
        metadata=dict(metadata or {}),
    )
    if return_cell_map:
        return mesh, perm
    return mesh
