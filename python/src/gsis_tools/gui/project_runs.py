"""プロジェクトの実行入力と GPU の排他制御(Qt なし)。"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import uuid
from pathlib import Path

import tomlkit

from .project import Project, case_document

SUBCOMMANDS = {"gsis": "run", "cis": "cis-run", "nsf": "pbc-run"}


def solver_repository(solver: Path, fallback: Path) -> Path:
    """外部プロジェクトでも、ソルバーを置いたリポジトリのロックを使う。"""
    for parent in Path(solver).resolve().parents:
        if (parent / "CLAUDE.md").is_file() and (parent / "CMakeLists.txt").is_file():
            return parent
    return Path(fallback).resolve()


def next_run_directory(project: Project) -> Path:
    base = project.asset("runs")
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = base / stamp
    index = 2
    while path.exists():
        path = base / f"{stamp}_{index}"
        index += 1
    return path


def prepare_run(project: Project, out: Path) -> Path:
    """入力を固定する。メッシュはプロジェクト内の完成品を絶対パスで参照する。"""
    reason = project.blocked_reason("run")
    if reason:
        raise ValueError(reason)
    mesh = project.asset(project.mesh["path"])
    if not mesh.is_file():
        raise ValueError("完成したメッシュがありません")
    doc = case_document(project)
    doc["case"]["mesh"] = str(mesh)
    out.mkdir(parents=True, exist_ok=False)
    path = out / "case.toml"
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    (out / "gui_run.json").write_text(json.dumps({
        "example": project.geometry.get("example"), "project": project.name,
        "created": dt.datetime.now().astimezone().isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def record_command(out: Path, command: list[str]) -> None:
    path = out / "gui_run.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["command"] = command
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


class GpuLock:
    """mkdir で取得し、今回の所有トークンを確認したロックだけを解除する。

    PID の再利用や GUI 終了後のソルバー生存を考慮し、残存ロックは自動回収しない。
    """

    def __init__(self, path: Path, description: str):
        self.path = Path(path)
        self.owner = dict(application="gsis-gui", token=uuid.uuid4().hex, host=socket.gethostname(),
                          gui_pid=os.getpid(), description=description,
                          started=dt.datetime.now().astimezone().isoformat())
        self.acquired = False

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.mkdir()
        except FileExistsError:
            return False
        self.acquired = True
        try:
            self._write_owner()
        except OSError:
            self.path.rmdir()
            self.acquired = False
            raise
        return True

    def _write_owner(self):
        temporary = self.path / f"owner.{self.owner['token']}.tmp"
        try:
            temporary.write_text(json.dumps(self.owner, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path / "owner.txt")
        finally:
            temporary.unlink(missing_ok=True)

    def set_solver_pid(self, pid: int):
        if self.acquired:
            self.owner["solver_pid"] = pid
            self._write_owner()

    def owner_text(self) -> str:
        try:
            return (self.path / "owner.txt").read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            return "owner.txt はありません、または読み取れません"

    def owner_summary(self) -> str:
        raw = self.owner_text()
        try:
            owner = json.loads(raw)
            return f"{owner.get('description', '計算名なし')} / 開始: {owner.get('started', '不明')}"
        except (ValueError, AttributeError):
            return raw[:500]

    def release(self) -> bool:
        if not self.acquired:
            return False
        try:
            owner = json.loads((self.path / "owner.txt").read_text(encoding="utf-8"))
            if owner.get("token") != self.owner["token"]:
                return False
            # 他のファイルがある場合は削除せず、利用者に残す。
            if set(p.name for p in self.path.iterdir()) != {"owner.txt"}:
                return False
            (self.path / "owner.txt").unlink()
            self.path.rmdir()
            self.acquired = False
            return True
        except (OSError, ValueError, AttributeError):
            return False
