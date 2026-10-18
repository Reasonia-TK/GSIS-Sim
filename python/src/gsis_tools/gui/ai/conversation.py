"""会話: 形式ごとの履歴(API に送る messages)、固定したシステムとツールの定義、表示用の記録、トークン数。

保存するファイル(JSON)には API キーを入れない(書く前に :func:`~.secrets.redact_obj` も通す)。
モデルの状態(自動保存の手順の履歴)も入れておき、開くときに読み込めるようにする。
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .secrets import redact_obj
from .types import ToolDef, Usage

FILE_FORMAT = "gsis-gui-ai-conversation"
FILE_VERSION = 1


@dataclass
class Conversation:
    format: str  # 会話の形の種類(anthropic / openai-chat / openai-responses。履歴の形が違う)
    system: str = ""
    tools: list[ToolDef] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)  # 画面の会話の欄に出すもの
    usage: Usage = field(default_factory=Usage)
    last_usage: Usage = field(default_factory=Usage)
    created: str = field(default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))
    models: list[str] = field(default_factory=list)  # 使ったモデル(順に)

    @property
    def frozen(self) -> bool:
        return bool(self.system)

    @property
    def empty(self) -> bool:
        return not self.messages

    def freeze(self, system: str, tools: list[ToolDef]) -> None:
        """会話の始めにシステムとツールの定義を決める(途中では変えない)。"""
        if not self.frozen:
            self.system = system
            self.tools = list(tools)

    def add_record(self, rec: dict[str, Any]) -> None:
        self.records.append(rec)

    def to_json(self, *, model_state: Any = None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        data = {
            "format": FILE_FORMAT, "version": FILE_VERSION,
            "saved": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "created": self.created, "provider_format": self.format, "models": self.models,
            "settings": settings or {},
            "system": self.system, "tools": [t.to_dict() for t in self.tools],
            "messages": self.messages, "records": self.records,
            "usage": self.usage.to_dict(), "model_state": model_state,
        }
        return redact_obj(data)

    def save(self, path: Path, **kw: Any) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_json(**kw), ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        tmp.replace(path)
        return path

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Conversation:
        if data.get("format") != FILE_FORMAT:
            raise ValueError(f"会話のファイルではありません(format={data.get('format')!r})")
        if int(data.get("version", 0)) != FILE_VERSION:
            raise ValueError(f"会話のファイルの版が違います(version={data.get('version')!r})")
        fmt = str(data.get("provider_format") or "")
        fmt = {"openai": "openai-chat"}.get(fmt, fmt)  # 2026-10-18 の初めの版の会話(Chat Completions だけのとき)
        conv = cls(format=fmt, system=str(data.get("system") or ""),
                   tools=[ToolDef.from_dict(t) for t in data.get("tools") or []],
                   messages=list(data.get("messages") or []), records=list(data.get("records") or []),
                   usage=Usage.from_dict(data.get("usage")), created=str(data.get("created") or ""),
                   models=[str(m) for m in data.get("models") or []])
        return conv

    @classmethod
    def load(cls, path: Path) -> tuple[Conversation, dict[str, Any]]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_json(data), data
