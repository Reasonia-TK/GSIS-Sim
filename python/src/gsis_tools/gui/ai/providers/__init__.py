"""形式ごとの違いを閉じ込める層(Anthropic の Messages API、OpenAI の Responses API と Chat Completions API)。

どちらも公式の Python SDK(``anthropic``・``openai``。HTTP は httpx2)の非同期のクライアントを使い、
接続先(``base_url``)を指定できる。形式によって違うのは次の点で、この層の外には出さない:

- ツールの定義の形: MCP の ``inputSchema`` → Anthropic の ``input_schema`` / OpenAI の ``function.parameters``
- ツールの結果の返し方: Anthropic は 1 つの user のメッセージに ``tool_result`` を並べる。OpenAI の Chat Completions は
  呼び出しごとに ``role: "tool"`` のメッセージ(画像を入れられないので、後ろに別の user のメッセージで送る)。
  OpenAI の Responses は呼び出しごとに ``function_call_output``(``call_id``。画像も ``output`` の中に入れられる)
- 1 回の応答に複数の呼び出しがあるときの読み方(Anthropic は ``tool_use`` のブロック、OpenAI は ``tool_calls``)
- トークン数の読み方(キャッシュの読み書き、考えの分)
- エラーの種類(401・403・404・400・429・5xx・通信の切断・タイムアウト)

履歴(``messages``)は形式ごとの形のまま持つ(Anthropic の考えのブロックは、そのまま送り返す必要がある)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from ..secrets import ResolvedKey
from ..settings import FORMAT_ANTHROPIC, FORMAT_OPENAI, AiSettings
from ..types import ERR_CONFIG, AssistantTurn, ProviderError, ToolDef, ToolOutcome

StreamCallback = Callable[[str, str], None]  # (種類, 文字列): "text" / "thinking" / "tool" / "info"

MAX_IMAGE_EDGE = 1568  # 画像の長い辺の上限(これより大きい render は縮めて頼む。Anthropic の推奨の大きさ)


class Provider(ABC):
    """AI の API の 1 つの形式。"""

    format: str = ""
    kind: str = ""  # 会話の形の種類(anthropic / openai-chat / openai-responses。AiSettings.provider_kind と同じ)

    def __init__(self, settings: AiSettings):
        self.settings = settings

    @abstractmethod
    def tool_params(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        """ツールの定義(リクエストの tools)。"""

    @abstractmethod
    def user_message(self, text: str) -> list[dict[str, Any]]:
        """ユーザーの文章を履歴に足すメッセージ。"""

    @abstractmethod
    def tool_result_messages(self, outcomes: list[ToolOutcome], send_images: bool) -> list[dict[str, Any]]:
        """ツールの結果を履歴に足すメッセージ(呼び出しの順に、全部をまとめて)。"""

    @abstractmethod
    async def send(self, *, system: str, tools: list[ToolDef], messages: list[dict[str, Any]],
                   on_event: StreamCallback | None = None) -> AssistantTurn:
        """1 回のリクエスト。エラーは :class:`ProviderError`。"""

    @abstractmethod
    async def list_models(self) -> list[str]:
        """接続先のモデルの一覧(``/v1/models``)。"""

    async def aclose(self) -> None:
        close = getattr(getattr(self, "client", None), "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # pragma: no cover
                pass


def make_provider(settings: AiSettings, key: ResolvedKey, *, http_client: Any = None) -> Provider:
    """設定の形式のクライアントを作る(``http_client`` は試験で httpx2 の模擬を渡す)。"""
    if settings.format == FORMAT_ANTHROPIC:
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings, key, http_client=http_client)
    if settings.format == FORMAT_OPENAI:
        if settings.effective_openai_api() == "responses":
            from .openai_responses_provider import OpenAIResponsesProvider

            return OpenAIResponsesProvider(settings, key, http_client=http_client)
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(settings, key, http_client=http_client)
    raise ProviderError(ERR_CONFIG, f"形式が不明です: {settings.format}")


def error_text(exc: BaseException) -> str:
    """SDK の例外から、本文のエラーの説明を取り出す(なければ例外の文字列)。"""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            t = err.get("type") or err.get("code")
            return f"{err['message']}" + (f"({t})" if t else "")
        if isinstance(err, str):
            return err
        if body.get("message"):
            return str(body["message"])
    msg = getattr(exc, "message", None)
    return str(msg or exc)


def retry_after_of(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is None:
        return None
    for k in ("retry-after-ms", "retry-after"):
        v = headers.get(k)
        if v:
            try:
                x = float(v)
            except ValueError:
                return None
            return x / 1000.0 if k.endswith("-ms") else x
    return None


def capped_size(w: int, h: int, edge: int = MAX_IMAGE_EDGE) -> tuple[int, int]:
    m = max(w, h)
    if m <= edge:
        return w, h
    s = edge / m
    return max(200, int(w * s)), max(200, int(h * s))
