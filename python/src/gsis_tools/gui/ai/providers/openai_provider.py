"""OpenAI 形式(Chat Completions API、公式 SDK ``openai`` の非同期のクライアント)。OpenAI 互換のサーバーにも使う。

リクエストの形(2026-10-18 に SDK ``openai`` 3.35.1 の型と説明で確かめた。型は OpenAI の OpenAPI の仕様から
生成されている):

- ``POST {base_url}/chat/completions``。ヘッダー ``Authorization: Bearer <キー>``
- ``tools``: ``[{type: "function", function: {name, description, parameters}}]``
- 応答の ``message.tool_calls``(``id``・``function.name``・``function.arguments`` は JSON の文字列)が複数あれば、
  呼び出しごとに ``{role: "tool", tool_call_id, content}`` を順に足す。tool のメッセージの ``content`` は
  文字列か text の部品だけ(画像は入れられない)ので、画像は後ろの user のメッセージ(``image_url`` の data URL)で送る
- 出力の上限: ``max_tokens`` は非推奨で o 系などでは使えない。``max_completion_tokens``(考えの分を含む)を使う。
  互換サーバーには ``max_tokens`` しか読まないものがあるので、設定で選べる(400 の説明に両方の名前があれば
  もう一方に切り替えて 1 回だけ送り直す)
- ストリーミング: ``stream: true`` と ``stream_options: {include_usage: true}``(最後の断片に usage)。
  ツールの呼び出しは ``delta.tool_calls[].index`` ごとに、``id``・``name``(最初の断片)と ``arguments`` の断片を足す
- トークン数: ``usage.prompt_tokens``(キャッシュを含む)・``prompt_tokens_details.cached_tokens``・
  ``completion_tokens``(考えを含む)・``completion_tokens_details.reasoning_tokens``
- キーなし(ローカルのサーバー): ``Authorization`` のヘッダーを送らない。公式以外の接続先には、環境変数の
  組織・プロジェクトの ID(``OpenAI-Organization``・``OpenAI-Project``)も送らない
"""

from __future__ import annotations

import base64
import json
from typing import Any

from ..secrets import ResolvedKey, redact
from ..settings import FORMAT_OPENAI, AiSettings
from ..types import (
    ERR_AUTH,
    ERR_BAD_REQUEST,
    ERR_CONFIG,
    ERR_CONNECTION,
    ERR_NOT_FOUND,
    ERR_OTHER,
    ERR_PERMISSION,
    ERR_RATE_LIMIT,
    ERR_SERVER,
    ERR_TIMEOUT,
    STOP_END,
    STOP_MAX_TOKENS,
    STOP_OTHER,
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

_STOP = {"stop": STOP_END, "tool_calls": STOP_TOOL, "function_call": STOP_TOOL, "length": STOP_MAX_TOKENS,
         "content_filter": STOP_REFUSAL}


async def _no_key() -> str:
    return ""


def make_openai_client(settings: AiSettings, key: ResolvedKey, http_client: Any = None) -> tuple[Any, dict[str, Any]]:
    """OpenAI の SDK の非同期のクライアントと、毎回のリクエストに付けるヘッダー(Chat Completions と Responses で共通)。

    キーは明示して渡す(SDK に環境変数のキーを探させない)。キーなしのときは ``Authorization`` を送らない。
    公式以外の接続先には、SDK が環境変数から付ける組織・プロジェクトの ID を送らない。
    """
    import openai

    extra_headers: dict[str, Any] = {}
    kw: dict[str, Any] = dict(base_url=settings.base_url, timeout=settings.timeout_s,
                              max_retries=settings.max_retries, http_client=http_client)
    if key.source == "no_key":
        kw["api_key"] = _no_key
        extra_headers["Authorization"] = openai.Omit()
    elif key.secret is None:
        raise ProviderError(ERR_CONFIG, "API キーがありません",
                            hint="設定の「API キー」に入力するか(公式の接続先なら環境変数 OPENAI_API_KEY)、"
                                 "ローカルのサーバーなら「キーなし」を選んでください")
    else:
        kw["api_key"] = key.secret.reveal()
    if not settings.is_official():
        extra_headers["OpenAI-Organization"] = openai.Omit()
        extra_headers["OpenAI-Project"] = openai.Omit()
    return openai.AsyncOpenAI(**kw), extra_headers


def map_openai_error(sdk: Any, exc: BaseException) -> ProviderError:
    """OpenAI の SDK の例外 → :class:`ProviderError`(説明は伏せ字を通す)。"""
    o = sdk
    msg = redact(error_text(exc))
    status = getattr(exc, "status_code", None)
    ra = retry_after_of(exc)
    if isinstance(exc, o.AuthenticationError):
        return ProviderError(ERR_AUTH, msg, status=401)
    if isinstance(exc, o.PermissionDeniedError):
        return ProviderError(ERR_PERMISSION, msg, status=403)
    if isinstance(exc, o.NotFoundError):
        return ProviderError(ERR_NOT_FOUND, msg, status=404)
    if isinstance(exc, o.RateLimitError):
        return ProviderError(ERR_RATE_LIMIT, msg, status=429, retry_after=ra)
    if isinstance(exc, o.APIStatusError):
        if status is not None and status >= 500:
            return ProviderError(ERR_SERVER, msg, status=status, retry_after=ra)
        return ProviderError(ERR_BAD_REQUEST, msg, status=status)
    if isinstance(exc, o.APITimeoutError):
        return ProviderError(ERR_TIMEOUT, msg)
    if isinstance(exc, o.APIConnectionError):
        return ProviderError(ERR_CONNECTION, msg)
    if isinstance(exc, o.APIError):  # ストリーミングの途中の error など(HTTP の状態は 200)
        return ProviderError(ERR_SERVER, msg)
    if isinstance(exc, o.OpenAIError):
        return ProviderError(ERR_CONFIG, msg)
    return ProviderError(ERR_OTHER, f"{type(exc).__name__}: {msg}")


class OpenAIProvider(Provider):
    """OpenAI 形式の Chat Completions API。"""

    format = FORMAT_OPENAI
    kind = "openai-chat"

    def __init__(self, settings: AiSettings, key: ResolvedKey, *, http_client: Any = None):
        super().__init__(settings)
        import openai

        self._sdk = openai
        self.client, self.extra_headers = make_openai_client(settings, key, http_client)
        self.token_param = settings.effective_token_param()
        self.use_stream_options = True

    # ------------------------------------------------------------------ 形
    def tool_params(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.input_schema}} for t in tools]

    def user_message(self, text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": text}]

    def tool_result_messages(self, outcomes: list[ToolOutcome], send_images: bool) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "tool", "tool_call_id": o.call.id, "content": o.text or "(結果なし)"}
                                      for o in outcomes]
        if send_images:
            parts: list[dict[str, Any]] = []
            for o in outcomes:
                for img in o.images:
                    parts.append({"type": "text", "text": f"ツール {o.call.name}(呼び出し {o.call.id})の画像:"})
                    parts.append({"type": "image_url", "image_url": {
                        "url": "data:image/png;base64," + base64.b64encode(img).decode("ascii"), "detail": "auto"}})
            if parts:
                msgs.append({"role": "user", "content": parts})
        return msgs

    def request_kwargs(self, system: str, tools: list[ToolDef], messages: list[dict[str, Any]]) -> dict[str, Any]:
        s = self.settings
        kw: dict[str, Any] = {"model": s.model, "messages": [{"role": "system", "content": system}, *messages]}
        tl = self.tool_params(tools)
        if tl:
            kw["tools"] = tl
        if self.token_param in ("max_completion_tokens", "max_tokens"):
            kw[self.token_param] = s.max_tokens
        if s.stream:
            kw["stream"] = True
            if self.use_stream_options:
                kw["stream_options"] = {"include_usage": True}
        extra = s.extra_body()
        if extra:
            kw["extra_body"] = extra
        if self.extra_headers:
            kw["extra_headers"] = dict(self.extra_headers)
        return kw

    # ------------------------------------------------------------------ 送受信
    async def send(self, *, system: str, tools: list[ToolDef], messages: list[dict[str, Any]],
                   on_event: StreamCallback | None = None) -> AssistantTurn:
        notes: list[str] = []
        switched: set[str] = set()
        while True:
            kw = self.request_kwargs(system, tools, messages)
            try:
                turn = await self._send_once(kw, on_event)
                turn.notes.extend(notes)
                return turn
            except ProviderError as err:
                fix = self._auto_fix(err, switched)
                if fix is None:
                    raise
                notes.append(fix)

    def _auto_fix(self, err: ProviderError, done: set[str]) -> str | None:
        """互換サーバーとの引数の違いを 1 回だけ直す(直せたら説明を返す)。"""
        if err.kind != ERR_BAD_REQUEST:
            return None
        m = (err.message or "").lower()
        if "max_tokens" in m and "max_completion_tokens" in m and "token_param" not in done:
            done.add("token_param")
            old = self.token_param
            self.token_param = "max_tokens" if old == "max_completion_tokens" else "max_completion_tokens"
            return (f"接続先が {old} を受け付けなかったので {self.token_param} に切り替えて送り直しました"
                    "(設定の「出力の上限の引数」で固定できます)")
        if "stream_options" in m and self.use_stream_options and self.settings.stream and "stream_options" not in done:
            done.add("stream_options")
            self.use_stream_options = False
            return "接続先が stream_options を受け付けなかったので、外して送り直しました(トークン数は分からないことがあります)"
        return None

    async def _send_once(self, kw: dict[str, Any], on_event: StreamCallback | None) -> AssistantTurn:
        emit = on_event or (lambda _k, _t: None)
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish: str | None = None
        usage_obj: Any = None
        model: str | None = None
        refusal = ""
        try:
            if kw.get("stream"):
                stream = await self.client.chat.completions.create(**kw)
                async with stream:
                    async for chunk in stream:
                        model = getattr(chunk, "model", None) or model
                        if getattr(chunk, "usage", None) is not None:
                            usage_obj = chunk.usage
                        for ch in chunk.choices or []:
                            if getattr(ch, "index", 0) not in (0, None):
                                continue
                            d = ch.delta
                            if d is not None:
                                if d.content:
                                    text_parts.append(d.content)
                                    emit("text", d.content)
                                r = _reasoning_of(d)
                                if r:
                                    thinking_parts.append(r)
                                    emit("thinking", r)
                                if getattr(d, "refusal", None):
                                    refusal += d.refusal
                                for tc in d.tool_calls or []:
                                    acc = calls.setdefault(int(tc.index or 0), {"id": "", "name": "", "arguments": ""})
                                    if tc.id:
                                        acc["id"] = tc.id
                                    fn = tc.function
                                    if fn is not None:
                                        if fn.name:
                                            if not acc["name"]:
                                                acc["name"] = fn.name
                                                emit("tool", fn.name)
                                            elif not fn.name.startswith(acc["name"]):
                                                acc["name"] += fn.name
                                            else:
                                                acc["name"] = fn.name
                                        if fn.arguments:
                                            acc["arguments"] += fn.arguments
                            if ch.finish_reason:
                                finish = ch.finish_reason
            else:
                resp = await self.client.chat.completions.create(**kw)
                model = getattr(resp, "model", None)
                usage_obj = getattr(resp, "usage", None)
                if resp.choices:
                    ch = resp.choices[0]
                    finish = ch.finish_reason
                    m = ch.message
                    if m.content:
                        text_parts.append(m.content)
                    r = _reasoning_of(m)
                    if r:
                        thinking_parts.append(r)
                    if getattr(m, "refusal", None):
                        refusal = m.refusal
                    for i, tc in enumerate(m.tool_calls or []):
                        fn = getattr(tc, "function", None)
                        if fn is None:  # 関数以外の呼び出し(custom)は扱わない
                            continue
                        calls[i] = {"id": tc.id or "", "name": fn.name or "", "arguments": fn.arguments or ""}
        except ProviderError:
            raise
        except Exception as exc:
            raise self.map_error(exc) from None
        return self._turn("".join(text_parts), "".join(thinking_parts), calls, finish, usage_obj, model, refusal)

    def _turn(self, text: str, thinking: str, calls: dict[int, dict[str, str]], finish: str | None, usage_obj: Any,
              model: str | None, refusal: str) -> AssistantTurn:
        tool_calls: list[ToolCall] = []
        native_calls: list[dict[str, Any]] = []
        for n, i in enumerate(sorted(calls)):
            c = calls[i]
            cid = c["id"] or f"call_{n + 1}"
            raw = c["arguments"]
            try:
                args = json.loads(raw) if raw.strip() else {}
                err = None if isinstance(args, dict) else "引数が JSON のオブジェクトではありません"
                if not isinstance(args, dict):
                    args = None
            except json.JSONDecodeError as exc:
                args, err = None, f"引数を JSON として読めませんでした({exc.msg}、{exc.pos} 文字目)"
            tool_calls.append(ToolCall(id=cid, name=c["name"], arguments=args, raw_arguments=raw, error=err))
            native_calls.append({"id": cid, "type": "function", "function": {"name": c["name"], "arguments": raw}})
        stop = _STOP.get(finish or "", STOP_OTHER if finish else STOP_END)
        if tool_calls and stop in (STOP_END, STOP_OTHER):
            stop = STOP_TOOL  # 互換サーバーには finish_reason が stop のまま呼び出しを返すものがある
        if refusal and not tool_calls:
            stop = STOP_REFUSAL
        msg: dict[str, Any] = {"role": "assistant", "content": text if text else (None if native_calls else "")}
        if native_calls:
            msg["tool_calls"] = native_calls
        usage = Usage(requests=1)
        if usage_obj is not None:
            usage.input_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
            usage.output_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
            ptd = getattr(usage_obj, "prompt_tokens_details", None)
            if ptd is not None:
                usage.cache_read_tokens = int(getattr(ptd, "cached_tokens", 0) or 0)
                usage.cache_write_tokens = int(getattr(ptd, "cache_write_tokens", 0) or 0)
            ctd = getattr(usage_obj, "completion_tokens_details", None)
            if ctd is not None:
                usage.reasoning_tokens = int(getattr(ctd, "reasoning_tokens", 0) or 0)
        return AssistantTurn(text=text or refusal, thinking=[thinking] if thinking else [], tool_calls=tool_calls,
                             stop=stop, usage=usage, native=[msg], model=model,
                             stop_detail=(finish if stop == STOP_REFUSAL else None))

    async def list_models(self) -> list[str]:
        out: list[str] = []
        try:
            page = self.client.models.list(extra_headers=dict(self.extra_headers) if self.extra_headers else None)
            async for m in page:
                out.append(m.id)
        except Exception as exc:
            raise self.map_error(exc) from None
        return sorted(set(out))

    # ------------------------------------------------------------------ エラー
    def map_error(self, exc: BaseException) -> ProviderError:
        return map_openai_error(self._sdk, exc)


def _reasoning_of(obj: Any) -> str:
    """互換サーバーの考えの文章(``reasoning_content`` / ``reasoning``)。送り返さない(表示だけ)。"""
    for name in ("reasoning_content", "reasoning"):
        v = getattr(obj, name, None)
        if v is None:
            extra = getattr(obj, "model_extra", None) or {}
            v = extra.get(name)
        if isinstance(v, str) and v:
            return v
    return ""
