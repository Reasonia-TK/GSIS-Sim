"""形式(Anthropic / OpenAI)によらない型: ツールの定義・呼び出し・結果、トークン数、応答、API のエラー。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# 応答が止まった理由(形式ごとの値をこの名前にそろえる)
STOP_END = "end"  # 応答を終えた
STOP_TOOL = "tool_use"  # ツールを呼ぶ
STOP_MAX_TOKENS = "max_tokens"  # 出力の上限で切れた
STOP_REFUSAL = "refusal"  # 断られた(安全の分類など)
STOP_PAUSE = "pause"  # サーバー側で一時停止(Anthropic の pause_turn)
STOP_OTHER = "other"


@dataclass
class ToolDef:
    """AI に渡すツール 1 つ(MCP の tools/list から作る)。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = False
    title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema,
                "read_only": self.read_only, "title": self.title}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ToolDef:
        return cls(name=str(d["name"]), description=str(d.get("description") or ""),
                   input_schema=dict(d.get("input_schema") or {"type": "object", "properties": {}}),
                   read_only=bool(d.get("read_only", False)), title=d.get("title"))


@dataclass
class ToolCall:
    """AI が求めたツールの呼び出し 1 つ。"""

    id: str
    name: str
    arguments: dict[str, Any] | None  # 読めなかったときは None
    raw_arguments: str = ""  # OpenAI 形式の JSON 文字列(そのまま)
    error: str | None = None  # 引数を読めなかった理由


@dataclass
class ToolOutcome:
    """ツールを実行した結果(AI に返すもの)。"""

    call: ToolCall
    text: str  # AI に返す文字列(MCP の応答の JSON か、エラーの説明の JSON)
    is_error: bool = False
    images: list[bytes] = field(default_factory=list)  # PNG(AI に画像を送る設定のときだけ送る)
    seconds: float = 0.0
    data: Any = None  # 応答の JSON を読んだもの(画面用)


@dataclass
class Usage:
    """トークン数。input_tokens はキャッシュの読み書きを含む入力の合計。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0  # 出力のうち考え(reasoning)の分(OpenAI 形式で分かるとき)
    requests: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.requests += other.requests

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Usage:
        d = d or {}
        return cls(**{k: int(d.get(k, 0) or 0) for k in cls.__dataclass_fields__})


@dataclass
class AssistantTurn:
    """AI の応答 1 回(形式ごとの履歴の形は ``native`` に持つ)。"""

    text: str
    thinking: list[str]  # 空でない考えのブロック(Anthropic の途中の報告・要約)
    tool_calls: list[ToolCall]
    stop: str
    usage: Usage
    native: list[dict[str, Any]]  # 履歴に足すメッセージ(形式ごとの形)
    model: str | None = None
    stop_detail: str | None = None  # 断られた理由の分類、元の終了理由など
    notes: list[str] = field(default_factory=list)  # 画面に出す注意(引数の自動の切り替えなど)


# API のエラーの種類
ERR_AUTH = "auth"  # 401
ERR_PERMISSION = "permission"  # 403
ERR_NOT_FOUND = "not_found"  # 404(モデル名の誤りなど)
ERR_BAD_REQUEST = "bad_request"  # 400 など
ERR_RATE_LIMIT = "rate_limit"  # 429
ERR_OVERLOADED = "overloaded"  # 529 / overloaded_error
ERR_SERVER = "server"  # 5xx
ERR_CONNECTION = "connection"  # 通信の切断・名前解決の失敗など
ERR_TIMEOUT = "timeout"
ERR_CONFIG = "config"  # 設定の不足(キーがない、モデルがない)
ERR_OTHER = "other"

RETRYABLE = {ERR_RATE_LIMIT, ERR_OVERLOADED, ERR_SERVER, ERR_CONNECTION, ERR_TIMEOUT}


class ProviderError(Exception):
    """AI の API のエラー(画面に出す説明は伏せ字を通したもの)。"""

    def __init__(self, kind: str, message: str, *, status: int | None = None, retry_after: float | None = None,
                 hint: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.retry_after = retry_after
        self.hint = hint

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE

    def describe(self) -> str:
        """画面に出す日本語の説明。"""
        head = {
            ERR_AUTH: "API キーが受け付けられませんでした(401)。設定のキーを確かめてください",
            ERR_PERMISSION: "このキーでは使えません(403)。キーの権限やモデルの利用の許可を確かめてください",
            ERR_NOT_FOUND: "見つかりません(404)。モデル名と接続先(base URL)を確かめてください",
            ERR_BAD_REQUEST: "リクエストが受け付けられませんでした",
            ERR_RATE_LIMIT: "使用量の上限(429)に達しました。少し待ってから「再送」してください",
            ERR_OVERLOADED: "API のサーバーが混んでいます。少し待ってから「再送」してください",
            ERR_SERVER: "API のサーバーでエラーが起きました。少し待ってから「再送」してください",
            ERR_CONNECTION: "API に接続できませんでした(通信の切断・接続先の誤り)。接続を確かめて「再送」してください",
            ERR_TIMEOUT: "API の応答が時間内に来ませんでした。「再送」するか、設定のタイムアウトを延ばしてください",
            ERR_CONFIG: "設定が足りません",
        }.get(self.kind, "API のエラー")
        parts = [head]
        if self.status is not None and f"({self.status})" not in head:
            parts.append(f"HTTP {self.status}")
        if self.retry_after:
            parts.append(f"{self.retry_after:.0f} 秒後に再送できます")
        text = "。".join(parts)
        if self.message:
            text += f"\n詳細: {self.message}"
        if self.hint:
            text += f"\n{self.hint}"
        return text


class Cancelled(Exception):
    """ユーザーが止めた。"""
