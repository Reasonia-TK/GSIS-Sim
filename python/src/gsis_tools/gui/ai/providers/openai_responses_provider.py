"""OpenAI 形式の Responses API(公式 SDK ``openai`` の非同期のクライアント ``client.responses``)。

リクエストの形(2026-10-18 に developers.openai.com の Function calling・Reasoning・Conversation state のガイドと、
SDK ``openai`` 3.35.1 の型で確かめた):

- ``POST {base_url}/responses``。システムの指示は ``instructions``、会話は ``input``(項目の並び)
- ツールの定義: ``tools: [{type: "function", name, description, parameters, strict}]``(Chat Completions と違い、
  ``function`` の中に入れない)。``strict`` を省くと strict モードに直そうとする(できなければ非 strict に戻る)ので、
  MCP のスキーマ(strict の条件を満たさないことがある)のために ``strict: false`` を明示する
- 応答の ``output`` の項目: ``message``(``content`` の ``output_text``・``refusal``)、``function_call``(``id`` と
  ``call_id``・``name``・``arguments`` は JSON の文字列)、``reasoning``(``summary``・``encrypted_content``)
- ツールの結果: ``{type: "function_call_output", call_id, output}``。``output`` は文字列か、``input_text``・
  ``input_image``(``image_url`` に data URL)・``input_file`` の並び。画像はツールの結果の中に入れられる
- 推論のモデルは、関数の呼び出しと一緒に返った推論の項目(reasoning items)を、次のリクエストで送り返す必要がある。
  この層は応答の ``output`` の項目をすべて(変えずに)履歴に足し、毎回の ``input`` で送り返す
- 会話の保存: ``store`` は省くと true(OpenAI 側に 30 日以上保存)。既定で ``store: false`` を送り、
  ``previous_response_id`` は使わない(毎回、履歴の全部を送る)。``store: false`` では推論の項目に暗号化された内容
  (``encrypted_content``)が既定で付く。公式の接続先には ``include: ["reasoning.encrypted_content"]`` も送る
  (SDK の ``ResponsesToolRunner`` と同じ。``store: true`` にしても、履歴は手元から送り返す)
- 送り返す項目は SDK の ``ResponsesToolRunner`` と同じく、応答の項目をそのまま(``id``・``status`` を含む)
- 出力の上限は ``max_output_tokens``。上限で切れると ``status: "incomplete"``・``incomplete_details.reason:
  "max_output_tokens"``(``content_filter`` は断られた扱い)
- ストリーミング: ``response.output_text.delta``・``response.reasoning_summary_text.delta``・
  ``response.output_item.added``(``function_call`` の名前)・``response.function_call_arguments.delta``・
  ``response.completed`` / ``response.incomplete``(最後の応答の全体)・``response.failed``・``error``
- トークン数: ``usage.input_tokens``(キャッシュを含む)・``input_tokens_details.cached_tokens``・``output_tokens``
  (推論を含む)・``output_tokens_details.reasoning_tokens``
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from ..secrets import ResolvedKey, redact
from ..settings import FORMAT_OPENAI, AiSettings
from ..types import (
    ERR_BAD_REQUEST,
    ERR_CONNECTION,
    ERR_OTHER,
    ERR_RATE_LIMIT,
    ERR_SERVER,
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
from . import Provider, StreamCallback
from .openai_provider import make_openai_client, map_openai_error

_FAILED_KIND = {"rate_limit_exceeded": ERR_RATE_LIMIT, "server_error": ERR_SERVER}


def _wire(item: Any) -> dict[str, Any]:
    """応答の ``output`` の項目 → 次のリクエストの ``input`` に送り返す形(SDK の ``ResponsesToolRunner`` と同じ扱い:
    項目はそのまま返し、SDK が足した欄 ``created_by``・``parsed_arguments``・``parsed`` と、空の ``id`` だけを除く)。"""
    data = item.to_dict(mode="json") if hasattr(item, "to_dict") else dict(item)
    data.pop("created_by", None)
    if data.get("type") == "function_call":
        data.pop("parsed_arguments", None)
        if data.get("id") is None:
            data.pop("id", None)
    if data.get("type") == "message":
        for part in data.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "output_text":
                part.pop("parsed", None)
    return data


class OpenAIResponsesProvider(Provider):
    """OpenAI 形式の Responses API(``store`` は既定で false。履歴は毎回すべて送る)。"""

    format = FORMAT_OPENAI
    kind = "openai-responses"

    def __init__(self, settings: AiSettings, key: ResolvedKey, *, http_client: Any = None):
        super().__init__(settings)
        import openai

        self._sdk = openai
        self.client, self.extra_headers = make_openai_client(settings, key, http_client)
        self.send_store = True
        # 暗号化された推論の内容を必ず受け取る(SDK の ResponsesToolRunner も付ける)。互換サーバーには送らない
        self.send_include = settings.is_official()

    # ------------------------------------------------------------------ 形
    def tool_params(self, tools: list[ToolDef]) -> list[dict[str, Any]]:
        return [{"type": "function", "name": t.name, "description": t.description, "parameters": t.input_schema,
                 "strict": False} for t in tools]

    def user_message(self, text: str) -> list[dict[str, Any]]:
        return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]

    def tool_result_messages(self, outcomes: list[ToolOutcome], send_images: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for o in outcomes:
            text = o.text or "(結果なし)"
            imgs = list(o.images) if send_images else []
            if imgs:
                output: Any = [{"type": "input_text", "text": text}]
                for img in imgs:
                    output.append({"type": "input_image", "detail": "auto",
                                   "image_url": "data:image/png;base64," + base64.b64encode(img).decode("ascii")})
            else:
                output = text
            out.append({"type": "function_call_output", "call_id": o.call.id, "output": output})
        return out

    def request_kwargs(self, system: str, tools: list[ToolDef], messages: list[dict[str, Any]]) -> dict[str, Any]:
        s = self.settings
        kw: dict[str, Any] = {"model": s.model, "instructions": system, "input": list(messages),
                              "max_output_tokens": s.max_tokens}
        tl = self.tool_params(tools)
        if tl:
            kw["tools"] = tl
        if self.send_store:
            kw["store"] = bool(s.current.store)
        if self.send_include:
            kw["include"] = ["reasoning.encrypted_content"]
        if s.stream:
            kw["stream"] = True
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
        done: set[str] = set()
        while True:
            kw = self.request_kwargs(system, tools, messages)
            try:
                turn = await self._send_once(kw, on_event)
                turn.notes.extend(notes)
                return turn
            except ProviderError as err:
                fix = self._auto_fix(err, done)
                if fix is None:
                    raise
                notes.append(fix)

    def _auto_fix(self, err: ProviderError, done: set[str]) -> str | None:
        """接続先が知らない引数を 1 回だけ外して送り直す。

        ``store`` を外すのは公式以外の接続先だけ(公式の接続先では、省くと応答が保存されるので外さない)。
        """
        if err.kind != ERR_BAD_REQUEST:
            return None
        m = (err.message or "").lower()
        if re.search(r"\binclude\b", m) and self.send_include and "include" not in done:
            done.add("include")
            self.send_include = False
            return "接続先が include を受け付けなかったので、外して送り直しました"
        if (re.search(r"\bstore\b", m) and self.send_store and "store" not in done
                and not self.settings.is_official()):
            done.add("store")
            self.send_store = False
            return "接続先が store を受け付けなかったので、外して送り直しました(保存するかは接続先の既定になります)"
        return None

    async def _send_once(self, kw: dict[str, Any], on_event: StreamCallback | None) -> AssistantTurn:
        emit = on_event or (lambda _k, _t: None)
        final: Any = None
        try:
            if kw.get("stream"):
                stream = await self.client.responses.create(**kw)
                async with stream:
                    async for ev in stream:
                        t = getattr(ev, "type", "")
                        if t == "response.output_text.delta":
                            emit("text", ev.delta)
                        elif t == "response.reasoning_summary_text.delta":
                            emit("thinking", ev.delta)
                        elif t == "response.output_item.added":
                            item = ev.item
                            if getattr(item, "type", "") == "function_call":
                                emit("tool", getattr(item, "name", "") or "")
                        elif t in ("response.completed", "response.incomplete"):
                            final = ev.response
                        elif t == "response.failed":
                            raise self._failed(getattr(ev, "response", None))
                        elif t == "error":
                            code = getattr(ev, "code", None) or ""
                            raise ProviderError(_FAILED_KIND.get(code, ERR_OTHER),
                                                redact(f"{getattr(ev, 'message', '')}({code})"))
                if final is None:
                    raise ProviderError(ERR_CONNECTION, "応答の流れが最後まで届きませんでした(response.completed がない)")
            else:
                final = await self.client.responses.create(**kw)
                if getattr(final, "status", None) == "failed":
                    raise self._failed(final)
        except ProviderError:
            raise
        except Exception as exc:
            raise map_openai_error(self._sdk, exc) from None
        return self.to_turn(final)

    def _failed(self, resp: Any) -> ProviderError:
        err = getattr(resp, "error", None)
        code = getattr(err, "code", None) or ""
        msg = getattr(err, "message", None) or "応答が失敗しました(response.failed)"
        return ProviderError(_FAILED_KIND.get(str(code), ERR_SERVER if not code else ERR_OTHER),
                             redact(f"{msg}({code})" if code else msg))

    def to_turn(self, resp: Any) -> AssistantTurn:
        items = [_wire(it) for it in (resp.output or [])]
        text_parts: list[str] = []
        refusal = ""
        thinking: list[str] = []
        calls: list[ToolCall] = []
        for it in items:
            t = it.get("type")
            if t == "message":
                for c in it.get("content") or []:
                    if c.get("type") == "output_text":
                        text_parts.append(c.get("text") or "")
                    elif c.get("type") == "refusal":
                        refusal += c.get("refusal") or ""
            elif t == "reasoning":
                for sm in it.get("summary") or []:
                    if sm.get("text"):
                        thinking.append(sm["text"])
            elif t == "function_call":
                raw = it.get("arguments") or ""
                try:
                    args = json.loads(raw) if raw.strip() else {}
                    err = None if isinstance(args, dict) else "引数が JSON のオブジェクトではありません"
                    if not isinstance(args, dict):
                        args = None
                except json.JSONDecodeError as exc:
                    args, err = None, f"引数を JSON として読めませんでした({exc.msg}、{exc.pos} 文字目)"
                calls.append(ToolCall(id=str(it.get("call_id") or it.get("id") or f"call_{len(calls) + 1}"),
                                      name=str(it.get("name") or ""), arguments=args, raw_arguments=raw, error=err))
        status = getattr(resp, "status", None) or "completed"
        reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
        if status != "completed":
            # 途中で切れた応答の最後の推論の項目には、続く項目がない(送り返すと API が続く項目を求めることがある)ので外す
            while items and items[-1].get("type") == "reasoning":
                items.pop()
        if status == "incomplete":
            stop = STOP_REFUSAL if reason == "content_filter" else (STOP_MAX_TOKENS if reason == "max_output_tokens"
                                                                    else STOP_OTHER)
        elif calls:
            stop = STOP_TOOL
        elif refusal:
            stop = STOP_REFUSAL
        else:
            stop = STOP_END
        usage = Usage(requests=1)
        u = getattr(resp, "usage", None)
        if u is not None:
            usage.input_tokens = int(getattr(u, "input_tokens", 0) or 0)
            usage.output_tokens = int(getattr(u, "output_tokens", 0) or 0)
            itd = getattr(u, "input_tokens_details", None)
            if itd is not None:
                usage.cache_read_tokens = int(getattr(itd, "cached_tokens", 0) or 0)
                usage.cache_write_tokens = int(getattr(itd, "cache_write_tokens", 0) or 0)
            otd = getattr(u, "output_tokens_details", None)
            if otd is not None:
                usage.reasoning_tokens = int(getattr(otd, "reasoning_tokens", 0) or 0)
        return AssistantTurn(text="".join(text_parts) or refusal, thinking=thinking, tool_calls=calls, stop=stop,
                             usage=usage, native=items, model=getattr(resp, "model", None),
                             stop_detail=(reason or ("refusal" if refusal else None)) if stop == STOP_REFUSAL else None)

    async def list_models(self) -> list[str]:
        out: list[str] = []
        try:
            page = self.client.models.list(extra_headers=dict(self.extra_headers) if self.extra_headers else None)
            async for m in page:
                out.append(m.id)
        except Exception as exc:
            raise map_openai_error(self._sdk, exc) from None
        return sorted(set(out))
