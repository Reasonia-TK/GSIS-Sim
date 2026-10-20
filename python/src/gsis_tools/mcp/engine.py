"""gmsh(OpenCASCADE)のモデルを MCP のツールから操作するエンジン。

状態の持ち方:

- モデルを変える操作(形状・境界パッチ・メッシュの設定)は、検証済みの引数とともに手順の履歴
  (``history``)に記録する。gmsh / OpenCASCADE は同じ手順から同じ番号の形状を作るので、
  「元に戻す」と「保存した状態の読み込み」は、履歴を新しい gmsh のモデルで最初からやり直して行う
- 失敗した操作は、途中まで変わった gmsh のモデルを履歴からやり直して元に戻す
- 境界パッチは面の番号と、その面の面積・重心(指紋)で持つ。形状が変わって面が変わったら、
  その面をパッチから外して知らせる(移動・回転・拡大では、面は同じなので指紋を更新する)
- メッシュは状態ではなく、``mesh_generate`` で作るもの。形状を変えると消える

gmsh の呼び出しは、すべて 1 つのスレッドで順に行う(``server.py``)。
"""

from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import meshing
from .errors import BatchFailure, ToolFailure
from .faces import FaceInfo, compute_face_infos, face_row, select_faces
from .workspace import Workspace

logger = logging.getLogger(__name__)

UNITS = {"m": 1.0, "cm": 1e-2, "mm": 1e-3, "um": 1e-6}
STATE_FORMAT = "gsis-mcp-model"
STATE_VERSION = 1

# 操作の分類: geometry(形状を変える)、setting(パッチ・メッシュの設定)、query(読むだけ)、
# action(ファイルやメッシュを作る。履歴には残さない)、model(モデル全体の操作)
OP_KINDS: dict[str, str] = {
    "add_box": "geometry", "add_cylinder": "geometry", "add_sphere": "geometry", "add_cone": "geometry",
    "add_torus": "geometry", "add_profile": "geometry", "extrude": "geometry", "revolve": "geometry",
    "boolean": "geometry", "transform": "geometry", "remove": "geometry", "import_cad": "geometry",
    "run_python": "geometry",
    "assign_patch": "setting", "remove_patch": "setting", "mesh_settings": "setting", "mesh_refine": "setting",
    "model_info": "query", "measure": "query", "list_faces": "query", "check_geometry": "query",
    "list_patches": "query", "render": "query", "mesh_quality": "query",
    "gui_surface": "query",  # GUI 専用(--gui-tools のときだけ一覧に出る。AI には見せない)
    "mesh_generate": "action", "export_pmesh": "action", "export": "action", "model_save": "action",
    "model_new": "model", "model_load": "model", "undo": "model", "batch": "model",
}
BATCH_EXCLUDED = {"model_new", "model_load", "model_save", "undo", "batch", "gui_surface"}
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]{0,63}$")
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
REF_RE = re.compile(r"^([vscp]):(\d+)$")
DIM_OF_PREFIX = {"v": 3, "s": 2, "c": 1, "p": 0}
DIM_NAME = {0: "点", 1: "曲線", 2: "面", 3: "体積"}
PLANES = {"xy": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)), "xz": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
          "yz": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))}
CAD_SUFFIXES = (".step", ".stp", ".iges", ".igs", ".brep", ".brp")
OCC_UNIT_NAMES = {"m": "M", "cm": "CM", "mm": "MM", "um": "UM"}
HEAL_OPTIONS = ("OCCFixDegenerated", "OCCFixSmallEdges", "OCCFixSmallFaces", "OCCSewFaces", "OCCMakeSolids")


def rnd(v, sig: int = 7):
    """数値を有効数字 sig 桁に丸める(配列・リストにも使う)。応答を小さく保つため。"""
    if isinstance(v, (list, tuple, np.ndarray)):
        return [rnd(x, sig) for x in np.asarray(v, dtype=float).tolist()]
    v = float(v)
    if v == 0.0 or not math.isfinite(v):
        return 0.0 if v == 0.0 else v
    return float(f"{v:.{sig}g}")


@dataclass
class HistoryEntry:
    tool: str
    args: dict[str, Any]
    group: int
    before: dict[str, Any] = field(repr=False, default_factory=dict)  # この手順の前の設定(取り消し用)


@dataclass
class PatchDef:
    name: str
    kind: str
    faces: dict[int, tuple[float, float, float, float]]  # 面の番号 → (面積, 重心 x, y, z)


@dataclass
class MeshState:
    dim: int
    geom_version: int
    settings_version: int
    stats: dict[str, Any]


class ModelEngine:
    """gmsh のモデル 1 つ分の状態と、ツールの実装。"""

    def __init__(self, workspace: Workspace, *, allow_python: bool = False, threads: int | None = None):
        self.ws = workspace
        self.allow_python = allow_python
        self.threads = int(threads or min(8, os.cpu_count() or 1))
        self.arg_normalizer = None  # tools 層が設定する(読み込んだ手順の引数の検証)
        self._gmsh = None
        self._model: str | None = None
        self._owns_gmsh = False
        self._model_seq = 0
        self._group_seq = 0
        self._render_seq = 0
        self._record_args: dict[str, Any] | None = None
        self._refresh_faces: set[int] | None = None
        self._mesh_serial = 0  # メッシュを作るたびに増やす(GUI の表示の作り直しの判定に使う)
        self._reset_state("model", "mm")

    # ------------------------------------------------------------------
    # 状態
    # ------------------------------------------------------------------

    def _reset_state(self, name: str, unit: str) -> None:
        self.name = name
        self.unit = unit
        self.history: list[HistoryEntry] = []
        self.names: dict[str, list[tuple[int, int]]] = {}
        self._gone: dict[str, str] = {}
        self._auto: dict[str, int] = {}
        self.patches: dict[str, PatchDef] = {}
        self.mesh_settings = dict(meshing.DEFAULT_SETTINGS)
        self.refinements: dict[str, dict[str, Any]] = {}
        self._settings_version = 0
        self._geom_version = 0
        self._face_cache: tuple[int, dict[int, FaceInfo]] | None = None
        self._tess_cache: tuple[int, Any] | None = None
        self._mesh: MeshState | None = None
        self._polymesh_cache: tuple[Any, Any] | None = None

    def _settings_snapshot(self) -> dict[str, Any]:
        return copy.deepcopy({
            "names": self.names, "gone": self._gone, "auto": self._auto, "patches": self.patches,
            "mesh_settings": self.mesh_settings, "refinements": self.refinements,
        })

    def _restore_settings(self, snap: dict[str, Any], *, bump: bool = True) -> None:
        """設定を戻す。bump=False は失敗した手順の巻き戻し(設定は手順の前と同じなので、メッシュを古い扱いにしない)。"""
        snap = copy.deepcopy(snap)
        self.names = snap["names"]
        self._gone = snap["gone"]
        self._auto = snap["auto"]
        self.patches = snap["patches"]
        self.mesh_settings = snap["mesh_settings"]
        self.refinements = snap["refinements"]
        if bump:
            self._settings_version += 1
            self._polymesh_cache = None

    # ------------------------------------------------------------------
    # gmsh
    # ------------------------------------------------------------------

    @property
    def gmsh(self):
        return self._ensure_gmsh()

    def _ensure_gmsh(self):
        if self._gmsh is None:
            import gmsh

            if not gmsh.isInitialized():
                # argv[0] を省くと -nopopup が無視され、FLTK の設定保存が起こり得る。
                gmsh.initialize(["gsis-tools", "-nopopup"], readConfigFiles=False, interruptible=False)
                self._owns_gmsh = True
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("General.Verbosity", 2)
            gmsh.logger.start()
            self._gmsh = gmsh
            self._new_gmsh_model()
        g = self._gmsh
        if g.model.getCurrent() != self._model:
            g.model.setCurrent(self._model)
        return g

    def _new_gmsh_model(self) -> None:
        g = self._gmsh
        if self._model is not None:
            try:
                g.model.setCurrent(self._model)
                g.model.remove()
            except Exception:  # pragma: no cover
                pass
        self._model_seq += 1
        self._model = f"gsis_mcp_{os.getpid()}_{id(self) & 0xFFFFFF:x}_{self._model_seq}"
        g.model.add(self._model)

    def close(self) -> None:
        """gmsh のモデルを消す(このエンジンが gmsh を初期化していれば終了する)。"""
        if self._gmsh is None:
            return
        g = self._gmsh
        try:
            g.model.setCurrent(self._model)
            g.model.remove()
        except Exception:  # pragma: no cover
            pass
        try:
            g.logger.stop()
        except Exception:  # pragma: no cover
            pass
        if self._owns_gmsh:
            g.finalize()
        self._gmsh = None
        self._model = None

    @property
    def occ(self):
        return self.gmsh.model.occ

    def _log_reset(self) -> None:
        g = self.gmsh
        g.logger.stop()
        g.logger.start()

    def _log_messages(self, limit: int = 6) -> list[str]:
        try:
            msgs = self.gmsh.logger.get()
        except Exception:  # pragma: no cover
            return []
        out = [m for m in msgs if m.startswith(("Warning", "Error"))]
        if len(out) > limit:
            out = out[:limit] + [f"…ほか {len(out) - limit} 件"]
        return out

    @property
    def unit_scale(self) -> float:
        return UNITS[self.unit]

    def _volumes(self) -> list[int]:
        return [t for _, t in self.gmsh.model.getEntities(3)]

    def _diag(self) -> float:
        g = self.gmsh
        ents = g.model.getEntities(3) or g.model.getEntities(2) or g.model.getEntities()
        if not ents:
            return 1.0
        bbs = np.array([g.model.getBoundingBox(d, t) for d, t in ents])
        d = float(np.linalg.norm(bbs[:, 3:].max(axis=0) - bbs[:, :3].min(axis=0)))
        return d if d > 0 else 1.0

    def _fingerprint(self) -> tuple:
        """失敗した手順がモデルを変えたかを見る指紋(形状の一覧と OpenCASCADE の最大の番号)。

        gmsh の 1 回の呼び出し(変形・ブーリアンなど)は、失敗すればモデルを変えない。途中まで進んだ手順
        (断面の点や線を作った後の失敗など)は、形状が増えるので指紋が変わる。run_python の失敗は常にやり直す。
        """
        g = self.gmsh
        g.model.occ.synchronize()
        ents = tuple(g.model.getEntities())
        tags = tuple(g.model.occ.getMaxTag(d) for d in range(4))
        return ents, tags

    # ------------------------------------------------------------------
    # 呼び出し・履歴・やり直し
    # ------------------------------------------------------------------

    def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """ツールを 1 つ実行する(引数は tools 層で検証・正規化済み)。"""
        kind = OP_KINDS.get(tool)
        if kind is None:
            raise ToolFailure(f"未知のツールです: {tool}")
        self._ensure_gmsh()
        args = dict(args or {})
        if kind in ("geometry", "setting"):
            self._group_seq += 1
            return self._run_state_op(tool, args, self._group_seq)
        self._log_reset()
        result = getattr(self, "op_" + tool)(**args)
        msgs = self._log_messages()
        if msgs and isinstance(result, dict):
            result.setdefault("gmsh_messages", msgs)
        return result

    def _run_state_op(self, tool: str, args: dict[str, Any], group: int) -> dict[str, Any]:
        kind = OP_KINDS[tool]
        before = self._settings_snapshot()
        fp = self._fingerprint() if kind == "geometry" else None
        self._log_reset()
        self._record_args = None
        self._refresh_faces = None
        try:
            result = getattr(self, "op_" + tool)(**args) or {}
        except BaseException:
            self._restore_settings(before, bump=False)
            if kind == "geometry":
                self._recover_geometry(fp, force=(tool == "run_python"))
            raise
        rec = self._record_args if self._record_args is not None else args
        self.history.append(HistoryEntry(tool, copy.deepcopy(rec), group, before))
        if kind == "geometry":
            notes = self._after_geometry_change()
            if notes:
                result["patch_changes"] = notes
        else:
            self._settings_version += 1
            self._polymesh_cache = None
        msgs = self._log_messages()
        if msgs:
            result["gmsh_messages"] = msgs
        return result

    def _recover_geometry(self, fp, *, force: bool = False) -> None:
        try:
            same = (not force) and self._fingerprint() == fp
        except Exception:  # pragma: no cover
            same = False
        if not same:
            logger.info("失敗した操作の途中の変更を、履歴からやり直して戻します")
            self._rebuild(self.history)

    def _after_geometry_change(self) -> dict[str, Any]:
        g = self.gmsh
        g.model.occ.synchronize()
        self._geom_version += 1
        self._face_cache = None
        self._tess_cache = None
        self._polymesh_cache = None
        if self._mesh is not None:
            try:
                g.model.mesh.clear()
            except Exception:  # pragma: no cover
                pass
            self._mesh = None
        self._prune_names()
        return self._revalidate_patches(self._refresh_faces)

    def _rebuild(self, entries: list[HistoryEntry]) -> None:
        """新しい gmsh のモデルで、手順を最初からやり直す。"""
        entries = list(entries)
        name, unit = self.name, self.unit
        self._new_gmsh_model()
        self._reset_state(name, unit)
        for i, e in enumerate(entries):
            kind = OP_KINDS[e.tool]
            before = self._settings_snapshot()
            self._record_args = None
            self._refresh_faces = None
            try:
                getattr(self, "op_" + e.tool)(**copy.deepcopy(e.args))
            except Exception as exc:
                raise RuntimeError(f"履歴の {i} 番目の手順 {e.tool} をやり直せませんでした: {exc}") from exc
            self.history.append(HistoryEntry(e.tool, copy.deepcopy(e.args), e.group, before))
            if kind == "geometry":
                self._after_geometry_change()
            else:
                self._settings_version += 1
        self._group_seq = max([self._group_seq] + [e.group for e in self.history])

    # ------------------------------------------------------------------
    # 名前と参照
    # ------------------------------------------------------------------

    def _entity_set(self, dim: int) -> set[int]:
        return {t for _, t in self.gmsh.model.getEntities(dim)}

    def _new_name(self, name: str | None, prefix: str, *, reusable: set[str] | None = None) -> str:
        if name is None:
            while True:
                self._auto[prefix] = self._auto.get(prefix, 0) + 1
                cand = f"{prefix}{self._auto[prefix]}"
                if cand not in self.names:
                    return cand
        if not NAME_RE.match(name):
            raise ToolFailure(f"名前 {name!r} は使えません", hint="英字か _ で始まり、英数字・_・.・- からなる 64 文字以内の名前にしてください")
        if REF_RE.match(name):
            raise ToolFailure(f"名前 {name!r} は番号の参照(v:3 など)と紛らわしいので使えません")
        if name in self.names and not (reusable and name in reusable):
            raise ToolFailure(f"名前 {name!r} はすでに使われています({self._describe_ref(name)})",
                              hint="別の名前にするか、remove で前の形状を消してください")
        return name

    def _describe_ref(self, name: str) -> str:
        dts = self.names.get(name, [])
        return "、".join(f"{DIM_NAME[d]} {t}" for d, t in dts)

    def _register(self, name: str, dimtags: list[tuple[int, int]]) -> str:
        self.names[name] = [(int(d), int(t)) for d, t in dimtags]
        self._gone.pop(name, None)
        return name

    def _unregister(self, names: set[str], reason: str) -> None:
        for n in names:
            if n in self.names:
                del self.names[n]
                self._gone[n] = reason

    def _prune_names(self) -> None:
        g = self.gmsh
        exist = {d: {t for _, t in g.model.getEntities(d)} for d in range(4)}
        step = len(self.history)
        for n in list(self.names):
            keep = [(d, t) for d, t in self.names[n] if t in exist[d]]
            if keep:
                self.names[n] = keep
            else:
                del self.names[n]
                tool = self.history[-1].tool if self.history else "?"
                self._gone[n] = f"手順 {step}({tool})で消えました"

    def _resolve(self, refs, default_dim: int, *, dims: tuple[int, ...] | None = None, what: str = "entities"
                 ) -> list[tuple[int, int]]:
        if refs is None or (isinstance(refs, (list, tuple)) and not refs):
            raise ToolFailure(f"{what} が空です")
        if isinstance(refs, (int, str)):
            refs = [refs]
        out: list[tuple[int, int]] = []
        for r in refs:
            if isinstance(r, bool):
                raise ToolFailure(f"{what} に真偽値は使えません: {r}")
            if isinstance(r, int):
                dts = [(default_dim, int(r))]
            elif isinstance(r, str):
                m = REF_RE.match(r)
                if m:
                    dts = [(DIM_OF_PREFIX[m.group(1)], int(m.group(2)))]
                elif r in self.names:
                    dts = list(self.names[r])
                elif r in self._gone:
                    raise ToolFailure(f"名前 {r!r} の形状はもうありません({self._gone[r]})",
                                      hint="model_info で今ある名前を確かめてください")
                else:
                    known = ", ".join(sorted(self.names)) or "(なし)"
                    raise ToolFailure(f"名前 {r!r} はありません。今ある名前: {known}")
            else:
                raise ToolFailure(f"{what} の要素は番号か名前にしてください: {r!r}")
            for d, t in dts:
                if t not in self._entity_set(d):
                    raise ToolFailure(f"{DIM_NAME[d]} {t} はありません", hint="model_info / list_faces で番号を確かめてください")
                if dims is not None and d not in dims:
                    raise ToolFailure(f"{what} には{'・'.join(DIM_NAME[x] for x in dims)}を指定してください({DIM_NAME[d]} {t} が入っています)")
                if (d, t) not in out:
                    out.append((d, t))
        return out

    def _names_of(self, dimtags) -> list[str]:
        s = set(dimtags)
        return sorted(n for n, dts in self.names.items() if s.intersection(dts))

    def _volume_summary(self, dimtags) -> dict[str, Any]:
        occ = self.occ
        vols = [t for d, t in dimtags if d == 3]
        out: dict[str, Any] = {}
        if vols:
            out["volumes"] = vols
            out["volume"] = rnd(sum(occ.getMass(3, v) for v in vols), 10)
            faces = self.gmsh.model.getBoundary([(3, v) for v in vols], combined=False, oriented=False)
            out["faces"] = len({abs(t) for _, t in faces})
        surfs = [t for d, t in dimtags if d == 2]
        if surfs:
            out["surfaces"] = surfs
        return out

    def _created(self, name: str, dimtags, expected: float | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"name": name}
        out.update(self._volume_summary(dimtags))
        if expected is not None and "volume" in out and expected > 0:
            out["expected_volume"] = rnd(expected, 10)
            out["rel_error"] = rnd((out["volume"] - expected) / expected, 3)
        out["model_volumes"] = len(self._volumes())
        return out

    # ------------------------------------------------------------------
    # パッチの指紋
    # ------------------------------------------------------------------

    def _face_fp(self, tag: int) -> tuple[float, float, float, float]:
        occ = self.occ
        c = occ.getCenterOfMass(2, tag)
        return (float(occ.getMass(2, tag)), float(c[0]), float(c[1]), float(c[2]))

    def _fp_match(self, a, b, diag: float) -> bool:
        if abs(a[0] - b[0]) > 1e-7 * max(abs(a[0]), abs(b[0]), 1e-300):
            return False
        return math.dist(a[1:], b[1:]) <= 1e-7 * diag

    def _revalidate_patches(self, refresh: set[int] | None) -> dict[str, Any]:
        if not self.patches:
            return {}
        infos = self.face_infos()
        diag = self._diag()
        notes: dict[str, Any] = {}
        for p in self.patches.values():
            dropped = []
            for tag in list(p.faces):
                f = infos.get(tag)
                if f is None or not f.boundary:
                    dropped.append(tag)
                    del p.faces[tag]
                    continue
                fp = self._face_fp(tag)
                if refresh is not None and tag in refresh:
                    p.faces[tag] = fp
                elif not self._fp_match(p.faces[tag], fp, diag):
                    dropped.append(tag)
                    del p.faces[tag]
            if dropped:
                notes[p.name] = {"dropped_faces": dropped, "remaining": len(p.faces)}
        if notes:
            notes["note"] = "形状が変わったので、変わった面をパッチから外しました。assign_patch で付け直してください"
        return notes

    # ------------------------------------------------------------------
    # 面の情報
    # ------------------------------------------------------------------

    def face_infos(self) -> dict[int, FaceInfo]:
        if self._face_cache is not None and self._face_cache[0] == self._geom_version:
            return self._face_cache[1]
        infos = compute_face_infos(self.gmsh, self._diag())
        self._face_cache = (self._geom_version, infos)
        return infos

    def _patch_of(self) -> dict[int, str]:
        return {t: p.name for p in self.patches.values() for t in p.faces}

    def _boundary_faces(self) -> list[int]:
        return sorted(t for t, f in self.face_infos().items() if f.boundary)

    def _select(self, sel: dict[str, Any] | None) -> tuple[list[int], dict[str, int]]:
        return select_faces(
            self.gmsh, self.face_infos(), sel, patch_of=self._patch_of(), diag=self._diag(),
            resolve_volumes=lambda refs: [t for _, t in self._resolve(refs, 3, dims=(3,), what="volumes")],
        )

    # ==================================================================
    # モデル
    # ==================================================================

    def op_model_new(self, *, name: str = "model", unit: str = "mm") -> dict[str, Any]:
        if unit not in UNITS:
            raise ToolFailure(f"unit は {list(UNITS)} のいずれか: {unit}")
        self._new_gmsh_model()
        self._reset_state(name, unit)
        return {"model": name, "unit": unit, "note": "新しいモデルを作りました(履歴は空)"}

    def op_model_info(self, *, history: bool = False) -> dict[str, Any]:
        occ = self.occ
        vols = []
        for v in self._volumes():
            vols.append({"tag": v, "names": self._names_of([(3, v)]), "volume": rnd(occ.getMass(3, v), 10)})
        infos = self.face_infos()
        free = sorted(t for t, f in infos.items() if not f.volumes)
        internal = sorted(t for t, f in infos.items() if len(f.volumes) > 1)
        pof = self._patch_of()
        bnd = self._boundary_faces()
        out: dict[str, Any] = {
            "model": self.name, "unit": self.unit, "workspace": str(self.ws.root),
            "steps": len({e.group for e in self.history}), "volumes": vols,
            "total_volume": rnd(sum(v["volume"] for v in vols), 10),
            "faces": {"boundary": len(bnd), "internal": len(internal), "free": len(free)},
            "names": {n: self._describe_ref(n) for n in sorted(self.names)},
            "patches": {p.name: {"kind": p.kind, "faces": len(p.faces)} for p in self.patches.values()},
            "unassigned_faces": [t for t in bnd if t not in pof][:40],
            "mesh": self._mesh_status(),
            "refinements": sorted(self.refinements),
        }
        if free:
            out["free_surfaces"] = free[:40]
        if history:
            out["history"] = [{"step": i, "group": e.group, "tool": e.tool} for i, e in enumerate(self.history)]
        elif self.history:
            out["last_step"] = self.history[-1].tool
        return out

    def _mesh_status(self) -> dict[str, Any]:
        m = self._mesh
        if m is None:
            return {"exists": False}
        st = {"exists": True, "dim": m.dim, "stale_settings": m.settings_version != self._settings_version}
        if "tetrahedra" in m.stats:
            st["tetrahedra"] = m.stats["tetrahedra"]
        return st

    def op_model_save(self, *, path: str) -> dict[str, Any]:
        out = self.ws.out_path(path, (".json",))
        data = {
            "format": STATE_FORMAT, "version": STATE_VERSION, "name": self.name, "unit": self.unit,
            "saved": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "generator": "gsis_tools.mcp", "gmsh": getattr(self.gmsh, "__version__", ""),
            "history": [{"tool": e.tool, "args": e.args, "group": e.group} for e in self.history],
        }
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        return {"path": self.ws.relative(out), "steps": len(self.history)}

    def op_model_load(self, *, path: str) -> dict[str, Any]:
        src = self.ws.in_path(path, (".json",))
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolFailure(f"状態のファイルを読めません: {exc}") from exc
        if data.get("format") != STATE_FORMAT or data.get("version") != STATE_VERSION:
            raise ToolFailure(f"状態のファイルの形式が違います(format={data.get('format')!r}, version={data.get('version')!r})")
        unit = data.get("unit", "mm")
        if unit not in UNITS:
            raise ToolFailure(f"状態のファイルの unit が不正です: {unit}")
        entries = []
        for i, h in enumerate(data.get("history", [])):
            tool, args = h.get("tool"), h.get("args", {})
            if OP_KINDS.get(tool) not in ("geometry", "setting"):
                raise ToolFailure(f"状態のファイルの {i} 番目の手順 {tool!r} は記録できる手順ではありません")
            if tool == "run_python" and not self.allow_python:
                raise ToolFailure(f"状態のファイルの {i} 番目の手順が run_python です。--allow-python で起動したサーバーでだけ読み込めます")
            if self.arg_normalizer is not None:
                try:
                    args = self.arg_normalizer(tool, args)
                except Exception as exc:
                    raise ToolFailure(f"状態のファイルの {i} 番目の手順 {tool} の引数が不正です: {exc}") from exc
            entries.append(HistoryEntry(tool, args, int(h.get("group", i + 1))))
        old_entries, old_name, old_unit = list(self.history), self.name, self.unit
        name = str(data.get("name", "model"))
        if not MODEL_NAME_RE.match(name):
            name = "model"  # 画像の既定のファイル名に使うので、変な名前は使わない
        try:
            self.name, self.unit = name, unit
            self._rebuild(entries)
        except Exception as exc:
            self.name, self.unit = old_name, old_unit
            self._rebuild(old_entries)
            raise ToolFailure(f"状態を読み込めなかったので、元のモデルに戻しました: {exc}") from exc
        info = self.op_model_info()
        info["loaded"] = self.ws.relative(src)
        return info

    def op_undo(self, *, steps: int = 1) -> dict[str, Any]:
        if not self.history:
            raise ToolFailure("取り消せる手順がありません")
        groups = []
        for e in self.history:
            if not groups or groups[-1] != e.group:
                groups.append(e.group)
        steps = max(1, int(steps))
        drop = set(groups[-steps:])
        keep = [e for e in self.history if e.group not in drop]
        removed = [e for e in self.history if e.group in drop]
        trailing = all(e.group in drop for e in self.history[len(keep):])
        if trailing and all(OP_KINDS[e.tool] == "setting" for e in removed):
            # 設定だけの取り消し: 形状とメッシュはそのまま
            self._restore_settings(removed[0].before)
            self.history = keep
        else:
            old = list(self.history)
            try:
                self._rebuild(keep)
            except Exception as exc:
                self._rebuild(old)
                raise ToolFailure(f"取り消しのやり直しに失敗したので、取り消す前に戻しました: {exc}") from exc
        out = {"undone": [e.tool for e in reversed(removed)], "steps_left": len({e.group for e in self.history})}
        out.update({k: v for k, v in self.op_model_info().items() if k in ("volumes", "total_volume", "patches", "unassigned_faces", "mesh")})
        return out

    def op_batch(self, *, steps: list[tuple[str, dict[str, Any]]], verbose: bool = False) -> dict[str, Any]:
        n0 = len(self.history)
        snap = self._settings_snapshot()
        self._group_seq += 1
        group = self._group_seq
        results = []
        geom_done = False
        for i, (tool, args) in enumerate(steps):
            if tool in BATCH_EXCLUDED:
                raise ToolFailure(f"batch の中では {tool} は使えません(index {i})")
            kind = OP_KINDS.get(tool)
            try:
                if kind is None:
                    raise ToolFailure(f"未知のツールです: {tool}")
                if kind in ("geometry", "setting"):
                    r = self._run_state_op(tool, dict(args), group)
                    geom_done = geom_done or kind == "geometry"
                else:
                    r = getattr(self, "op_" + tool)(**args)
                results.append(r)
            except Exception as exc:
                del self.history[n0:]
                if geom_done:
                    self._rebuild(self.history)
                else:
                    self._restore_settings(snap)
                raise BatchFailure(i, tool, exc, results) from exc
        out: dict[str, Any] = {"ok": True, "steps": len(steps)}
        if verbose:
            out["results"] = results
        else:
            out["results"] = [_brief(t, r) for (t, _), r in zip(steps, results)]
        return out

    # ==================================================================
    # 形状
    # ==================================================================

    def _sync(self) -> None:
        self.occ.synchronize()

    def op_add_box(self, *, corner, size, name=None) -> dict[str, Any]:
        if min(size) <= 0:
            raise ToolFailure("size(箱の大きさ)は 3 つとも正の値にしてください")
        nm = self._new_name(name, "box")
        tag = self.occ.addBox(*corner, *size)
        self._sync()
        self._register(nm, [(3, tag)])
        return self._created(nm, [(3, tag)], expected=float(np.prod(size)))

    def op_add_cylinder(self, *, base, axis, radius, angle_deg=360.0, name=None) -> dict[str, Any]:
        h = float(np.linalg.norm(axis))
        if h <= 0 or radius <= 0:
            raise ToolFailure("axis(底面の中心から上面の中心へのベクトル)と radius は 0 にできません")
        if not 0 < angle_deg <= 360:
            raise ToolFailure("angle_deg は 0 より大きく 360 以下にしてください")
        nm = self._new_name(name, "cylinder")
        tag = self.occ.addCylinder(*base, *axis, radius, angle=math.radians(angle_deg))
        self._sync()
        self._register(nm, [(3, tag)])
        return self._created(nm, [(3, tag)], expected=math.pi * radius**2 * h * angle_deg / 360.0)

    def op_add_sphere(self, *, center, radius, name=None) -> dict[str, Any]:
        if radius <= 0:
            raise ToolFailure("radius は正の値にしてください")
        nm = self._new_name(name, "sphere")
        tag = self.occ.addSphere(*center, radius)
        self._sync()
        self._register(nm, [(3, tag)])
        return self._created(nm, [(3, tag)], expected=4.0 / 3.0 * math.pi * radius**3)

    def op_add_cone(self, *, base, axis, radius1, radius2, name=None) -> dict[str, Any]:
        h = float(np.linalg.norm(axis))
        if h <= 0 or radius1 < 0 or radius2 < 0 or radius1 == radius2 == 0:
            raise ToolFailure("axis は 0 にできません。radius1・radius2 は 0 以上で、両方 0 にはできません")
        nm = self._new_name(name, "cone")
        tag = self.occ.addCone(*base, *axis, radius1, radius2)
        self._sync()
        self._register(nm, [(3, tag)])
        return self._created(nm, [(3, tag)], expected=math.pi * h * (radius1**2 + radius1 * radius2 + radius2**2) / 3.0)

    def op_add_torus(self, *, center, radius_major, radius_minor, axis=(0.0, 0.0, 1.0), name=None) -> dict[str, Any]:
        if not 0 < radius_minor < radius_major:
            raise ToolFailure("0 < radius_minor < radius_major にしてください")
        nm = self._new_name(name, "torus")
        tag = self.occ.addTorus(*center, radius_major, radius_minor, zAxis=list(_unit3(axis)))
        self._sync()
        self._register(nm, [(3, tag)])
        return self._created(nm, [(3, tag)], expected=2.0 * math.pi**2 * radius_major * radius_minor**2)

    # ---- 断面 ----------------------------------------------------------

    @staticmethod
    def _plane_frame(plane, origin, u_axis, v_axis):
        O = np.asarray(origin, float)
        if u_axis is not None or v_axis is not None:
            if u_axis is None or v_axis is None:
                raise ToolFailure("u_axis と v_axis は両方指定してください")
            U = _unit3(u_axis)
            Vv = np.asarray(v_axis, float) - np.dot(v_axis, U) * U
            if np.linalg.norm(Vv) < 1e-12:
                raise ToolFailure("u_axis と v_axis が平行です")
            V = Vv / np.linalg.norm(Vv)
        else:
            if plane not in PLANES:
                raise ToolFailure(f"plane は {list(PLANES)} のいずれか: {plane}")
            U, V = (np.asarray(a, float) for a in PLANES[plane])
        return O, U, V, np.cross(U, V)

    def op_add_profile(self, *, plane="xy", origin=(0.0, 0.0, 0.0), u_axis=None, v_axis=None, outer, holes=(),
                       name=None) -> dict[str, Any]:
        O, U, V, N = self._plane_frame(plane, origin, u_axis, v_axis)
        loops = [outer, *list(holes or [])]
        polys = [_loop_polyline(lp) for lp in loops]
        scale = max(float(np.ptp(np.concatenate(polys), axis=0).max()), 1e-300)
        tol = 1e-9 * scale
        for i, p in enumerate(polys):
            where = "outer" if i == 0 else f"holes[{i - 1}]"
            if abs(_shoelace(p)) <= 1e-12 * scale**2:
                raise ToolFailure(f"断面の {where} の面積が 0 です")
            hit = _self_intersection(p, tol)
            if hit is not None:
                raise ToolFailure(f"断面の {where} が自己交差しています(辺 {hit[0]} と {hit[1]})",
                                  hint="点の順序を見直してください(閉じる辺は自動で足します)")
        for i, p in enumerate(polys[1:]):
            if not _point_in_polygon(p[0], polys[0]):
                raise ToolFailure(f"断面の holes[{i}] が outer の外にあります")
            if _polylines_cross(p, polys[0], tol):
                raise ToolFailure(f"断面の holes[{i}] が outer と交わっています")
        nm = self._new_name(name, "profile")
        occ = self.occ
        cache: dict[tuple[float, float], int] = {}

        def point(uv) -> int:
            key = (round(float(uv[0]) / tol) * tol, round(float(uv[1]) / tol) * tol) if tol > 0 else tuple(uv)
            if key not in cache:
                X = O + float(uv[0]) * U + float(uv[1]) * V
                cache[key] = occ.addPoint(*X)
            return cache[key]

        wires = []
        for lp in loops:
            wires.append(_build_loop(occ, lp, point, O, U, V, N, tol))
        s = occ.addPlaneSurface(wires)
        self._sync()
        area = float(occ.getMass(2, s))
        expect = abs(_shoelace(polys[0])) - sum(abs(_shoelace(p)) for p in polys[1:])
        if abs(area - expect) > 2e-3 * max(expect, 1e-300):
            raise ToolFailure(f"断面の面積 {area:.6g} が点列からの見積り {expect:.6g} と合いません(自己交差や穴の重なりの疑い)")
        self._register(nm, [(2, s)])
        c = np.asarray(occ.getCenterOfMass(2, s), float)
        cu, cv = float((c - O) @ U), float((c - O) @ V)
        out: dict[str, Any] = {"name": nm, "surface": s, "area": rnd(area, 10), "centroid": rnd(c),
                               "centroid_uv": [rnd(cu), rnd(cv)], "normal": rnd(N, 6)}
        allu = np.concatenate(polys)[:, 0]
        if allu.min() >= -tol or allu.max() <= tol:
            out["pappus_volume_v_axis"] = rnd(2.0 * math.pi * abs(cu) * area, 10)
            out["note"] = "pappus_volume_v_axis は、この断面を v 軸(u = 0 の直線)まわりに 360° 回したときの体積"
        return out

    def op_extrude(self, *, entities, vector, name=None) -> dict[str, Any]:
        dts = self._resolve(entities, 2, dims=(2,), what="entities")
        if float(np.linalg.norm(vector)) <= 0:
            raise ToolFailure("vector は 0 にできません")
        occ = self.occ
        expected = 0.0
        for _, s in dts:
            f = self.face_infos().get(s)
            if f is not None and f.type == "plane" and f.raw_normal is not None:
                expected += float(occ.getMass(2, s)) * abs(float(np.dot(f.raw_normal, vector)))
        nm = self._new_name(name, "extrude")
        out = occ.extrude(dts, *vector)
        self._sync()
        vols = [(d, t) for d, t in out if d == 3]
        if not vols:
            raise ToolFailure("押し出しで体積ができませんでした")
        self._register(nm, vols)
        return self._created(nm, vols, expected=expected or None)

    def op_revolve(self, *, entities, axis_point=(0.0, 0.0, 0.0), axis_dir=(0.0, 0.0, 1.0), angle_deg=360.0,
                   name=None) -> dict[str, Any]:
        dts = self._resolve(entities, 2, dims=(2,), what="entities")
        if not 0 < abs(angle_deg) <= 360:
            raise ToolFailure("angle_deg は 0 でなく、絶対値 360 以下にしてください")
        a = _unit3(axis_dir)
        p0 = np.asarray(axis_point, float)
        occ = self.occ
        g = self.gmsh
        # パップスの定理(平面の断面で、軸が断面の平面内にあり、断面が軸をまたがないとき)
        expected = 0.0
        pappus_ok = True
        theta = math.radians(abs(angle_deg))
        for _, s in dts:
            f = self.face_infos().get(s)
            if f is None or f.type != "plane" or f.raw_normal is None or abs(float(np.dot(f.raw_normal, a))) > 1e-9:
                pappus_ok = False
                break
            side = np.cross(a, f.raw_normal)  # 平面内で軸に直交する向き
            pts = [g.model.getValue(0, t, []) for _, t in g.model.getBoundary([(2, s)], recursive=True)]
            dd = np.array([float(np.dot(np.asarray(q) - p0, side)) for q in pts])
            span = max(float(np.ptp(dd)), 1e-300)
            if not (dd.min() >= -1e-9 * span or dd.max() <= 1e-9 * span):
                pappus_ok = False
                break
            expected += theta * float(occ.getMass(2, s)) * abs(float(np.dot(f.centroid - p0, side)))
        nm = self._new_name(name, "revolve")
        out = occ.revolve(dts, *p0, *a, math.radians(angle_deg))
        self._sync()
        vols = [(d, t) for d, t in out if d == 3]
        if not vols:
            raise ToolFailure("回転で体積ができませんでした")
        removed = []
        if abs(abs(angle_deg) - 360.0) < 1e-9:
            # 360° 回すと、元の断面は体積の面ではなくなって残る(体積に属さない面)。消す
            for d, s in dts:
                if s in self._entity_set(2) and len(g.model.getAdjacencies(2, s)[0]) == 0:
                    occ.remove([(2, s)], recursive=True)
                    removed.append(s)
            self._sync()
        self._register(nm, vols)
        res = self._created(nm, vols)
        if pappus_ok and expected > 0:
            res["pappus_volume"] = rnd(expected, 10)
            res["rel_error"] = rnd((res["volume"] - expected) / expected, 3)
        if removed:
            res["removed_profile_surfaces"] = removed
        return res

    # ---- ブーリアン・変形・削除 ------------------------------------------

    def op_boolean(self, *, op, objects, tools=None, keep_objects=False, keep_tools=False, name=None) -> dict[str, Any]:
        fn = {"fuse": "fuse", "cut": "cut", "intersect": "intersect", "fragment": "fragment"}.get(op)
        if fn is None:
            raise ToolFailure(f"op は fuse / cut / intersect / fragment のいずれか: {op}")
        obj = self._resolve(objects, 3, dims=(2, 3), what="objects")
        dim = obj[0][0]
        if any(d != dim for d, _ in obj):
            raise ToolFailure("objects には同じ次元の形状(体積どうし、面どうし)を指定してください")
        tl = self._resolve(tools, dim, dims=(dim,), what="tools") if tools else []
        if op != "fragment" and not tl:
            raise ToolFailure(f"{op} には tools が要ります")
        both = set(obj) & set(tl)
        if both:
            raise ToolFailure(f"objects と tools の両方に入っている形状があります: {sorted(both)}")
        consumed = set()
        if not keep_objects:
            consumed |= set(obj)
        if not keep_tools:
            consumed |= set(tl)
        consumed_names = {n for n, dts in self.names.items() if set(dts) <= consumed}
        first_obj_names = [n for n, dts in self.names.items() if obj[0] in dts and n in consumed_names]
        inherit = name or (first_obj_names[0] if first_obj_names else None)
        nm = self._new_name(inherit, op, reusable=consumed_names)
        occ = self.occ
        before_v = sum(occ.getMass(d, t) for d, t in obj + tl) if dim == 3 else None
        out, _ = getattr(occ, fn)(obj, tl, removeObject=not keep_objects, removeTool=not keep_tools)
        self._sync()
        res = [(d, t) for d, t in out if d == dim]
        if not res:
            raise ToolFailure(f"{op} の結果が空になりました(形状が重なっていない、など)",
                              hint="measure と render で位置を確かめてください")
        self._unregister(consumed_names - {nm}, f"{op} で使われて消えました")
        self._register(nm, res)
        r = {"op": op, "name": nm, "count": len(res)}
        r.update(self._volume_summary(res))
        if dim == 3 and op == "fuse" and before_v is not None:
            r["sum_of_inputs"] = rnd(before_v, 10)
        r["model_volumes"] = len(self._volumes())
        return r

    def op_transform(self, *, entities, op, vector=None, point=(0.0, 0.0, 0.0), axis=None, angle_deg=None,
                     factor=None, normal=None, duplicate=False, name=None) -> dict[str, Any]:
        dts = self._resolve(entities, 3, what="entities")
        occ = self.occ
        g = self.gmsh
        if op == "translate":
            if vector is None:
                raise ToolFailure("translate には vector が要ります")
        elif op == "rotate":
            if axis is None or angle_deg is None:
                raise ToolFailure("rotate には axis と angle_deg が要ります")
            _unit3(axis)
        elif op == "scale":
            if factor is None:
                raise ToolFailure("scale には factor(数か [fx, fy, fz])が要ります")
            f = [float(factor)] * 3 if isinstance(factor, (int, float)) else [float(x) for x in factor]
            if len(f) != 3 or min(abs(x) for x in f) == 0:
                raise ToolFailure("factor は 0 でない数か、3 つの数にしてください")
        elif op == "mirror":
            if normal is None:
                raise ToolFailure("mirror には normal(鏡の面の法線)が要ります")
            _unit3(normal)
        else:
            raise ToolFailure(f"op は translate / rotate / scale / mirror のいずれか: {op}")
        nm = None
        if duplicate:
            nm = self._new_name(name, "copy")
            dts = [(d, t) for d, t in occ.copy(dts)]
        elif name is not None:
            raise ToolFailure("name は duplicate=true のときだけ使えます(移動した形状は元の名前のまま)")
        if op == "translate":
            occ.translate(dts, *vector)
        elif op == "rotate":
            occ.rotate(dts, *point, *_unit3(axis), math.radians(angle_deg))
        elif op == "scale":
            occ.dilate(dts, *point, *f)
        else:
            n = _unit3(normal)
            occ.mirror(dts, float(n[0]), float(n[1]), float(n[2]), -float(n @ np.asarray(point, float)))
        self._sync()
        if nm:
            self._register(nm, dts)
        else:
            faces = {abs(t) for _, t in g.model.getBoundary([dt for dt in dts if dt[0] == 3], combined=False)} if any(
                d == 3 for d, _ in dts) else set()
            faces |= {t for d, t in dts if d == 2}
            self._refresh_faces = faces
        r: dict[str, Any] = {"op": op, "duplicate": bool(duplicate)}
        if nm:
            r["name"] = nm
        r.update(self._volume_summary(dts))
        return r

    def op_remove(self, *, entities) -> dict[str, Any]:
        dts = self._resolve(entities, 3, what="entities")
        self.occ.remove(dts, recursive=True)
        self._sync()
        return {"removed": [f"{DIM_NAME[d]} {t}" for d, t in dts], "model_volumes": len(self._volumes())}

    def op_import_cad(self, *, path, heal=False, name=None) -> dict[str, Any]:
        src = self.ws.in_path(path, CAD_SUFFIXES)
        g = self.gmsh
        internal_prefix = f"{self.ws.internal.name}/imports/"
        rel = self.ws.relative(src)
        if rel.startswith(internal_prefix):
            dst = src
        else:
            digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
            dst_dir = self.ws.internal / "imports"
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / f"{digest}{src.suffix.lower()}"
            if not dst.exists():
                shutil.copy2(src, dst)
        nm = self._new_name(name, "import")
        opt = g.option
        prev_unit = opt.getString("Geometry.OCCTargetUnit")
        opt.setString("Geometry.OCCTargetUnit", OCC_UNIT_NAMES[self.unit])
        prev_heal = {k: opt.getNumber(f"Geometry.{k}") for k in HEAL_OPTIONS}
        for k in HEAL_OPTIONS:
            opt.setNumber(f"Geometry.{k}", 1 if heal else 0)
        try:
            dts = self.occ.importShapes(str(dst), highestDimOnly=True)
            self._sync()
        finally:
            opt.setString("Geometry.OCCTargetUnit", prev_unit)
            for k, v in prev_heal.items():
                opt.setNumber(f"Geometry.{k}", v)
        dts = [(int(d), int(t)) for d, t in dts]
        if not dts:
            raise ToolFailure("ファイルから形状を読めませんでした")
        self._record_args = {"path": self.ws.relative(dst), "heal": bool(heal), "name": nm}
        self._register(nm, dts)
        occ = self.occ
        faces = sorted({abs(t) for _, t in g.model.getBoundary([dt for dt in dts if dt[0] == 3], combined=False)}) if any(
            d == 3 for d, _ in dts) else [t for d, t in dts if d == 2]
        areas = {f: float(occ.getMass(2, f)) for f in faces}
        tot = sum(areas.values()) or 1.0
        zero = [f for f, a in areas.items() if a <= 1e-9 * tot]
        r: dict[str, Any] = {"name": nm, "source": rel, "stored_as": self.ws.relative(dst), "healed": bool(heal),
                             "unit": self.unit}
        r.update(self._volume_summary(dts))
        if not any(d == 3 for d, _ in dts):
            r["warning"] = "体積がありません(面だけを読みました)"
        if zero:
            r["zero_area_faces"] = zero
            r["hint"] = ("面積 0 の面があります。そのままではメッシュを作れないことがあります。heal=true で読み直し、"
                         "体積を必ず確かめてください(修復は空洞のある正常な形状を壊すことがあるので既定にしていません)")
        return r

    def op_run_python(self, *, code: str) -> dict[str, Any]:
        if not self.allow_python:
            raise ToolFailure("run_python は無効です(サーバーを --allow-python 付きで起動したときだけ使えます)")
        g = self.gmsh
        buf = io.StringIO()
        api = _NameAPI(self)
        ns = {"gmsh": g, "occ": g.model.occ, "np": np, "math": math, "names": api, "__name__": "__gsis_mcp__"}
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<run_python>", "exec"), ns)
        except ToolFailure:
            raise
        except BaseException as exc:
            import traceback

            tb = traceback.format_exception(exc)
            lines = [ln for ln in "".join(tb).splitlines() if ln.strip()]
            raise ToolFailure(f"スクリプトが失敗しました: {type(exc).__name__}: {exc}",
                              hint="モデルはスクリプトの前の状態に戻しました",
                              data={"traceback": lines[-8:], "stdout": buf.getvalue()[-2000:]}) from exc
        if g.model.getCurrent() != self._model:
            g.model.setCurrent(self._model)
            raise ToolFailure("スクリプトが gmsh の現在のモデルを切り替えました(元に戻しました)")
        text = buf.getvalue()
        if len(text) > 4000:
            text = text[:4000] + f"\n…(残り {len(text) - 4000} 文字は省略)"
        r = {"stdout": text, "model_volumes": len(self._volumes()),
             "total_volume": rnd(sum(self.occ.getMass(3, v) for v in self._volumes()), 10)}
        if api.created:
            r["names"] = api.created
        return r

    # ==================================================================
    # 計測・検査
    # ==================================================================

    def op_measure(self, *, entities=None, expected_volume=None, rtol=None) -> dict[str, Any]:
        g = self.gmsh
        occ = self.occ
        vols = [t for _, t in self._resolve(entities, 3, dims=(3,), what="entities")] if entities else self._volumes()
        if not vols:
            raise ToolFailure("体積がありません")
        rows = []
        total = 0.0
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        for v in vols:
            V = float(occ.getMass(3, v))
            total += V
            faces = sorted({abs(t) for _, t in g.model.getBoundary([(3, v)], combined=False)})
            area = sum(float(occ.getMass(2, f)) for f in faces)
            bb = np.asarray(g.model.getBoundingBox(3, v))
            lo, hi = np.minimum(lo, bb[:3]), np.maximum(hi, bb[3:])
            _, loops = occ.getSurfaceLoops(v)
            rows.append({"tag": v, "names": self._names_of([(3, v)]), "volume": rnd(V, 10), "area": rnd(area, 10),
                         "center": rnd(occ.getCenterOfMass(3, v)), "bbox": rnd(bb), "faces": len(faces),
                         "shells": len(loops)})
        out: dict[str, Any] = {"unit": self.unit, "volumes": rows, "total_volume": rnd(total, 10),
                               "bbox": rnd(np.concatenate([lo, hi]))}
        if expected_volume is not None:
            err = (total - expected_volume) / expected_volume if expected_volume else float("inf")
            out["expected_volume"] = rnd(expected_volume, 10)
            out["rel_error"] = rnd(err, 3)
            if rtol is not None:
                out["check"] = "ok" if abs(err) <= rtol else "fail"
                if abs(err) > rtol:
                    raise ToolFailure(f"体積 {total:.10g} が期待値 {expected_volume:.10g} と合いません(相対誤差 {err:.3e} > rtol {rtol:g})",
                                      data={"measured": out})
        return out

    def op_list_faces(self, *, select=None, limit=60, offset=0) -> dict[str, Any]:
        infos = self.face_infos()
        pof = self._patch_of()
        if select:
            tags, counts = self._select(select)
        else:
            tags = sorted(infos)
            counts = None
        rows = [face_row(infos[t], pof.get(t), rnd) for t in tags[offset:offset + limit]]
        out: dict[str, Any] = {"unit": self.unit, "count": len(tags), "faces": rows}
        if offset + limit < len(tags):
            out["truncated"] = f"{offset}〜{offset + len(rows) - 1} 番目を表示。続きは offset={offset + limit}"
        if counts is not None:
            out["filter_counts"] = counts
        return out

    def op_check_geometry(self, *, mesh_test: bool = False) -> dict[str, Any]:
        g = self.gmsh
        occ = self.occ
        infos = self.face_infos()
        vols = self._volumes()
        issues: list[dict[str, Any]] = []
        if not vols:
            issues.append({"kind": "no_volume", "message": "体積がありません"})
        for v in vols:
            V = float(occ.getMass(3, v))
            if not V > 0:
                issues.append({"kind": "non_positive_volume", "volume": v, "value": V})
        tot_area = sum(f.area for f in infos.values()) or 1.0
        zero = [t for t, f in infos.items() if f.area <= 1e-9 * tot_area]
        if zero:
            issues.append({"kind": "zero_area_faces", "faces": zero,
                           "message": "面積 0 の面(退化面)があります。メッシュを作れないことがあります"})
        diag = self._diag()
        small = []
        degenerated = 0
        for _, c in g.model.getEntities(1):
            try:
                L = float(occ.getMass(1, c))
            except Exception:  # pragma: no cover
                continue
            if L <= 1e-7 * diag:
                # 球・円錐の極の「退化した曲線」(OpenCASCADE の正常な位相。gmsh では種類が Unknown)は除く
                if g.model.getType(1, c) == "Unknown":
                    degenerated += 1
                else:
                    small.append(c)
        if small:
            issues.append({"kind": "tiny_edges", "curves": small[:50], "message": "長さがほぼ 0 の曲線があります"})
        free = sorted(t for t, f in infos.items() if not f.volumes)
        if free:
            issues.append({"kind": "free_surfaces", "faces": free,
                           "message": "体積に属さない面があります(メッシュには入りません。remove で消せます)"})
        # 重なっている体積(外接箱が重なる組だけ調べる)
        if len(vols) > 1:
            overl = self._overlaps(vols)
            for a, b, w in overl:
                issues.append({"kind": "overlapping_volumes", "volumes": [a, b], "overlap_volume": rnd(w, 6),
                               "message": "体積が重なっています。boolean(fuse か fragment)でまとめてください"})
        out: dict[str, Any] = {"ok": not issues, "volumes": len(vols), "faces": len(infos), "issues": issues}
        if degenerated:
            out["degenerated_edges"] = degenerated  # 球の極など(問題ではない)
        shells = {}
        for v in vols:
            _, loops = occ.getSurfaceLoops(v)
            shells[v] = len(loops)
        out["shells"] = shells
        if mesh_test and vols:
            out["surface_mesh_test"] = self._surface_mesh_test()
            if not out["surface_mesh_test"].get("ok"):
                out["ok"] = False
        return out

    def _overlaps(self, vols: list[int]) -> list[tuple[int, int, float]]:
        """外接箱が重なる体積の組について、重なりの体積を調べる(作業用の別のモデルで)。"""
        g = self.gmsh
        occ = self.occ
        bbs = {v: np.asarray(g.model.getBoundingBox(3, v)) for v in vols}
        pairs = []
        for i, a in enumerate(vols):
            for b in vols[i + 1:]:
                A, B = bbs[a], bbs[b]
                if np.all(A[:3] <= B[3:]) and np.all(B[:3] <= A[3:]):
                    pairs.append((a, b))
        if not pairs:
            return []
        sig = {v: (float(occ.getMass(3, v)), np.asarray(occ.getCenterOfMass(3, v))) for v in vols}
        diag = self._diag()
        out = []
        tmp = self._tmp_file(".brep")
        cur = g.model.getCurrent()
        try:
            g.write(str(tmp))
            g.model.add(f"{cur}_overlap")
            try:
                occ.importShapes(str(tmp), highestDimOnly=True)
                occ.synchronize()
                # 読み直した体積と元の体積を、体積の値と重心で対応させる
                mp: dict[int, int] = {}
                for _, t in g.model.getEntities(3):
                    m, c = float(occ.getMass(3, t)), np.asarray(occ.getCenterOfMass(3, t))
                    for v, (mv, cv) in sig.items():
                        if v not in mp and abs(m - mv) <= 1e-9 * max(mv, 1e-300) and np.linalg.norm(c - cv) <= 1e-9 * diag:
                            mp[v] = t
                            break
                for a, b in pairs:
                    if a not in mp or b not in mp:
                        continue
                    try:
                        res, _ = occ.intersect([(3, mp[a])], [(3, mp[b])], removeObject=False, removeTool=False)
                        occ.synchronize()
                        w = sum(occ.getMass(3, t) for d, t in res if d == 3)
                        if res:
                            occ.remove(res, recursive=True)
                            occ.synchronize()
                    except Exception:
                        w = 0.0
                    ref = min(sig[a][0], sig[b][0])
                    if w > 1e-9 * ref:
                        out.append((a, b, w))
            finally:
                g.model.remove()
                g.model.setCurrent(cur)
        finally:
            tmp.unlink(missing_ok=True)
        return out

    def _surface_mesh_test(self) -> dict[str, Any]:
        """2 次元メッシュ(なければ粗く作る)で、各体積の境界の三角形が閉じているか確かめる。"""
        tri, fid, nodes, ok_mesh, msg = self._tessellate()
        out: dict[str, Any] = {"ok": ok_mesh, "triangles": int(tri.shape[0])}
        if msg:
            out["message"] = msg
        if tri.shape[0] == 0:
            out["ok"] = False
            return out
        infos = self.face_infos()
        for v in self._volumes():
            faces = [t for t, f in infos.items() if v in f.volumes]
            tv = nodes[np.isin(fid, faces)]
            if tv.shape[0] == 0:
                out["ok"] = False
                out.setdefault("no_triangles", []).append(v)
                continue
            e = np.sort(np.concatenate([tv[:, [0, 1]], tv[:, [1, 2]], tv[:, [2, 0]]]), axis=1)
            _, cnt = np.unique(e, axis=0, return_counts=True)
            bad = int((cnt != 2).sum())
            if bad:
                out["ok"] = False
                out.setdefault("open_edges", {})[str(v)] = bad
        return out

    # ==================================================================
    # 境界パッチ
    # ==================================================================

    def op_assign_patch(self, *, name, select, kind=None, mode="set", steal=False) -> dict[str, Any]:
        if not NAME_RE.match(name):
            raise ToolFailure(f"パッチ名 {name!r} は使えません", hint="英字か _ で始まる英数字・_・.・- の名前にしてください")
        from ..builder import guess_patch_kind
        from ..pmesh import PATCH_KINDS

        if kind is not None and kind not in PATCH_KINDS:
            raise ToolFailure(f"kind は {list(PATCH_KINDS)} のいずれか: {kind}")
        tags, counts = self._select(select)
        if not tags and mode != "remove":
            raise ToolFailure("条件に合う面がありません", hint="条件ごとの残りの数(filter_counts)を見て、条件を緩めてください",
                              data={"filter_counts": counts})
        pof = self._patch_of()
        if mode in ("set", "add"):
            taken = {t: pof[t] for t in tags if t in pof and pof[t] != name}
            if taken and not steal:
                raise ToolFailure(f"面 {sorted(taken)} はすでに別のパッチに入っています({sorted(set(taken.values()))})",
                                  hint="steal=true で移すか、条件に unassigned=true を足してください",
                                  data={"taken": {str(k): v for k, v in taken.items()}})
            for t in taken:
                del self.patches[taken[t]].faces[t]
        p = self.patches.get(name)
        if p is None:
            if mode == "remove":
                raise ToolFailure(f"パッチ {name!r} はありません")
            p = PatchDef(name, kind or guess_patch_kind(name), {})
            self.patches[name] = p
        elif kind is not None:
            p.kind = kind
        if mode == "set":
            p.faces = {t: self._face_fp(t) for t in tags}
        elif mode == "add":
            for t in tags:
                p.faces[t] = self._face_fp(t)
        elif mode == "remove":
            for t in tags:
                p.faces.pop(t, None)
        else:
            raise ToolFailure(f"mode は set / add / remove のいずれか: {mode}")
        infos = self.face_infos()
        pof = self._patch_of()
        un = [t for t in self._boundary_faces() if t not in pof]
        return {"patch": name, "kind": p.kind, "faces": sorted(p.faces), "area": rnd(sum(infos[t].area for t in p.faces), 10),
                "matched": len(tags), "unassigned": len(un), "unassigned_faces": un[:30]}

    def op_remove_patch(self, *, name) -> dict[str, Any]:
        if name == "*":
            n = len(self.patches)
            self.patches = {}
            return {"removed": n}
        if name not in self.patches:
            raise ToolFailure(f"パッチ {name!r} はありません。今あるパッチ: {list(self.patches) or '(なし)'}")
        del self.patches[name]
        return {"removed": name, "patches": list(self.patches)}

    def op_list_patches(self) -> dict[str, Any]:
        infos = self.face_infos()
        rows = []
        for p in self.patches.values():
            rows.append({"name": p.name, "kind": p.kind, "faces": sorted(p.faces),
                         "area": rnd(sum(infos[t].area for t in p.faces if t in infos), 10)})
        pof = self._patch_of()
        un = [t for t in self._boundary_faces() if t not in pof]
        out: dict[str, Any] = {"unit": self.unit, "patches": rows, "unassigned_count": len(un)}
        if un:
            out["unassigned"] = [face_row(infos[t], None, rnd) for t in un[:40]]
            if len(un) > 40:
                out["unassigned_truncated"] = len(un) - 40
        out["complete"] = not un and bool(rows)
        return out

    # ==================================================================
    # メッシュ
    # ==================================================================

    def op_mesh_settings(self, *, size_max=None, size_min=None, curvature_points=None, algorithm_3d=None,
                         algorithm_2d=None, optimize=None, extend_from_boundary=None, threads=None, reset=False
                         ) -> dict[str, Any]:
        if reset:
            self.mesh_settings = dict(meshing.DEFAULT_SETTINGS)
        new = {"size_max": size_max, "size_min": size_min, "curvature_points": curvature_points,
               "algorithm_3d": algorithm_3d, "algorithm_2d": algorithm_2d, "optimize": optimize,
               "extend_from_boundary": extend_from_boundary, "threads": threads}
        for k, v in new.items():
            if v is not None:
                self.mesh_settings[k] = v
        s = self.mesh_settings
        if s["size_max"] is not None and s["size_max"] <= 0:
            raise ToolFailure("size_max は正の値にしてください")
        if s["size_min"] and s["size_max"] and s["size_min"] > s["size_max"]:
            raise ToolFailure("size_min が size_max より大きくなっています")
        out = dict(s)
        out["size_max_effective"] = rnd(meshing.effective_size_max(s, self._diag()), 6)
        out["unit"] = self.unit
        return out

    def op_mesh_refine(self, *, name, target=None, select=None, size=None, size_far=None, dist_min=0.0, dist_max=None,
                       box=None, center=None, radius=None, base=None, axis=None, thickness=0.0, sampling=None,
                       remove=False) -> dict[str, Any]:
        if remove:
            if name == "*":
                n = len(self.refinements)
                self.refinements = {}
                return {"removed": n}
            if name not in self.refinements:
                raise ToolFailure(f"細分化 {name!r} はありません。今あるもの: {sorted(self.refinements) or '(なし)'}")
            del self.refinements[name]
            return {"removed": name, "refinements": sorted(self.refinements)}
        if not NAME_RE.match(name):
            raise ToolFailure(f"名前 {name!r} は使えません")
        if target is None or size is None:
            raise ToolFailure("target と size が要ります")
        if size <= 0 or (size_far is not None and size_far <= 0):
            raise ToolFailure("size・size_far は正の値にしてください")
        r: dict[str, Any] = {"target": target, "size": float(size), "size_far": size_far}
        if target == "faces":
            if dist_max is None or dist_max <= (dist_min or 0.0):
                raise ToolFailure("faces には dist_max(この距離で size_far になる。dist_min より大きく)が要ります")
            tags, counts = self._select(select or {})
            if not tags:
                raise ToolFailure("条件に合う面がありません", data={"filter_counts": counts})
            r.update({"select": _drop_none(select), "dist_min": float(dist_min or 0.0), "dist_max": float(dist_max),
                      "sampling": sampling})
            r["faces"] = tags
        elif target == "box":
            if box is None or len(box) != 6 or any(box[i] >= box[i + 3] for i in range(3)):
                raise ToolFailure("box には [xmin, ymin, zmin, xmax, ymax, zmax](min < max)が要ります")
            r.update({"box": [float(x) for x in box], "thickness": float(thickness or 0.0)})
        elif target == "ball":
            if center is None or radius is None or radius <= 0:
                raise ToolFailure("ball には center と正の radius が要ります")
            r.update({"center": [float(x) for x in center], "radius": float(radius), "thickness": float(thickness or 0.0)})
        elif target == "cylinder":
            if base is None or axis is None or radius is None or radius <= 0 or float(np.linalg.norm(axis)) == 0:
                raise ToolFailure("cylinder には base・axis(底面の中心から上面の中心へ)・正の radius が要ります")
            r.update({"base": [float(x) for x in base], "axis": [float(x) for x in axis], "radius": float(radius)})
        else:
            raise ToolFailure(f"target は faces / box / ball / cylinder のいずれか: {target}")
        self.refinements[name] = r
        out = {"refinement": name, **{k: v for k, v in r.items() if v is not None}}
        out["refinements"] = sorted(self.refinements)
        return out

    def _refinement_faces(self) -> dict[str, list[int]]:
        """面による細分化の面(形状が変わっていたら、記録した条件で選び直す)。"""
        out = {}
        infos = self.face_infos()
        for name, r in self.refinements.items():
            if r["target"] != "faces":
                continue
            faces = [t for t in r.get("faces", []) if t in infos]
            if r.get("select") is not None:
                try:
                    faces, _ = self._select(r["select"])
                except ToolFailure:
                    faces = []
            if not faces:
                raise ToolFailure(f"細分化 {name!r} の面が見つかりません(形状が変わった?)",
                                  hint="mesh_refine で付け直すか、remove=true で消してください")
            out[name] = faces
        return out

    def op_mesh_generate(self, *, dim: int = 3, max_cells: int = 2_000_000) -> dict[str, Any]:
        g = self.gmsh
        vols = self._volumes()
        if dim == 3 and not vols:
            raise ToolFailure("体積がないので 3 次元メッシュを作れません")
        if dim not in (2, 3):
            raise ToolFailure("dim は 2 か 3")
        diag = self._diag()
        face_lists = self._refinement_faces()
        eff = meshing.apply_settings(g, self.mesh_settings, self.refinements, face_lists, diag, self.threads)
        t0 = time.perf_counter()
        self._mesh = None
        self._polymesh_cache = None
        self._tess_cache = None
        g.model.mesh.clear()
        estimate = None
        try:
            g.model.mesh.generate(2)
            if dim == 3:
                estimate = meshing.estimate_cells(g, self.mesh_settings, self.refinements, face_lists, eff["size_max"])
                if estimate > max_cells:
                    g.model.mesh.clear()
                    raise ToolFailure(
                        f"四面体の数の見積り {estimate:,} が上限 max_cells = {max_cells:,} を超えるので、作りませんでした",
                        hint="size_max・細分化の size を大きくするか、本当に必要なら max_cells を上げてください",
                        data={"estimate": estimate})
                g.model.mesh.generate(3)
                opt = (self.mesh_settings.get("optimize") or "none").lower()
                if opt == "netgen":
                    g.model.mesh.optimize("Netgen")
                elif opt == "gmsh":
                    g.model.mesh.optimize("")
        except ToolFailure:
            raise
        except Exception as exc:
            try:
                g.model.mesh.clear()
            except Exception:  # pragma: no cover
                pass
            raise ToolFailure(f"メッシュの生成に失敗しました: {exc}",
                              hint="check_geometry(mesh_test=true)で形状を確かめるか、寸法を変えてみてください",
                              data={"gmsh_messages": self._log_messages(10)}) from exc
        elapsed = time.perf_counter() - t0
        patch_faces = {p.name: sorted(p.faces) for p in self.patches.values()}
        geom_v = float(sum(self.occ.getMass(3, v) for v in vols)) if vols else 0.0
        stats = meshing.mesh_stats(g, dim, patch_faces, self._boundary_faces(), geom_v, rnd)
        stats["time_s"] = rnd(elapsed, 3)
        if estimate is not None:
            stats["estimate"] = estimate
        stats["settings"] = {"size_max": rnd(eff["size_max"], 6), "size_min": eff["size_min"], "fields": eff["fields"]}
        stats["unit"] = self.unit
        self._mesh = MeshState(dim, self._geom_version, self._settings_version, stats)
        self._mesh_serial += 1
        un = [t for t in self._boundary_faces() if t not in self._patch_of()]
        if un:
            stats["unassigned_faces"] = un[:30]
            stats["note"] = "パッチの付いていない面があります。export_pmesh の前に assign_patch してください"
        return stats

    def _require_mesh(self, dim: int = 3) -> MeshState:
        m = self._mesh
        if m is None or m.geom_version != self._geom_version:
            raise ToolFailure("メッシュがありません(形状を変えると消えます)", hint="mesh_generate を先に実行してください")
        if m.dim < dim:
            raise ToolFailure(f"{dim} 次元のメッシュがありません", hint=f"mesh_generate(dim={dim}) を実行してください")
        return m

    def _set_physical_groups(self, *, include_unassigned: bool) -> list[str]:
        g = self.gmsh
        # 名前も消す(名前が残っていると、作り直したグループに名前が付かない)
        for d, t in g.model.getPhysicalGroups():
            nm = g.model.getPhysicalName(d, t)
            if nm:
                g.model.removePhysicalName(nm)
        g.model.removePhysicalGroups()
        names = []
        for p in self.patches.values():
            if p.faces:
                g.model.addPhysicalGroup(2, sorted(p.faces), name=p.name)
                names.append(p.name)
        if include_unassigned:
            pof = self._patch_of()
            un = [t for t in self._boundary_faces() if t not in pof]
            if un:
                g.model.addPhysicalGroup(2, un, name="unassigned")
                names.append("unassigned")
        vols = self._volumes()
        if vols:
            g.model.addPhysicalGroup(3, vols, name="fluid")
        return names

    def _polymesh(self, *, patch_order=None, renumber=True):
        """メッシュを PolyMesh(モデルの単位のまま)にする。"""
        self._require_mesh(3)
        key = (tuple(patch_order or ()), bool(renumber), self._settings_version, self._geom_version)
        if self._polymesh_cache is not None and self._polymesh_cache[0] == key:
            return self._polymesh_cache[1]
        pof = self._patch_of()
        un = [t for t in self._boundary_faces() if t not in pof]
        if un:
            raise ToolFailure(f"パッチの付いていない面があります: {un[:30]}",
                              hint="assign_patch で名前を付けてください(残りをまとめるなら select={\"unassigned\": true})",
                              data={"unassigned_faces": un[:30]})
        if not any(p.faces for p in self.patches.values()):
            raise ToolFailure("パッチがありません")
        order = list(patch_order or [p.name for p in self.patches.values() if p.faces])
        unknown = [n for n in order if n not in self.patches or not self.patches[n].faces]
        if unknown:
            raise ToolFailure(f"patch_order に、ないか空のパッチがあります: {unknown}")
        self._set_physical_groups(include_unassigned=False)
        from ..gmsh_convert import polymesh_from_gmsh

        md = {"generator": "gsis_tools.mcp", "model": self.name, "model_unit": self.unit,
              "steps": str(len(self.history))}
        kinds = {p.name: p.kind for p in self.patches.values()}
        pm = polymesh_from_gmsh(patch_order=order, patch_kinds=kinds, renumber="rcm" if renumber else None, metadata=md)
        self._polymesh_cache = (key, pm)
        return pm

    def op_mesh_quality(self) -> dict[str, Any]:
        from ..pmesh import check_mesh
        from ..quality import compute_quality, summary

        pm = self._polymesh()
        q = compute_quality(pm)
        s = summary(pm, q)
        res = check_mesh(pm)
        geom_v = float(sum(self.occ.getMass(3, v) for v in self._volumes()))
        vol = s["total_volume"]
        out: dict[str, Any] = {
            "unit": self.unit, "cells": s["n_cells"], "faces": s["n_faces"], "points": s["n_points"],
            "cell_types": s["cell_types"], "volume": rnd(vol, 10), "geometry_volume": rnd(geom_v, 10),
            "volume_rel_diff": rnd((vol - geom_v) / geom_v, 3) if geom_v else None,
            "patches": {k: {"kind": v["kind"], "faces": v["faces"], "area": rnd(v["area"], 8)} for k, v in s["patches"].items()},
            "cell_size_hV": {k: rnd(s["cell_size"][k], 4) for k in ("min", "mean", "p50", "max")},
            "max_edge": rnd(s["cell_max_edge"]["max"], 4),
            "non_orthogonality_deg": {k: rnd(s["non_orthogonality"][k], 4) for k in ("mean", "p99", "max")},
            "skewness_max": rnd(s["skewness"]["max"], 4),
            "volume_ratio_max": rnd(s["volume_ratio"]["max"], 4),
            "check": "OK" if res.ok else "エラー",
        }
        if res.errors:
            out["check_errors"] = res.errors[:10]
        if res.warnings:
            out["check_warnings"] = res.warnings[:10]
        return out

    def op_export_pmesh(self, *, path, patch_order=None, renumber=True, report=None, vtu=None, boundary_vtu=None
                        ) -> dict[str, Any]:
        from ..pmesh import PMeshError, check_mesh, write_pmesh

        out_p = self.ws.out_path(path, (".pmesh",))
        rep_p = self.ws.out_path(report, (".md",)) if report else None
        vtu_p = self.ws.out_path(vtu, (".vtu",)) if vtu else None
        bvtu_p = self.ws.out_path(boundary_vtu, (".vtu",)) if boundary_vtu else None
        pm = self._polymesh(patch_order=patch_order, renumber=renumber).copy()
        s = self.unit_scale
        pm.points = pm.points * s
        pm.geometry(recompute=True)
        pm.metadata = dict(pm.metadata or {})
        pm.metadata["scale_to_m"] = repr(s)
        try:
            write_pmesh(out_p, pm)
        except PMeshError as exc:
            raise ToolFailure(f".pmesh の検査でエラーになりました: {exc}") from exc
        res = check_mesh(pm)
        g = pm.geometry()
        written = [self.ws.relative(out_p)]
        if rep_p or vtu_p:
            from ..quality import compute_quality, format_report

            q = compute_quality(pm)
            if rep_p:
                text = format_report(pm, q, title=f"メッシュ品質: {out_p.name}", length_scale=s, length_label=self.unit,
                                     notes=[f"gsis_tools.mcp で作成(モデル {self.name}、単位 {self.unit} を m に換算)"])
                rep_p.write_text(text, encoding="utf-8", newline="\n")
                written.append(self.ws.relative(rep_p))
            if vtu_p:
                from ..vtu import write_vtu

                write_vtu(vtu_p, pm, q.cell_data())
                written.append(self.ws.relative(vtu_p))
        if bvtu_p:
            from ..vtu import write_boundary_vtu

            write_boundary_vtu(bvtu_p, pm)
            written.append(self.ws.relative(bvtu_p))
        return {
            "path": self.ws.relative(out_p), "written": written, "length_unit": "m", "scale_to_m": s,
            "cells": pm.n_cells, "faces": pm.n_faces, "points": pm.n_points,
            "volume_m3": rnd(float(g.cell_volumes.sum()), 10),
            "patches": [{"name": p.name, "kind": p.kind, "faces": p.count, "area_m2": rnd(float(g.face_areas[p.faces].sum()), 8)}
                        for p in pm.patches],
            "check": "OK" if res.ok else "エラー",
            "warnings": res.warnings[:5],
        }

    def op_export(self, *, path, binary=False) -> dict[str, Any]:
        suffixes = (".step", ".stp", ".iges", ".igs", ".brep", ".stl", ".msh", ".vtu")
        out_p = self.ws.out_path(path, suffixes)
        suf = out_p.suffix.lower()
        g = self.gmsh
        opt = g.option
        info: dict[str, Any] = {"path": self.ws.relative(out_p), "format": suf[1:], "unit": self.unit}
        if suf in (".step", ".stp", ".iges", ".igs", ".brep"):
            if not g.model.getEntities():
                raise ToolFailure("書き出す形状がありません")
            prev = opt.getString("Geometry.OCCTargetUnit")
            opt.setString("Geometry.OCCTargetUnit", OCC_UNIT_NAMES[self.unit])
            try:
                g.write(str(out_p))
            finally:
                opt.setString("Geometry.OCCTargetUnit", prev)
            info["volumes"] = len(self._volumes())
            if suf != ".brep":
                info["note"] = "境界パッチは STEP / IGES には入りません(状態の保存や .msh・.pmesh で残ります)"
        elif suf == ".stl":
            self._require_mesh(2)
            names = self._set_physical_groups(include_unassigned=True)
            opt.setNumber("Mesh.StlOneSolidPerSurface", 2 if names else 0)
            opt.setNumber("Mesh.Binary", 1 if binary else 0)
            opt.setNumber("Mesh.SaveAll", 0)
            g.write(str(out_p))
            info["solids"] = names
        elif suf == ".msh":
            self._require_mesh(2)
            names = self._set_physical_groups(include_unassigned=True)
            opt.setNumber("Mesh.MshFileVersion", 4.1)
            opt.setNumber("Mesh.Binary", 1 if binary else 0)
            opt.setNumber("Mesh.SaveAll", 0)
            g.write(str(out_p))
            info["physical_groups"] = names + (["fluid"] if self._volumes() else [])
        else:  # .vtu
            from ..quality import compute_quality
            from ..vtu import write_vtu

            pm = self._polymesh()
            write_vtu(out_p, pm, compute_quality(pm).cell_data())
            info["cells"] = pm.n_cells
        info["bytes"] = out_p.stat().st_size
        return info

    # ==================================================================
    # 画像
    # ==================================================================

    def _tmp_file(self, suffix: str) -> Path:
        d = self.ws.internal / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        self._render_seq += 1
        return d / f"t{os.getpid()}_{self._render_seq}{suffix}"

    def _tessellate(self):
        """面の三角形 (T, 3, 3)、三角形ごとの面の番号、節点の番号 (T, 3)、成否、メッセージ。

        形状に合う 2 次元以上のメッシュがあればそれを使う。なければ粗い 2 次元メッシュを作って
        取り出し、消す(形状の番号は変わらない)。
        """
        g = self.gmsh
        if self._tess_cache is not None and self._tess_cache[0] == self._geom_version:
            return self._tess_cache[1]
        msg = None
        ok = True
        have_mesh = self._mesh is not None and self._mesh.geom_version == self._geom_version
        saved = {}
        if not have_mesh:
            diag = self._diag()
            opt = g.option
            keys = ("Mesh.MeshSizeMax", "Mesh.MeshSizeMin", "Mesh.MeshSizeFromCurvature",
                    "Mesh.MeshSizeExtendFromBoundary", "Mesh.MeshSizeFromPoints", "Mesh.Algorithm")
            saved = {k: opt.getNumber(k) for k in keys}
            F = g.model.mesh.field
            for t in list(F.list()):
                F.remove(t)
            opt.setNumber("Mesh.MeshSizeMax", diag / 45.0)
            opt.setNumber("Mesh.MeshSizeMin", diag / 3000.0)
            opt.setNumber("Mesh.MeshSizeFromCurvature", 24)
            opt.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
            opt.setNumber("Mesh.MeshSizeFromPoints", 0)
            opt.setNumber("Mesh.Algorithm", 6)
            g.model.mesh.clear()
            try:
                g.model.mesh.generate(2)
            except Exception as exc:
                ok = False
                msg = f"2 次元メッシュの一部が作れませんでした: {exc}"
        try:
            if g.model.mesh.getNodes()[0].size:
                X, idx = meshing.node_index(g)
            else:
                X, idx = np.zeros((0, 3)), np.zeros(1, np.int64)
            tris, fids, nds = [], [], []
            for _, s in g.model.getEntities(2):
                t = meshing.surface_triangles(g, s)
                if t.size:
                    tris.append(X[idx[t]])
                    fids.append(np.full(t.shape[0], s, dtype=np.int64))
                    nds.append(t)
        finally:
            if not have_mesh:
                g.model.mesh.clear()
                for k, v in saved.items():
                    g.option.setNumber(k, v)
        tri = np.concatenate(tris) if tris else np.zeros((0, 3, 3))
        fid = np.concatenate(fids) if fids else np.zeros(0, dtype=np.int64)
        nodes = np.concatenate(nds) if nds else np.zeros((0, 3), dtype=np.int64)
        res = (tri, fid, nodes, ok, msg)
        self._tess_cache = (self._geom_version, res)
        return res

    def op_render(self, *, target="geometry", view="auto", azimuth=None, elevation=None, direction=None, section=None,
                  color_by="patch", labels="auto", focus=None, zoom=1.0, width=900, height=700, path=None,
                  inline=False) -> dict[str, Any]:
        from . import render as R

        g = self.gmsh
        infos = self.face_infos()
        pof = self._patch_of()
        patch_list = [p.name for p in self.patches.values()]
        pcolor = {n: R.PALETTE[i % len(R.PALETTE)] for i, n in enumerate(patch_list)}
        clip = None
        if section is not None:
            p0 = np.asarray(section["point"], float)
            n0 = _unit3(section["normal"])
            clip = (p0, n0)
        legend: list[tuple[str, tuple]] = []
        label_map: dict[int, str] = {}
        line_id = None
        cap = False
        if target == "geometry":
            tri, fid, _, ok, msg = self._tessellate()
            if tri.shape[0] == 0:
                raise ToolFailure("描く面がありません", hint=msg or "形状を作ってください")
            if color_by == "patch":
                col_of = {t: pcolor.get(pof.get(t), R.UNASSIGNED) for t in infos}
                legend = [(n, pcolor[n]) for n in patch_list]
                if any(f.boundary and t not in pof for t, f in infos.items()):
                    legend.append(("(unassigned)", R.UNASSIGNED))
            elif color_by == "face":
                col_of = {t: R.face_palette(i) for i, t in enumerate(sorted(infos))}
            elif color_by == "volume":
                vols = self._volumes()
                cv = {v: R.PALETTE[i % len(R.PALETTE)] for i, v in enumerate(vols)}
                col_of = {t: cv.get(f.volumes[0], R.UNASSIGNED) if f.volumes else R.UNASSIGNED for t, f in infos.items()}
                legend = [(f"volume {v}", c) for v, c in cv.items()]
            else:
                raise ToolFailure("color_by は patch / face / volume のいずれか")
            uq, inv = np.unique(fid, return_inverse=True)
            color = np.array([col_of.get(int(t), R.UNASSIGNED) for t in uq]).reshape(-1, 3)[inv]
            face_id = fid
            lab = labels
            if lab == "auto":
                lab = "faces" if len(infos) <= 60 else "none"
            if lab == "faces":
                label_map = {t: str(t) for t in infos}
            elif lab == "patches":
                fid_u = np.array([patch_list.index(pof[int(t)]) + 1 if int(t) in pof else 0 for t in uq], dtype=np.int64)
                face_id = fid_u[inv]
                label_map = {i + 1: n for i, n in enumerate(patch_list)}
            cap = clip is not None
            ntri = tri.shape[0]
        elif target == "mesh":
            self._require_mesh(2)
            X, idx = meshing.node_index(g)
            tris, fids = [], []
            for t in self._boundary_faces() + sorted(t for t, f in infos.items() if not f.boundary):
                tt = meshing.surface_triangles(g, t)
                if tt.size:
                    tris.append(tt)
                    fids.append(np.full(tt.shape[0], t, dtype=np.int64))
            btri = np.concatenate(tris) if tris else np.zeros((0, 3), np.int64)
            bfid = np.concatenate(fids) if fids else np.zeros(0, np.int64)
            internal = {t for t, f in infos.items() if not f.boundary}
            if clip is not None and self._mesh.dim == 3:
                _, tets = meshing.tetrahedra(g)
                P = X[idx[tets]]
                keep = ((P.mean(axis=1) - clip[0]) @ clip[1]) <= 0
                kt = idx[tets[keep]]
                fc = np.concatenate([kt[:, [0, 1, 2]], kt[:, [0, 1, 3]], kt[:, [0, 2, 3]], kt[:, [1, 2, 3]]])
                key = _tri_keys(fc, X.shape[0])
                _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
                vis = cnt[inv.ravel()] == 1
                faces_v = fc[vis]
                # 境界の三角形と照合して、面の番号を付ける(合わないものは切り口)
                allk = np.concatenate([key[vis], _tri_keys(idx[btri], X.shape[0])])
                _, inv2 = np.unique(allk, axis=0, return_inverse=True)
                inv2 = inv2.ravel()
                a_id, b_id = inv2[: faces_v.shape[0]], inv2[faces_v.shape[0]:]
                lut = np.full(inv2.max() + 1, -1, dtype=np.int64)
                ok_b = ~np.isin(bfid, list(internal))
                lut[b_id[ok_b]] = bfid[ok_b]
                vf = lut[a_id]
                tri = X[faces_v]
                fid = vf
                clip = None  # 四面体ごとに切ったので、画素ごとには切らない
            else:
                ok_b = ~np.isin(bfid, list(internal))
                tri = X[idx[btri[ok_b]]]
                fid = bfid[ok_b]
            CUT = -10
            fid = np.where(fid < 0, CUT, fid)
            uq, inv = np.unique(fid, return_inverse=True)
            color = np.array([R.CUT_COLOR if t == CUT else pcolor.get(pof.get(int(t)), R.UNASSIGNED) for t in uq]
                             ).reshape(-1, 3)[inv]
            legend = [(n, pcolor[n]) for n in patch_list]
            if (uq == CUT).any():
                legend.append(("(cut)", R.CUT_COLOR))
            if any(int(t) not in pof for t in uq if t != CUT):
                legend.append(("(unassigned)", R.UNASSIGNED))
            pidx = {n: i for i, n in enumerate(patch_list)}
            fid_u = np.array([0 if t == CUT else (pidx[pof[int(t)]] + 1 if int(t) in pof else 1000 + int(t)) for t in uq],
                             dtype=np.int64)
            face_id = fid_u[inv]
            line_id = np.arange(fid.size)
            if labels in ("auto", "patches"):
                label_map = {i + 1: n for i, n in enumerate(patch_list)}
            elif labels == "faces":
                face_id = np.where(fid == CUT, 0, fid)
                label_map = {t: str(t) for t in infos}
            ntri = tri.shape[0]
        else:
            raise ToolFailure("target は geometry か mesh")
        # 視点
        if direction is not None:
            basis = R.camera_from_direction(direction)
            vdesc = f"direction {rnd(direction, 3)}"
        elif azimuth is not None or elevation is not None or view == "custom":
            az = -60.0 if azimuth is None else float(azimuth)
            el = 25.0 if elevation is None else float(elevation)
            basis = R.camera_from_angles(az, el)
            vdesc = f"az {az:g}, el {el:g}"
        elif view == "auto":
            if section is not None:
                basis = R.section_camera(section["normal"])
                vdesc = "section"
            else:
                basis = R.camera_from_angles(*R.VIEWS["iso"])
                vdesc = "iso"
        else:
            if view not in R.VIEWS:
                raise ToolFailure(f"view は {['auto', 'custom', *R.VIEWS]} のいずれか: {view}")
            basis = R.camera_from_angles(*R.VIEWS[view])
            vdesc = view
        if path:
            out_p = self.ws.out_path(path, (".png",))
        else:
            self._render_seq += 1
            out_p = self.ws.out_path(f"renders/{self.name}_{self._render_seq:03d}_{target}.png", (".png",))
        scene = R.Scene(tris=tri, color=color, face_id=face_id, line_id=line_id, labels=label_map, legend=legend,
                        clip=clip, cap=cap, focus=np.asarray(focus, float) if focus is not None else None)
        title = f"{self.name} [{self.unit}]  {target}  {vdesc}"
        if section is not None:
            title += f"  section p={rnd(section['point'], 4)} n={rnd(section['normal'], 3)}"
        info = R.render_scene(scene, out_p, basis=basis, width=width, height=height, title=title, zoom=zoom)
        out: dict[str, Any] = {"path": self.ws.relative(out_p), **info, "view": vdesc}
        if target == "geometry" and not ok:
            out["warning"] = msg
        if section is not None and float(np.dot(basis[2], _unit3(section["normal"]))) <= 0:
            out["note"] = ("カメラが断面の残した側にあるので、切り口は見えません。view を変えるか、"
                           "section の normal の向きを逆にしてください(法線の側を取り除き、その側から見ると切り口が見える)")
        if legend:
            out["legend"] = {t: "#%02x%02x%02x" % tuple(int(255 * c) for c in col) for t, col in legend}
        if inline:
            out["_image_png"] = out_p.read_bytes()
        out["triangles"] = int(ntri)
        return out

    # ==================================================================
    # GUI 専用(AI には見せない)
    # ==================================================================

    def op_gui_surface(self, *, known: str | None = None) -> dict[str, Any]:
        """GUI の 3D 表示用の表面: 三角形と、三角形ごとの面の番号、面の情報、パッチ。

        三角形は render と同じもの(メッシュがあればその表面、なければ粗い面のメッシュ)。配列は
        リトルエンディアンの生のバイト列を base64 にして返す(点 float32 (N, 3)、三角形 int32 (M, 3)、
        面の番号 int32 (M,))。``key`` は形状とメッシュの版で、前回の ``key`` を ``known`` に渡すと、
        変わっていなければ配列を省く(パッチの対応は毎回返す)。
        """
        mesh_now = self._mesh is not None and self._mesh.geom_version == self._geom_version
        key = f"{self._model_seq}:{self._geom_version}:{self._mesh_serial if mesh_now else 0}"
        infos = self.face_infos()
        out: dict[str, Any] = {
            "key": key, "model": self.name, "unit": self.unit,
            "steps": len({e.group for e in self.history}), "volumes": len(self._volumes()),
            "source": "mesh" if mesh_now else "geometry", "mesh": self._mesh_status(),
            "patches": [{"name": p.name, "kind": p.kind, "faces": sorted(p.faces)} for p in self.patches.values()],
            "faces": [{"id": t, "type": f.type, "boundary": f.boundary, "area": rnd(f.area, 7),
                       "c": rnd(f.centroid, 6)} for t, f in sorted(infos.items())],
        }
        if known is not None and known == key:
            out["unchanged"] = True
            return out
        tri, fid, nodes, ok, msg = self._tessellate() if infos else (np.zeros((0, 3, 3)), np.zeros(0, np.int64),
                                                                     np.zeros((0, 3), np.int64), True, None)
        if tri.shape[0]:
            uq, inv = np.unique(np.asarray(nodes).reshape(-1), return_inverse=True)
            inv = inv.reshape(-1)
            pts = np.zeros((uq.size, 3))
            pts[inv] = np.asarray(tri).reshape(-1, 3)
            tris = inv.reshape(-1, 3)
        else:
            pts = np.zeros((0, 3))
            tris = np.zeros((0, 3), np.int64)
        out["points"] = _pack_array(pts, "<f4")
        out["triangles"] = _pack_array(tris, "<i4")
        out["face_ids"] = _pack_array(fid, "<i4")
        out["n_points"] = int(pts.shape[0])
        out["n_triangles"] = int(tris.shape[0])
        if not ok:
            out["warning"] = msg
        return out


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def _pack_array(a, dtype: str) -> dict[str, Any]:
    """配列を JSON で送れる形にする(リトルエンディアンの生のバイト列を base64 に)。"""
    import base64

    arr = np.ascontiguousarray(np.asarray(a), dtype=np.dtype(dtype))
    return {"dtype": np.dtype(dtype).name, "shape": list(arr.shape),
            "data": base64.b64encode(arr.tobytes()).decode("ascii")}


class _NameAPI:
    """run_python から名前を付けるための小さな窓口。"""

    def __init__(self, engine: ModelEngine):
        self._e = engine
        self.created: dict[str, str] = {}

    def set(self, name: str, dimtags) -> None:
        nm = self._e._new_name(name, "py")
        self._e._register(nm, [(int(d), int(t)) for d, t in dimtags])
        self.created[nm] = self._e._describe_ref(nm)

    def get(self, name: str):
        return list(self._e.names.get(name, []))


def _drop_none(d):
    """辞書から値が None の項目を除く(入れ子も)。"""
    if isinstance(d, dict):
        return {k: _drop_none(v) for k, v in d.items() if v is not None}
    if isinstance(d, list):
        return [_drop_none(v) for v in d]
    return d


def _tri_keys(tri: np.ndarray, n_nodes: int) -> np.ndarray:
    """三角形 (n, 3) の節点の組を、向きによらない照合用のキーにする(節点が少なければ 1 つの整数に)。"""
    s = np.sort(np.asarray(tri, dtype=np.int64), axis=1)
    if n_nodes < (1 << 21):
        return (s[:, 0] << 42) | (s[:, 1] << 21) | s[:, 2]
    return s


def _unit3(v) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    if a.shape != (3,):
        raise ToolFailure(f"ベクトルは [x, y, z] の 3 つの数にしてください: {v}")
    n = float(np.linalg.norm(a))
    if n == 0:
        raise ToolFailure("0 ベクトルは使えません")
    return a / n


def _brief(tool: str, r: dict[str, Any]) -> dict[str, Any]:
    """batch の結果の短い形。"""
    keep = ("name", "volumes", "volume", "surface", "area", "rel_error", "pappus_volume", "patch", "faces", "matched",
            "unassigned", "path", "count", "tetrahedra", "cells", "check", "total_volume", "refinement",
            "pappus_volume_v_axis", "error")
    out = {"tool": tool}
    out.update({k: r[k] for k in keep if k in r and not isinstance(r[k], (dict,)) and k != "_image_png"})
    if "patch_changes" in r:
        out["patch_changes"] = r["patch_changes"]
    return out


# ---- 断面の点列 -------------------------------------------------------------


def _arc_points(p0, p1, *, center=None, through=None, ccw=True, n_per_rad: float = 24.0):
    """円弧の点列(始点を含まず、終点を含む)と、3 点の円弧の中間点。"""
    p0 = np.asarray(p0, float)
    p1 = np.asarray(p1, float)
    if center is not None:
        c = np.asarray(center, float)
        r0 = float(np.linalg.norm(p0 - c))
        r1 = float(np.linalg.norm(p1 - c))
        if r0 <= 0 or abs(r0 - r1) > 1e-6 * max(r0, r1):
            raise ToolFailure(f"円弧の始点と終点の中心からの距離が違います({r0:.6g} と {r1:.6g})")
        a0 = math.atan2(*(p0 - c)[::-1])
        a1 = math.atan2(*(p1 - c)[::-1])
        sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
        if abs(sweep) < 1e-12:
            raise ToolFailure("円弧の始点と終点が同じです(全周の円は circle を使ってください)")
        r = r0
    else:
        m = np.asarray(through, float)
        # 3 点を通る円
        ax, ay = p0
        bx, by = m
        cx, cy = p1
        d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) < 1e-14 * max(1.0, float(np.abs([ax, ay, bx, by, cx, cy]).max()) ** 2):
            raise ToolFailure("円弧の 3 点が一直線上にあります")
        ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
        uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
        c = np.array([ux, uy])
        r = float(np.linalg.norm(p0 - c))
        a0 = math.atan2(*(p0 - c)[::-1])
        am = math.atan2(*(m - c)[::-1])
        a1 = math.atan2(*(p1 - c)[::-1])
        s_ccw = (a1 - a0) % (2 * math.pi)
        m_ccw = (am - a0) % (2 * math.pi)
        sweep = s_ccw if m_ccw < s_ccw else s_ccw - 2 * math.pi
    n = max(4, int(math.ceil(abs(sweep) * n_per_rad)))
    ang = a0 + sweep * np.arange(1, n + 1) / n
    pts = c + r * np.stack([np.cos(ang), np.sin(ang)], axis=1)
    pts[-1] = p1
    mid_ang = a0 + 0.5 * sweep
    mid = c + r * np.array([math.cos(mid_ang), math.sin(mid_ang)])
    return pts, mid, c, r, sweep


def _loop_polyline(lp: dict[str, Any]) -> np.ndarray:
    """断面の輪(points / segments / circle)の点列(閉じる点は含めない)。"""
    if lp.get("circle") is not None:
        c = np.asarray(lp["circle"]["center"], float)
        r = float(lp["circle"]["radius"])
        if r <= 0:
            raise ToolFailure("circle の radius は正の値にしてください")
        ang = np.linspace(0, 2 * math.pi, 193)[:-1]
        return c + r * np.stack([np.cos(ang), np.sin(ang)], axis=1)
    if lp.get("points") is not None:
        P = np.asarray(lp["points"], float)
        if P.ndim != 2 or P.shape[1] != 2 or P.shape[0] < 3:
            raise ToolFailure("points には [u, v] を 3 つ以上並べてください")
        if np.allclose(P[0], P[-1]):
            P = P[:-1]
        if P.shape[0] < 3:
            raise ToolFailure("points には異なる点を 3 つ以上並べてください")
        return P
    start = lp.get("start")
    segs = lp.get("segments")
    if start is None or not segs:
        raise ToolFailure("断面の輪は points、start + segments、circle のどれかで指定してください")
    pts = [np.asarray(start, float)]
    cur = pts[0]
    for i, s in enumerate(segs):
        if s.get("line_to") is not None:
            nxt = np.asarray(s["line_to"], float)
            pts.append(nxt)
        elif s.get("arc_to") is not None:
            nxt = np.asarray(s["arc_to"], float)
            arc, *_ = _arc_points(cur, nxt, center=s.get("center"), through=s.get("through"), ccw=s.get("ccw", True))
            pts.extend(list(arc))
        else:
            raise ToolFailure(f"segments[{i}] には line_to か arc_to が要ります")
        cur = nxt
    P = np.array(pts)
    if np.allclose(P[0], P[-1]):
        P = P[:-1]
    return P


def _build_loop(occ, lp: dict[str, Any], point, O, U, V, N, tol) -> int:
    """断面の輪を gmsh の曲線の輪にする。"""
    if lp.get("circle") is not None:
        c = np.asarray(lp["circle"]["center"], float)
        X = O + c[0] * U + c[1] * V
        cv = occ.addCircle(*X, float(lp["circle"]["radius"]), zAxis=list(N), xAxis=list(U))
        return occ.addCurveLoop([cv])
    curves = []
    if lp.get("points") is not None:
        P = _loop_polyline(lp)
        tags = [point(p) for p in P]
        for i in range(len(tags)):
            a, b = tags[i], tags[(i + 1) % len(tags)]
            if a != b:
                curves.append(occ.addLine(a, b))
        return occ.addCurveLoop(curves)
    start = np.asarray(lp["start"], float)
    cur = start
    t_cur = point(start)
    t_start = t_cur
    for s in lp["segments"]:
        if s.get("line_to") is not None:
            nxt = np.asarray(s["line_to"], float)
            t_nxt = point(nxt)
            if t_nxt != t_cur:
                curves.append(occ.addLine(t_cur, t_nxt))
        else:
            nxt = np.asarray(s["arc_to"], float)
            t_nxt = point(nxt)
            _, mid, *_ = _arc_points(cur, nxt, center=s.get("center"), through=s.get("through"), ccw=s.get("ccw", True))
            t_mid = occ.addPoint(*(O + mid[0] * U + mid[1] * V))
            curves.append(occ.addCircleArc(t_cur, t_mid, t_nxt, center=False))
            occ.remove([(0, t_mid)])  # 円弧を決めるためだけの点
        cur, t_cur = nxt, t_nxt
    if t_cur != t_start:
        curves.append(occ.addLine(t_cur, t_start))
    return occ.addCurveLoop(curves)


def _shoelace(P: np.ndarray) -> float:
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _segments_intersect(a0, a1, b0, b1, tol):
    """線分の組 (n, 2) どうしが交わるか(端点の接触は tol 以内なら交わらないとみなす)。"""

    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])

    o1 = orient(a0, a1, b0)
    o2 = orient(a0, a1, b1)
    o3 = orient(b0, b1, a0)
    o4 = orient(b0, b1, a1)
    la = np.linalg.norm(a1 - a0, axis=-1)
    lb = np.linalg.norm(b1 - b0, axis=-1)
    e = tol * (la + lb + tol)
    proper = (((o1 > e) & (o2 < -e)) | ((o1 < -e) & (o2 > e))) & (((o3 > e) & (o4 < -e)) | ((o3 < -e) & (o4 > e)))
    return proper


def _self_intersection(P: np.ndarray, tol: float):
    n = P.shape[0]
    A0 = P
    A1 = np.roll(P, -1, axis=0)
    i, j = np.triu_indices(n, k=2)
    m = ~((i == 0) & (j == n - 1))
    i, j = i[m], j[m]
    if i.size == 0:
        return None
    for s in range(0, i.size, 2_000_000):
        ii, jj = i[s:s + 2_000_000], j[s:s + 2_000_000]
        hit = _segments_intersect(A0[ii], A1[ii], A0[jj], A1[jj], tol)
        if hit.any():
            k = int(np.argmax(hit))
            return int(ii[k]), int(jj[k])
    return None


def _polylines_cross(P: np.ndarray, Q: np.ndarray, tol: float) -> bool:
    A0, A1 = P, np.roll(P, -1, axis=0)
    B0, B1 = Q, np.roll(Q, -1, axis=0)
    i, j = np.meshgrid(np.arange(P.shape[0]), np.arange(Q.shape[0]), indexing="ij")
    i, j = i.ravel(), j.ravel()
    return bool(_segments_intersect(A0[i], A1[i], B0[j], B1[j], tol).any())


def _point_in_polygon(p, P: np.ndarray) -> bool:
    x, y = float(p[0]), float(p[1])
    xs, ys = P[:, 0], P[:, 1]
    xs2, ys2 = np.roll(xs, -1), np.roll(ys, -1)
    cond = (ys > y) != (ys2 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xin = xs + (y - ys) * (xs2 - xs) / (ys2 - ys)
    return bool(np.count_nonzero(cond & (x < xin)) % 2 == 1)
