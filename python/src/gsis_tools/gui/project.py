"""版付きのプロジェクトと 6 段階の状態、case.toml の生成(Qt なし)。"""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

from .general_settings import GeneralSettings, velocity_grid

FORMAT = "gsis-project"
VERSION = 1
STAGES = ("geometry", "settings", "boundaries", "mesh", "run", "results")
LABELS = ("形状", "設定", "境界条件", "メッシュ", "実行", "結果")
STATE_LABELS = {"pending": "○ 未着手", "done": "✓ 済", "redo": "! 要やり直し"}


@dataclass
class Project:
    name: str = "新しい計算"
    directory: Path | None = None
    geometry: dict[str, Any] = field(default_factory=dict)
    settings: GeneralSettings = field(default_factory=GeneralSettings)
    boundaries: list[dict[str, Any]] = field(default_factory=list)
    mesh: dict[str, Any] = field(default_factory=lambda: {"path": "mesh/fluid.pmesh"})
    stages: dict[str, str] = field(default_factory=lambda: dict.fromkeys(STAGES, "pending"))
    dirty: bool = False

    def blocked_reason(self, stage: str) -> str:
        before = STAGES[:STAGES.index(stage)]
        missing = [LABELS[STAGES.index(k)] for k in before if self.stages[k] != "done"]
        if missing:
            return "先に「" + "・".join(missing) + "」を完了してください"
        if stage in ("run", "results") and self.mesh.get("cells"):
            checked = getattr(self, "_solver_check", None)
            if checked is not None and not checked.get("ok"):
                return "C++ の入力検査にエラーがあります。設定・メッシュの検査結果を確認してください"
            if (checked and checked.get("mesh_loaded") and checked.get("memory")
                    and self.settings.mode != "nsf" and self.settings.kinetic.get("backend", "cuda") != "cpu"
                    and not checked["memory"]["fits"]):
                return "VRAM の予算を超えています。セル数または速度点を減らしてください"
        return ""

    def change(self, stage: str) -> None:
        self.stages[stage] = "redo" if self.stages[stage] == "done" else self.stages[stage]
        for later in STAGES[STAGES.index(stage) + 1:]:
            if self.stages[later] == "done":
                self.stages[later] = "redo"
        # 古いメッシュは履歴用に残すが、再生成まで参照しない。
        if STAGES.index(stage) <= STAGES.index("mesh"):
            self.mesh.pop("validated", None)
            self.__dict__.pop("_solver_check", None)
        self.dirty = True

    def complete(self, stage: str) -> None:
        reason = self.blocked_reason(stage)
        if reason:
            raise ValueError(reason)
        self.stages[stage] = "done"
        self.dirty = True

    def asset(self, relative: str) -> Path:
        if self.directory is None:
            raise ValueError("プロジェクトのフォルダがありません")
        root = self.directory.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("プロジェクトのファイルはフォルダ内に置いてください")
        return path


def case_document(project: Project) -> tomlkit.TOMLDocument:
    """プロジェクト入力からソルバー用 TOML を作る。保存前でも単体で呼べる。

    境界・メッシュが未完了でも編集中の入力を書ける。実行可能かは段階と check-case で検査する。
    """
    s = project.settings
    n, ranges = velocity_grid(s)
    doc = tomlkit.document()
    doc.add(tomlkit.comment("GUI が project.toml から生成。変更はプロジェクトの画面で行ってください"))
    doc["case"] = dict(name=project.name, mesh=project.mesh.get("path", "mesh/fluid.pmesh"), mode=s.mode)
    doc["gas"] = {k: getattr(s, k) for k in ("molecular_mass", "mu_ref", "T_ref", "omega")}
    doc["conditions"] = dict(L_ref=s.L_ref, T0=s.T0, p_ref=s.p_ref)
    doc["velocity_grid"] = dict(auto=True) if s.velocity_auto else dict(n=n, range=ranges)
    doc["gsis"] = {**s.gsis_extra, **{k: getattr(s, k) for k in ("n_cis", "max_pbc", "cfl_pbc", "tolerance")}}
    doc["kinetic"] = {**s.kinetic, "velocity_chunk": s.velocity_chunk}
    if s.macro:
        doc["macro"] = s.macro
    aot = tomlkit.aot()
    for b in project.boundaries:
        table = tomlkit.table()
        for key, value in b.items():
            if key in ("patch", "type", "temperature", "velocity", "pressure", "tangential_velocity",
                       "accommodation", "mass_flow") and value is not None:
                table[key] = value
        aot.append(table)
    doc["boundary"] = aot
    return doc


def _atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _without_none(value):
    """TOML に null はないため、任意の法線などの欠測はキーの省略で表す。"""
    if isinstance(value, dict):
        return {k: _without_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_none(v) for v in value]
    return value


def save_project(project: Project, directory: Path | None = None) -> Path:
    target = Path(directory or project.directory or "").resolve()
    if directory is None and project.directory is None:
        raise ValueError("保存先を指定してください")
    old = project.directory.resolve() if project.directory else None
    different = old is not None and target != old
    if different:
        if target.is_relative_to(old) or old.is_relative_to(target):
            raise ValueError("名前を付けて保存する先は、元のフォルダと入れ子にしないでください")
        if target.exists() and any(target.iterdir()):
            raise ValueError("名前を付けて保存する先は空のフォルダにしてください")
    candidate = copy.copy(project)
    candidate.directory = target
    # ファイルを作る前に入力と全ての管理対象パスを検査する。
    case_text = tomlkit.dumps(case_document(candidate))
    for key in ("source", "domain", "ai_state", "ai_conversation"):
        if candidate.geometry.get(key):
            candidate.asset(candidate.geometry[key])
    candidate.asset(candidate.mesh.get("path", "mesh/fluid.pmesh"))
    for key in ("quality_path", "report"):
        if candidate.mesh.get(key):
            candidate.asset(candidate.mesh[key])
    doc = tomlkit.document()
    doc["format"] = FORMAT
    doc["version"] = VERSION
    doc["project"] = {"name": project.name}
    doc["stages"] = project.stages
    doc["geometry"] = _without_none(project.geometry)
    doc["settings"] = project.settings.to_dict()
    doc["mesh"] = project.mesh
    project_text = tomlkit.dumps(doc)
    if different:
        shutil.copytree(old, target, dirs_exist_ok=True)
    for part in ("geometry", "mesh", "runs"):
        (target / part).mkdir(parents=True, exist_ok=True)
    _atomic_write(target / "boundaries.json", json.dumps(project.boundaries, ensure_ascii=False, indent=2))
    _atomic_write(target / "case.toml", case_text)
    _atomic_write(target / "project.toml", project_text)
    project.directory = target
    project.dirty = False
    return target / "project.toml"


def load_project(path: Path) -> Project:
    path = Path(path)
    if path.is_dir():
        path = path / "project.toml"
    doc = tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()
    if doc.get("format") != FORMAT or type(doc.get("version")) is not int or doc["version"] != VERSION:
        raise ValueError("この版の project.toml は開けません(format / version を確認してください)")
    # G1 の設定には既定壁温度がなかったので、当時の基準温度から補う。
    doc["settings"].setdefault("wall_temperature", doc["settings"].get("T0", 273.15))
    p = Project(name=doc["project"]["name"], directory=path.parent.resolve(), geometry=doc.get("geometry", {}),
                settings=GeneralSettings(**doc["settings"]), mesh=doc.get("mesh", {"path": "mesh/fluid.pmesh"}))
    p.settings.validate()
    p.stages.update(doc.get("stages", {}))
    if set(p.stages) != set(STAGES) or any(v not in STATE_LABELS for v in p.stages.values()):
        raise ValueError("段階の状態が不正です")
    for key in ("source", "domain", "ai_state", "ai_conversation"):
        if p.geometry.get(key):
            p.asset(p.geometry[key])
    p.asset(p.mesh.get("path", "mesh/fluid.pmesh"))
    for key in ("quality_path", "report"):
        if p.mesh.get(key):
            p.asset(p.mesh[key])
    bpath = p.asset("boundaries.json")
    p.boundaries = json.loads(bpath.read_text(encoding="utf-8")) if bpath.exists() else []
    if not isinstance(p.boundaries, list) or any(not isinstance(b, dict) for b in p.boundaries):
        raise ValueError("boundaries.json は境界条件の配列にしてください")
    # 編集途中の状態でも開ける。入力が失われた場合は後段を無効にする。
    if p.geometry.get("domain") and not p.asset(p.geometry["domain"]).is_file():
        p.change("geometry")
    if p.geometry.get("accepted") is False and p.stages["geometry"] == "done":
        p.change("geometry")
    if p.stages["mesh"] == "done" and not p.asset(p.mesh.get("path", "mesh/fluid.pmesh")).is_file():
        p.change("mesh")
    for key in STAGES:
        if p.stages[key] == "done" and p.blocked_reason(key):
            p.change(key)
    return p
