"""AI の反復(エージェントのループ)。asyncio のループの中で動き、GUI のスレッドは止めない。

1 回の指示(ユーザーのメッセージ)ごとに:

1. 履歴を API に送り、応答(文章・考え・ツールの呼び出し)を受け取る
2. ツールの呼び出しを順に MCP サーバーで実行し、結果を次の応答のために履歴に足す
   (エラーは ``is_error`` 付きで返し、AI に直させる)。モデルを変え得る手順の後は自動保存と 3D 表示の更新
3. ツールを呼ばなくなるまで繰り返す

止める仕組み:

- ツール呼び出しの上限(1 回の指示あたり、既定 40)。超えた呼び出しは実行せず、上限の説明を結果として返して止める
- 停止: API の応答待ちは、その場で通信を切る。ツールの実行中は 2 秒だけ終わるのを待ち、終わらなければ
  ツールのタスクを取り消す(呼び出しの後始末で MCP サーバーを止めて起動し直し、直前の自動保存に戻す)
- 履歴は足すだけにする。止めたときも、応答にあったツールの呼び出しすべてに結果(「止めた」)を付けてから
  終わる(次のメッセージで API がエラーにしないように)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .mcp_client import McpServerDied
from .providers import Provider, capped_size
from .secrets import redact
from .settings import AiSettings
from .types import (
    STOP_MAX_TOKENS,
    STOP_PAUSE,
    STOP_REFUSAL,
    AssistantTurn,
    ProviderError,
    ToolCall,
    ToolOutcome,
)

MAX_RESULT_CHARS = 30_000  # AI に返すツールの結果の長さの上限(これを超えたら切る)

Emit = Callable[[str, dict[str, Any]], None]
Confirm = Callable[[ToolCall], Awaitable[bool]]


def error_json(message: str, hint: str | None = None, **extra: Any) -> str:
    d: dict[str, Any] = {"error": message}
    if hint:
        d["hint"] = hint
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


class Agent:
    """1 回の指示を処理する(状態は会話 ``conv`` に足していく)。"""

    def __init__(self, *, provider: Provider, session: Any, conv: Any, settings: AiSettings, emit: Emit,
                 confirm: Confirm | None = None):
        self.provider = provider
        self.session = session
        self.conv = conv
        self.settings = settings
        self.emit = emit
        self.confirm = confirm
        self.stop_requested = False
        self.api_task: asyncio.Task | None = None
        self.tool_task: asyncio.Task | None = None
        self.calls_done = 0
        self.tool_names = {t.name for t in conv.tools}
        self._pending: list[ToolCall] | None = None
        self._outcomes: list[ToolOutcome] = []
        self._started: str | None = None  # 実行を始めた呼び出しの id
        self._pauses = 0

    # ------------------------------------------------------------------ 入口
    async def run(self, user_text: str | None, notes: list[str] | tuple[str, ...] = ()) -> str:
        """指示を処理する。``user_text`` が None なら履歴をそのまま送り直す(「再送」)。

        戻り値: ``done`` / ``stopped`` / ``limit`` / ``error`` / ``refusal``
        """
        if user_text is not None:
            text = user_text
            if notes:
                text = "[GUI での操作]\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" + user_text
            self.conv.messages.extend(self.provider.user_message(text))
        try:
            return await self._loop()
        except asyncio.CancelledError:
            self._finalize()
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise  # 外からの取り消し(終了など)
            self.emit("note", {"kind": "warning", "text": "止めました"})
            return "stopped"
        except ProviderError as err:
            self._finalize()
            self.emit("api_error", {"kind": err.kind, "status": err.status, "text": redact(err.describe()),
                                    "retryable": err.retryable})
            return "error"

    def request_stop(self) -> None:
        self.stop_requested = True

    # ------------------------------------------------------------------ 反復
    async def _sub(self, kind: str, coro: Awaitable[Any]) -> Any:
        """取り消せる下請けのタスクとして待つ(停止はこのタスクだけを取り消す)。"""
        task = asyncio.ensure_future(coro)
        setattr(self, f"{kind}_task", task)
        try:
            return await task
        finally:
            setattr(self, f"{kind}_task", None)

    def _on_stream(self, kind: str, text: str) -> None:
        self.emit("delta", {"kind": kind, "text": text})

    def _account(self, turn: AssistantTurn) -> None:
        self.conv.last_usage = turn.usage
        self.conv.usage.add(turn.usage)
        self.emit("usage", {"last": turn.usage.to_dict(), "total": self.conv.usage.to_dict()})

    async def _loop(self) -> str:
        limit = max(1, int(self.settings.tool_limit))
        while True:
            if self.stop_requested:
                self.emit("note", {"kind": "warning", "text": "止めました"})
                return "stopped"
            self.emit("status", {"text": "AI の応答を待っています…"})
            turn: AssistantTurn = await self._sub("api", self.provider.send(
                system=self.conv.system, tools=self.conv.tools, messages=self.conv.messages,
                on_event=self._on_stream))
            self._account(turn)
            for n in turn.notes:
                self.emit("note", {"kind": "info", "text": n})
            if turn.stop == STOP_REFUSAL:
                # 断られた応答は履歴に足さない(途中までの出力は捨てる)
                self.emit("assistant", {"text": turn.text, "thinking": [], "model": turn.model, "refused": True})
                self.emit("note", {"kind": "warning", "text": (
                    f"AI が応答を断りました(分類: {turn.stop_detail or '不明'})。指示の言い方を変えるか、"
                    "設定で別のモデルを選んでください")})
                return "refusal"
            self.conv.messages.extend(turn.native)
            if turn.model and (not self.conv.models or self.conv.models[-1] != turn.model):
                self.conv.models.append(turn.model)
            self.emit("assistant", {"text": turn.text, "thinking": turn.thinking, "model": turn.model,
                                    "stop": turn.stop, "calls": len(turn.tool_calls)})
            if not turn.tool_calls:
                if turn.stop == STOP_PAUSE and self._pauses < 5:  # サーバー側の一時停止は送り直して続ける
                    self._pauses += 1
                    continue
                if turn.stop == STOP_MAX_TOKENS:
                    self.emit("note", {"kind": "warning", "text": (
                        "応答が最大トークンで切れました。設定の「最大トークン」を増やすか、「続けて」と送ってください")})
                return "done"
            # ツールの実行
            self._pending = list(turn.tool_calls)
            self._outcomes = []
            limit_hit = False
            for call in turn.tool_calls:
                if self.stop_requested:
                    self._outcomes.append(self._skip(call, "ユーザーが止めたので実行しませんでした"))
                    continue
                if turn.stop == STOP_MAX_TOKENS:
                    self._outcomes.append(self._skip(call, (
                        "応答が最大トークンで切れたので、このツールは実行しませんでした(引数が途中で切れている"
                        "おそれがあります)"), hint="手順を分けるか、ユーザーに最大トークンを増やすよう頼んでください"))
                    continue
                if self.calls_done >= limit:
                    limit_hit = True
                    self._outcomes.append(self._skip(call, (
                        f"1 回の指示あたりのツール呼び出しの上限({limit} 回)に達したので実行しませんでした"),
                        hint="ここまでの結果をユーザーに伝え、続けてよいか尋ねてください"))
                    continue
                self.calls_done += 1
                self._outcomes.append(await self._execute(call))
            self.conv.messages.extend(self.provider.tool_result_messages(self._outcomes, self.settings.send_images))
            self._pending = None
            self._outcomes = []
            if self.stop_requested:
                self.emit("note", {"kind": "warning", "text": "止めました"})
                return "stopped"
            if limit_hit:
                self.emit("note", {"kind": "warning", "text": (
                    f"ツール呼び出しの上限({limit} 回)に達したので止めました。続けるには「続けて」と送ってください"
                    "(上限は設定で変えられます)")})
                return "limit"

    # ------------------------------------------------------------------ ツール
    def _skip(self, call: ToolCall, message: str, hint: str | None = None) -> ToolOutcome:
        """実行しなかった呼び出しの結果(画面にも出す)。"""
        o = ToolOutcome(call, text=error_json(message, hint), is_error=True)
        self.emit("tool_start", {"id": call.id, "name": call.name, "args": _args_of(call)})
        self.emit("tool_end", {"id": call.id, "name": call.name, "ok": False, "skipped": True, "text": o.text,
                               "seconds": 0.0})
        return o

    async def _execute(self, call: ToolCall) -> ToolOutcome:
        t0 = time.perf_counter()
        self._started = call.id
        self.emit("tool_start", {"id": call.id, "name": call.name, "args": _args_of(call)})
        image: bytes | None = None
        if call.arguments is None:
            o = ToolOutcome(call, text=error_json(call.error or "引数を読めませんでした",
                                                  "引数を JSON のオブジェクトにして呼び直してください"), is_error=True)
        elif call.name not in self.tool_names:
            o = ToolOutcome(call, text=error_json(f"未知のツールです: {call.name}",
                                                  "使えるツール: " + ", ".join(sorted(self.tool_names))),
                            is_error=True)
        else:
            args = dict(call.arguments)
            send_images = bool(self.settings.send_images)
            if call.name == "render":
                args["inline"] = send_images  # 画像を送らない設定では応答に画像を入れさせない
                if send_images:
                    w, h = capped_size(int(args.get("width") or 900), int(args.get("height") or 700))
                    args["width"], args["height"] = w, h
            refused = False
            if self.settings.confirm_tools and self.confirm is not None:
                refused = not await self.confirm(call)
            if refused:
                o = ToolOutcome(call, text=error_json("ユーザーがこのツールの実行を断りました",
                                                      "理由をユーザーに尋ねるか、別の方法を提案してください"),
                                is_error=True)
            else:
                try:
                    res = await self._sub("tool", self.session.call(call.name, args))
                except McpServerDied as exc:
                    await self.session.recover(
                        f"MCP サーバーが {call.name} の実行中に異常終了しました。起動し直して、直前の状態(自動保存)に戻しました"
                        f"({redact(exc)})")
                    o = ToolOutcome(call, text=error_json(
                        "MCP サーバー(gmsh・OpenCASCADE)が異常終了したので、起動し直して直前の状態(自動保存)に戻しました。"
                        "この手順の変更は反映されていません。メッシュは作り直しが要ります",
                        "同じ操作でまた落ちるおそれがあります。形状の作り方か引数を変えてください"), is_error=True)
                else:
                    text = res.text
                    if len(text) > MAX_RESULT_CHARS:
                        text = text[:MAX_RESULT_CHARS] + "…(長いので省略しました)"
                    o = ToolOutcome(call, text=text, is_error=res.is_error,
                                    images=list(res.images) if send_images else [], data=res.data)
                    image = res.images[0] if res.images else None
                    if not res.is_error and not self.session.is_read_only(call.name):
                        await self.session.after_change()
                    if (not res.is_error and call.name == "export_pmesh" and isinstance(res.data, dict)
                            and res.data.get("path")):
                        p = (self.session.workspace / str(res.data["path"])).resolve()
                        self.emit("pmesh", {"path": str(p)})
        o.seconds = time.perf_counter() - t0
        self._started = None
        self.emit("tool_end", {"id": call.id, "name": call.name, "ok": not o.is_error, "text": o.text,
                               "seconds": o.seconds, "image": image})
        return o

    def _finalize(self) -> None:
        """止めたとき・エラーのとき: 結果のない呼び出しに「止めた」の結果を付けて履歴を閉じる(同期の処理だけ)。"""
        if self._pending is None:
            return
        outs = list(self._outcomes)
        for c in self._pending[len(outs):]:
            if c.id == self._started:
                msg = "ユーザーが止めたので、この手順を途中で止めました。モデルは直前の状態(自動保存)に戻ります"
            else:
                msg = "ユーザーが止めたので実行しませんでした"
                self.emit("tool_start", {"id": c.id, "name": c.name, "args": _args_of(c)})
            o = ToolOutcome(c, text=error_json(msg), is_error=True)
            outs.append(o)
            self.emit("tool_end", {"id": c.id, "name": c.name, "ok": False, "skipped": True, "text": o.text,
                                   "seconds": 0.0})
        self.conv.messages.extend(self.provider.tool_result_messages(outs, self.settings.send_images))
        self._pending = None
        self._outcomes = []
        self._started = None


def _args_of(call: ToolCall) -> Any:
    return call.arguments if call.arguments is not None else call.raw_arguments
