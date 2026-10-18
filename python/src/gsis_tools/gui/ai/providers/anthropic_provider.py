"""Anthropic 形式(Messages API、公式 SDK ``anthropic`` の非同期のクライアント)。

リクエストの形(2026-10-18 に platform.claude.com の資料で確かめた):

- ``POST /v1/messages``。ヘッダー ``x-api-key``・``anthropic-version``(SDK が付ける)
- ``tools``: ``[{name, description, input_schema}]``。応答の ``tool_use`` ブロック(``id``・``name``・``input``)が
  複数あれば、結果は次の 1 つの user のメッセージに ``tool_result``(``tool_use_id``・``content``・``is_error``)を
  呼び出しの順に並べる(``tool_result`` を本文の先頭に置く)。``content`` には text と image のブロックを入れられる
- プロンプトキャッシュ: ``tools`` の最後の定義と ``system`` のブロックに ``cache_control: {type: ephemeral}``、
  会話はリクエストの最上位の ``cache_control``(自動。区切りは合わせて 3 つ、上限は 4)。トークン数は
  ``usage.input_tokens``(最後の区切りより後)+ ``cache_creation_input_tokens`` + ``cache_read_input_tokens``
- 考え: Fable 5.1・Opus 5.5 などは常に考える(``thinking`` を送らないか ``{type: adaptive}``)。応答の
  ``thinking`` ブロックは変えずに送り返す(履歴は足すだけにする。前の手順を書き換えると考えのブロックが
  無効になる)。``display: "updates"``(beta)で、ツールの呼び出しの間の途中の報告を受け取る
- 断られたとき: ``stop_reason: "refusal"``。公式の接続先では ``fallbacks: "default"``(beta)で別のモデルが続ける。
  途中で切り替わったときは、最後の ``fallback`` ブロックより前の text 以外のブロックを送り返さない
- ストリーミング(SSE)は ``messages.stream()`` の ``get_final_message()`` で最後の形を得る
"""

from __future__ import annotations

import base64
from typing import Any

from ..secrets import ResolvedKey, redact
from ..settings import BETA_FALLBACK_DEFAULT, BETA_THINKING_UPDATES, FORMAT_ANTHROPIC, AiSettings
from ..types import (
    ERR_AUTH,
    ERR_BAD_REQUEST,
    ERR_CONFIG,
    ERR_CONNECTION,
    ERR_NOT_FOUND,
    ERR_OTHER,
    ERR_OVERLOADED,
    ERR_PERMISSION,
    ERR_RATE_LIMIT,
    ERR_SERVER,
    ERR_TIMEOUT,
    STOP_END,
    STOP_MAX_TOKENS,
    STOP_OTHER,
    STOP_PAUSE,
    STOP_REFUSAL,
    STOP_TOOL,
    AssistantTurn,
    ProviderError,
    ToolCall,
    ToolDef,
    ToolOutcome,
    Usage,
)
from . import Provider, StreamCallback, error_text, retry_after_of

_STOP = {"end_turn": STOP_END, "stop_sequence": STOP_END, "tool_use": STOP_TOOL, "max_tokens": STOP_MAX_TOKENS,
         "model_context_window_exceeded": STOP_MAX_TOKENS, "refusal": STOP_REFUSAL, "pause_turn": STOP_PAUSE}


class AnthropicProvider(Provider):
    format = FORMAT_ANTHROPIC
    kind = "anthropic"

    def __init__(self, settings: AiSettings, key: ResolvedKey, *, http_client: Any = None):
        super().__init__(settings)
        if key.secret is None:
            raise ProviderError(ERR_CONFIG, "API キーがありません",
                                hint="設定の「API キー」に入力するか、環境変数 ANTHROPIC_API_KEY を設定してください")
        import anthropic

        self._sdk = anthropic
        # キーと接続先は明示して渡す(SDK に環境変数・プロファイルの資格情報を探させない)
        self.client = anthropic.AsyncAnthropic(api_key=key.secret.reveal(), base_url=settings.base_url,
                                               timeout=settings.timeout_s, max_retries=settings.max_retries,
                                               http_client=http_client)

    # ------------------------------------------------------------------ 形
    def tool_params(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        out = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        if out and self.settings.prompt_cache:
            out[-1] = {**out[-1], "cache_control": {"type": "ephemeral"}}
        return out

    def user_message(self, text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": [{"type": "text", "text": text}]}]

    def tool_result_messages(self, outcomes: list[ToolOutcome], send_images: bool) -> list[dict[str, Any]]:
        blocks = []
        for o in outcomes:
            content: list[dict[str, Any]] = [{"type": "text", "text": o.text or "(結果なし)"}]
            if send_images:
                for img in o.images:
                    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                                "data": base64.b64encode(img).decode("ascii")}})
            b: dict[str, Any] = {"type": "tool_result", "tool_use_id": o.call.id, "content": content}
            if o.is_error:
                b["is_error"] = True
            blocks.append(b)
        return [{"role": "user", "content": blocks}] if blocks else []

    def request_kwargs(self, system: str, tools: list[ToolDef], messages: list[dict[str, Any]]
                       ) -> tuple[dict[str, Any], list[str]]:
        """リクエストの引数と beta の名前(試験でも使う)。"""
        s = self.settings
        sys_block: dict[str, Any] = {"type": "text", "text": system}
        if s.prompt_cache:
            sys_block["cache_control"] = {"type": "ephemeral"}
        kw: dict[str, Any] = {"model": s.model, "max_tokens": s.max_tokens, "system": [sys_block],
                              "messages": messages}
        tl = self.tool_params(tools)
        if tl:
            kw["tools"] = tl
        if s.prompt_cache:
            kw["cache_control"] = {"type": "ephemeral"}  # 会話の自動キャッシュ(最後のブロックに区切り)
        betas: list[str] = []
        disp = s.effective_thinking_display()
        if disp == "updates":
            kw["thinking"] = {"type": "adaptive", "display": "updates"}
            betas.append(BETA_THINKING_UPDATES)
        elif disp == "summarized":
            kw["thinking"] = {"type": "adaptive", "display": "summarized"}
        if s.effort:
            kw["output_config"] = {"effort": s.effort}
        if s.use_fallbacks():
            kw["fallbacks"] = "default"
            betas.append(BETA_FALLBACK_DEFAULT)
        extra = s.extra_body()
        if extra:
            kw["extra_body"] = extra
        return kw, betas

    # ------------------------------------------------------------------ 送受信
    async def send(self, *, system: str, tools: list[ToolDef], messages: list[dict[str, Any]],
                   on_event: StreamCallback | None = None) -> AssistantTurn:
        kw, betas = self.request_kwargs(system, tools, messages)
        api = self.client.beta.messages if betas else self.client.messages
        if betas:
            kw["betas"] = betas
        emit = on_event or (lambda _k, _t: None)
        try:
            if self.settings.stream:
                async with api.stream(**kw) as stream:
                    async for ev in stream:
                        t = getattr(ev, "type", "")
                        if t == "text":
                            emit("text", ev.text)
                        elif t == "thinking":
                            emit("thinking", ev.thinking)
                        elif t == "content_block_start":
                            cb = ev.content_block
                            if getattr(cb, "type", "") == "tool_use":
                                emit("tool", cb.name)
                            elif getattr(cb, "type", "") == "fallback":
                                emit("info", "安全の分類で断られたので、別のモデルに切り替わりました")
                    msg = await stream.get_final_message()
            else:
                msg = await api.create(timeout=self.settings.timeout_s, **kw)
        except ProviderError:
            raise
        except Exception as exc:
            raise self.map_error(exc) from None
        return self.to_turn(msg)

    def to_turn(self, msg: Any) -> AssistantTurn:
        blocks = [b.to_dict(mode="json") if hasattr(b, "to_dict") else dict(b) for b in (msg.content or [])]
        last_fb = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
        echo: list[dict[str, Any]] = []
        for i, b in enumerate(blocks):
            if i < last_fb and b.get("type") not in ("text", "fallback"):
                continue  # 切り替わる前の考え・ツールの呼び出しは送り返さない(公式の規則)
            echo.append(b)
        text = "".join(b.get("text", "") for b in echo if b.get("type") == "text")
        thinking = [b["thinking"] for b in echo if b.get("type") == "thinking" and b.get("thinking")]
        calls = []
        for b in echo:
            if b.get("type") == "tool_use":
                inp = b.get("input")
                calls.append(ToolCall(id=str(b.get("id")), name=str(b.get("name")),
                                      arguments=inp if isinstance(inp, dict) else None,
                                      error=None if isinstance(inp, dict) else "引数がオブジェクトではありません"))
        stop = _STOP.get(getattr(msg, "stop_reason", None) or "", STOP_OTHER)
        detail = None
        sd = getattr(msg, "stop_details", None)
        if sd is not None:
            detail = getattr(sd, "category", None) or (sd.get("category") if isinstance(sd, dict) else None)
        u = getattr(msg, "usage", None)
        usage = Usage(requests=1)
        if u is not None:
            cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
            cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
            usage.input_tokens = int(getattr(u, "input_tokens", 0) or 0) + cw + cr
            usage.cache_write_tokens = cw
            usage.cache_read_tokens = cr
            usage.output_tokens = int(getattr(u, "output_tokens", 0) or 0)
        native = [{"role": "assistant", "content": echo}] if echo else []
        if stop == STOP_TOOL and not calls:
            stop = STOP_END
        return AssistantTurn(text=text, thinking=thinking, tool_calls=calls, stop=stop, usage=usage, native=native,
                             model=getattr(msg, "model", None), stop_detail=detail)

    async def list_models(self) -> list[str]:
        out: list[str] = []
        try:
            async for m in self.client.models.list(limit=100):
                out.append(m.id)
        except Exception as exc:
            raise self.map_error(exc) from None
        return out

    # ------------------------------------------------------------------ エラー
    def map_error(self, exc: BaseException) -> ProviderError:
        a = self._sdk
        msg = redact(error_text(exc))
        status = getattr(exc, "status_code", None)
        ra = retry_after_of(exc)
        if isinstance(exc, a.AuthenticationError):
            return ProviderError(ERR_AUTH, msg, status=401)
        if isinstance(exc, a.PermissionDeniedError):
            return ProviderError(ERR_PERMISSION, msg, status=403)
        if isinstance(exc, a.NotFoundError):
            return ProviderError(ERR_NOT_FOUND, msg, status=404)
        if isinstance(exc, a.RateLimitError):
            return ProviderError(ERR_RATE_LIMIT, msg, status=429, retry_after=ra)
        if isinstance(exc, a.APIStatusError):
            etype = getattr(exc, "type", None)
            body = getattr(exc, "body", None)
            if not etype and isinstance(body, dict) and isinstance(body.get("error"), dict):
                etype = body["error"].get("type")
            if etype == "overloaded_error" or status == 529:
                return ProviderError(ERR_OVERLOADED, msg, status=status, retry_after=ra)
            if etype == "rate_limit_error":
                return ProviderError(ERR_RATE_LIMIT, msg, status=status, retry_after=ra)
            if status is not None and status >= 500:
                return ProviderError(ERR_SERVER, msg, status=status, retry_after=ra)
            if etype in ("api_error",):
                return ProviderError(ERR_SERVER, msg, status=status)
            return ProviderError(ERR_BAD_REQUEST, msg, status=status)
        if isinstance(exc, a.APITimeoutError):
            return ProviderError(ERR_TIMEOUT, msg)
        if isinstance(exc, a.APIConnectionError):
            return ProviderError(ERR_CONNECTION, msg)
        if isinstance(exc, ValueError):
            return ProviderError(ERR_CONFIG, msg)
        return ProviderError(ERR_OTHER, f"{type(exc).__name__}: {msg}")
