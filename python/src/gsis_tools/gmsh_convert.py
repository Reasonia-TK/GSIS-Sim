"""gmsh のモデル(API 上のもの)または ``.msh`` ファイルから :class:`PolyMesh` への変換。

- 対象は 1 次要素の四面体・六面体・プリズム・ピラミッド(高次要素はエラー)
- 境界面は 2 次元の Physical Group(名前)からパッチへ割り当てる。どのグループにも
  属さない境界面があればエラー(:func:`gsis_tools.builder.polymesh_from_elements`)
- 3 次元の Physical Group があれば、それに含まれる体積の要素だけをセルにする。
  なければ全ての体積の要素を使う

逆向き(要素 → gmsh の離散モデル → ``.msh``)の :func:`write_msh_from_elements` もある。
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

import numpy as np

from .builder import ELEMENT_NODES, BoundaryGroup, polymesh_from_elements
from .pmesh import PMeshError, PolyMesh

logger = logging.getLogger(__name__)

# gmsh の要素型番号 → 種類
GMSH_3D_TYPES = {4: "tet", 5: "hex", 6: "prism", 7: "pyramid"}
GMSH_TYPE_OF = {v: k for k, v in GMSH_3D_TYPES.items()}
GMSH_2D_TYPES = {2: 3, 3: 4}  # 三角形・四角形 → 節点数


def _gmsh():
    import gmsh

    return gmsh


@contextlib.contextmanager
def gmsh_session(model_name: str = "gsis", *, verbosity: int = 1, threads: int | None = None) -> Iterator[object]:
    """gmsh を使う区間。初期化されていなければ初期化し、終わったら終了する。

    すでに初期化されていれば、新しいモデルを作って使い、終わったら削除して元のモデルに戻す。
    """
    gmsh = _gmsh()
    owned = not gmsh.isInitialized()
    if owned:
        gmsh.initialize(["-nopopup"], readConfigFiles=False)
        gmsh.option.setNumber("General.Terminal", 1 if verbosity > 0 else 0)
        gmsh.option.setNumber("General.Verbosity", verbosity)
        prev = None
    else:
        prev = gmsh.model.getCurrent()
    if threads is not None:
        gmsh.option.setNumber("General.NumThreads", int(threads))
    gmsh.model.add(model_name)
    try:
        yield gmsh
    finally:
        if owned:
            gmsh.finalize()
        else:
            gmsh.model.remove()
            if prev:
                gmsh.model.setCurrent(prev)


def _node_index(gmsh) -> tuple[np.ndarray, np.ndarray]:
    """全節点の座標と、節点タグ → 添字の表。"""
    tags, coords, _ = gmsh.model.mesh.getNodes()
    tags = np.asarray(tags, dtype=np.int64)
    if tags.size == 0:
        raise PMeshError("gmsh のモデルに節点がありません(メッシュを生成しましたか)")
    index = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
    index[tags] = np.arange(tags.size)
    return np.asarray(coords, dtype=np.float64).reshape(-1, 3), index


def _map_nodes(index: np.ndarray, node_tags: np.ndarray) -> np.ndarray:
    t = np.asarray(node_tags, dtype=np.int64)
    if t.size and (t.max() >= index.size or np.any(index[t] < 0)):
        raise PMeshError("要素が未知の節点タグを参照しています")
    return index[t]


def _type_name(gmsh, etype: int) -> str:
    try:
        return gmsh.model.mesh.getElementProperties(int(etype))[0]
    except Exception:  # pragma: no cover
        return f"type {etype}"


def _volume_entities(gmsh) -> list[int]:
    groups = gmsh.model.getPhysicalGroups(3)
    if groups:
        ents: list[int] = []
        for _, ptag in groups:
            for e in gmsh.model.getEntitiesForPhysicalGroup(3, ptag):
                if int(e) not in ents:
                    ents.append(int(e))
        return ents
    return [int(tag) for _, tag in gmsh.model.getEntities(3)]


def elements_from_gmsh(gmsh=None) -> tuple[np.ndarray, list[tuple[str, np.ndarray]], list[BoundaryGroup]]:
    """現在の gmsh モデルから、節点・3 次元要素・境界グループ(2 次元 Physical Group)を取り出す。"""
    gmsh = gmsh or _gmsh()
    points, index = _node_index(gmsh)

    by_type: dict[str, list[np.ndarray]] = {}
    for ent in _volume_entities(gmsh):
        etypes, _etags, enodes = gmsh.model.mesh.getElements(3, ent)
        for t, nodes in zip(etypes, enodes):
            t = int(t)
            if t not in GMSH_3D_TYPES:
                raise PMeshError(
                    f"未対応の 3 次元要素です: {_type_name(gmsh, t)}(1 次の四面体・六面体・プリズム・ピラミッドのみ)"
                )
            kind = GMSH_3D_TYPES[t]
            by_type.setdefault(kind, []).append(_map_nodes(index, nodes).reshape(-1, ELEMENT_NODES[kind]))
    elements = [(k, np.concatenate(v)) for k, v in by_type.items()]
    if not elements:
        raise PMeshError("gmsh のモデルに 3 次元要素がありません")

    groups: dict[str, BoundaryGroup] = {}
    for _, ptag in gmsh.model.getPhysicalGroups(2):
        name = gmsh.model.getPhysicalName(2, ptag) or f"physical_{ptag}"
        grp = groups.setdefault(name, BoundaryGroup(name))
        for ent in gmsh.model.getEntitiesForPhysicalGroup(2, ptag):
            etypes, _etags, enodes = gmsh.model.mesh.getElements(2, int(ent))
            for t, nodes in zip(etypes, enodes):
                t = int(t)
                if t not in GMSH_2D_TYPES:
                    raise PMeshError(f"未対応の 2 次元要素です: {_type_name(gmsh, t)}(1 次の三角形・四角形のみ)")
                grp.faces.append(_map_nodes(index, nodes).reshape(-1, GMSH_2D_TYPES[t]))
    return points, elements, list(groups.values())


def _arrange_groups(
    groups: list[BoundaryGroup],
    patch_kinds: Mapping[str, str] | None,
    patch_order: Sequence[str] | None,
) -> list[BoundaryGroup]:
    if patch_kinds:
        for g in groups:
            if g.name in patch_kinds:
                g.kind = patch_kinds[g.name]
    if patch_order:
        names = [g.name for g in groups]
        missing = [n for n in patch_order if n not in names]
        if missing:
            raise PMeshError(f"patch_order にある名前の Physical Group がありません: {missing}")
        rest = [g for g in groups if g.name not in patch_order]
        groups = [groups[names.index(n)] for n in patch_order] + rest
    return groups


def polymesh_from_gmsh(
    *,
    patch_kinds: Mapping[str, str] | None = None,
    patch_order: Sequence[str] | None = None,
    renumber: str | None = "rcm",
    metadata: dict[str, str] | None = None,
) -> PolyMesh:
    """現在の gmsh モデル(初期化済み・メッシュ生成済み)を :class:`PolyMesh` に変換する。

    パッチは 2 次元 Physical Group の名前(名前がなければ ``physical_<タグ>``)。並びは
    ``patch_order``(省略時は Physical Group のタグ順)。kind は ``patch_kinds`` か、
    名前からの推定(:func:`gsis_tools.builder.guess_patch_kind`)。
    """
    points, elements, groups = elements_from_gmsh()
    groups = _arrange_groups(groups, patch_kinds, patch_order)
    return polymesh_from_elements(points, elements, groups, renumber=renumber, metadata=metadata)


def read_msh(
    path: str | Path,
    *,
    patch_kinds: Mapping[str, str] | None = None,
    patch_order: Sequence[str] | None = None,
    renumber: str | None = "rcm",
    metadata: dict[str, str] | None = None,
) -> PolyMesh:
    """gmsh の ``.msh``(MSH 2 / 4)を読んで :class:`PolyMesh` に変換する。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    md = {"source": path.name}
    md.update(metadata or {})
    with gmsh_session("gsis_read_msh", verbosity=1) as gmsh:
        gmsh.merge(str(path))
        return polymesh_from_gmsh(patch_kinds=patch_kinds, patch_order=patch_order, renumber=renumber, metadata=md)


def write_msh_from_elements(
    path: str | Path,
    points: np.ndarray,
    elements: Sequence[tuple[str, np.ndarray]],
    boundary: Sequence[BoundaryGroup],
    *,
    volume_name: str = "fluid",
    binary: bool = True,
) -> Path:
    """節点・要素・境界グループを gmsh の離散モデルにして ``.msh``(MSH 4.1)に書く。

    境界グループごとに離散曲面と 2 次元 Physical Group(名前付き)を作る。
    """
    path = Path(path)
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    with gmsh_session("gsis_write_msh", verbosity=1) as gmsh:
        vol = gmsh.model.addDiscreteEntity(3)
        node_tags = np.arange(1, points.shape[0] + 1, dtype=np.int64)
        gmsh.model.mesh.addNodes(3, vol, node_tags, points.ravel())
        next_tag = 1
        for etype, conn in elements:
            conn = np.asarray(conn, dtype=np.int64).reshape(-1, ELEMENT_NODES[etype])
            tags = np.arange(next_tag, next_tag + conn.shape[0], dtype=np.int64)
            gmsh.model.mesh.addElementsByType(vol, GMSH_TYPE_OF[etype], tags, (conn + 1).ravel())
            next_tag += conn.shape[0]
        gmsh.model.addPhysicalGroup(3, [vol], name=volume_name)
        for grp in boundary:
            surf = gmsh.model.addDiscreteEntity(2)
            for arr in grp.faces:
                arr = np.asarray(arr, dtype=np.int64)
                if arr.shape[0] == 0:
                    continue
                gtype = 2 if arr.shape[1] == 3 else 3
                tags = np.arange(next_tag, next_tag + arr.shape[0], dtype=np.int64)
                gmsh.model.mesh.addElementsByType(surf, gtype, tags, (arr + 1).ravel())
                next_tag += arr.shape[0]
            gmsh.model.addPhysicalGroup(2, [surf], name=grp.name)
        gmsh.option.setNumber("Mesh.Binary", 1 if binary else 0)
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        path.parent.mkdir(parents=True, exist_ok=True)
        gmsh.write(str(path))
    return path
