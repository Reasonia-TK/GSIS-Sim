"""AI モデリングの裏側をまとめる: 別スレッドの asyncio のループで、MCP サーバー・AI の反復・手での操作を動かす。

GUI のスレッドからは、ここの公開の関数(すぐ戻る)を呼ぶだけ。結果と途中の様子は ``emit(種類, 内容)`` で
知らせる(GUI は Qt のシグナルに載せ替えて、GUI のスレッドで受け取る)。同時に動く処理は 1 つだけ
(AI の指示、元に戻す、状態の保存・読み込み、手で実行するツール)。停止はいつでもできる。

知らせの種類(``emit`` の第 1 引数):

==============  ==========================================================
``busy``        処理中かどうか(``busy``・``what``)
``server``      MCP サーバーの状態(``state``: starting / ready / restarting / error)
``user``        送ったユーザーの文章
``delta``       応答の途中の文字(``kind``: text / thinking / tool / info)
``assistant``   AI の応答 1 回分(文章・考え・モデル)
``tool_start``  ツールの呼び出しの始め(``id``・``name``・``args``)
``tool_end``    終わり(``ok``・``text``・``seconds``・``image``)
``surface``     3D 表示のデータ(:class:`~.surface.SurfaceData`)
``usage``       トークン数(``last``・``total``)
``note``        説明(``kind``: info / warning / error)
``api_error``   API のエラー(``retryable`` なら「再送」できる)
``pmesh``       書き出した .pmesh(``path``)
``confirm``     ツールの実行の確認(``id``・``name``・``args``)。``answer_confirm`` で答える
``manual``      手で実行したツールの結果
``conversation`` 会話を読み込んだ・リセットした(``records``・``usage``)
``settings``    設定を変えた(OpenAI の出力の上限の引数を自動で切り替えたときなど)
``done``        AI の指示の処理の終わり(``status``)
==============  ==========================================================
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .agent import Agent
from .conversation import Conversation
from .mcp_client import McpServerDied
from .prompt import build_system_prompt
from .providers import make_provider
from .secrets import KeyResolver, redact, redact_obj
from .session import GUI_DIR, ConnectionFactory, ModelSession
from .settings import KIND_LABELS, AiSettings
from .surface import SurfaceData, update_surface
from .types import ProviderError, ToolCall

logger = logging.getLogger("gsis_tools.gui.ai")

Emit = Callable[[str, dict[str, Any]], None]
HttpClientFactory = Callable[[AiSettings], Any]

CONVERSATION_AUTOSAVE = f"{GUI_DIR}/conversation_autosave.json"
RECORD_TEXT_LIMIT = 6000  # 表示用の記録に残すツールの結果の長さ


class AiBackend:
    """AI モデリングの裏側(1 つの作業フォルダ・1 つの会話)。"""

    def __init__(self, *, settings: AiSettings, resolver: KeyResolver, emit: Emit, root: Path,
                 http_client_factory: HttpClientFactory | None = None,
                 connection_factory: ConnectionFactory | None = None):
        self.root = Path(root)
        self._settings = settings.copy()
        self.resolver = resolver
        self._emit_cb = emit
        self.http_client_factory = http_client_factory
        self.connection_factory = connection_factory
        self.conv = Conversation(self._settings.provider_kind())
        self.session: ModelSession | None = None
        self.surface: SurfaceData | None = None
        self.agent: Agent | None = None
        self.last_status = ""
        self._op: concurrent.futures.Future | None = None
        self._op_name = ""
        self._manual_task: asyncio.Task | None = None
        self._gui_notes: list[str] = []
        self._confirms: dict[int, asyncio.Future] = {}
        self._confirm_seq = 0
        self._closed = False
        self._starting = False
        self._session_lock = asyncio.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="gsis-ai", daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ------------------------------------------------------------------ 状態
    @property
    def busy(self) -> bool:
        return self._op is not None and not self._op.done()

    @property
    def busy_what(self) -> str:
        return self._op_name if self.busy else ""

    @property
    def workspace(self) -> Path:
        return self._settings.workspace_path(self.root)

    def settings(self) -> AiSettings:
        return self._settings.copy()

    def update_settings(self, s: AiSettings) -> None:
        """設定を変える(次の指示から効く)。作業フォルダが変わったら、サーバーを止めて会話をリセットする。"""
        old_ws = self.workspace
        self._settings = s.copy()
        if self.session is not None and Path(self.workspace).resolve() != Path(old_ws).resolve():
            self._start_op("workspace", self._switch_workspace())

    def pending_gui_notes(self) -> list[str]:
        return list(self._gui_notes)

    # ------------------------------------------------------------------ 知らせ
    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "surface":
            self.surface = update_surface(self.surface, payload)
            payload = {"surface": self.surface}
        else:
            self._record(kind, payload)
        try:
            self._emit_cb(kind, payload)
        except Exception:  # pragma: no cover - 画面の側の誤りで裏側を止めない
            logger.exception("知らせの処理で失敗しました(%s)", kind)

    def _record(self, kind: str, p: dict[str, Any]) -> None:
        rec: dict[str, Any] | None = None
        if kind == "user":
            rec = {"role": "user", "text": p.get("text", ""), "notes": p.get("notes") or []}
        elif kind == "assistant":
            rec = {"role": "assistant", "text": p.get("text", ""), "thinking": p.get("thinking") or [],
                   "model": p.get("model"), "refused": bool(p.get("refused"))}
        elif kind == "tool_end":
            text = str(p.get("text") or "")
            rec = {"role": "tool", "id": p.get("id"), "name": p.get("name"), "ok": bool(p.get("ok")),
                   "skipped": bool(p.get("skipped")), "seconds": float(p.get("seconds") or 0.0),
                   "text": text[:RECORD_TEXT_LIMIT] + ("…" if len(text) > RECORD_TEXT_LIMIT else "")}
            for r in reversed(self.conv.records):  # 呼び出しの引数(tool_start)と組にする
                if r.get("role") == "tool_call" and r.get("id") == p.get("id"):
                    rec["args"] = r.get("args")
                    self.conv.records.remove(r)
                    break
        elif kind == "tool_start":
            rec = {"role": "tool_call", "id": p.get("id"), "name": p.get("name"), "args": p.get("args")}
        elif kind in ("note", "api_error"):
            rec = {"role": "note", "kind": p.get("kind", "info") if kind == "note" else "error",
                   "text": p.get("text", "")}
        elif kind == "manual":
            rec = {"role": "manual", "name": p.get("name"), "args": p.get("args"), "ok": bool(p.get("ok")),
                   "text": str(p.get("text") or "")[:RECORD_TEXT_LIMIT]}
        elif kind == "pmesh":
            rec = {"role": "pmesh", "path": p.get("path")}
        if rec is not None:
            self.conv.add_record(redact_obj(rec))

    # ------------------------------------------------------------------ 処理の実行
    def _start_op(self, name: str, coro: Any) -> bool:
        if self._closed:
            coro.close()
            return False
        if self.busy:
            coro.close()
            self._emit("note", {"kind": "warning", "text": f"いまは別の処理({self._op_name})をしています"})
            return False
        self._op_name = name
        self._op = asyncio.run_coroutine_threadsafe(self._wrap(name, coro), self._loop)
        return True

    async def _wrap(self, name: str, coro: Any) -> Any:
        self._emit("busy", {"busy": True, "what": name})
        try:
            return await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("%s に失敗しました", name)
            self._emit("note", {"kind": "error", "text": f"{name} に失敗しました: {redact(f'{type(exc).__name__}: {exc}')}"})
            return None
        finally:
            if self.session is not None and self.session.needs_restart and not self._closed:
                try:
                    await self.session.recover("止めた処理がサーバーに残っているので、MCP サーバーを起動し直して、"
                                               "直前の状態(自動保存)に戻しました")
                except Exception as exc:
                    self._emit("note", {"kind": "error", "text": f"MCP サーバーを起動し直せませんでした: {redact(exc)}"})
            self._emit("busy", {"busy": False, "what": name})

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """処理が終わるまで待つ(試験・終了用。GUI のスレッドからは呼ばない)。"""
        op = self._op
        if op is None:
            return True
        try:
            op.result(timeout)
        except concurrent.futures.TimeoutError:
            return False
        except BaseException:
            pass
        return True

    def call_soon(self, coro: Any, timeout: float | None = None) -> Any:
        """ループの中でコルーチンを動かして結果を待つ(試験・同期の処理用)。"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # ------------------------------------------------------------------ MCP サーバー
    async def _ensure_session(self) -> ModelSession:
        async with self._session_lock:  # 起動の途中に送られた指示は、起動の終わりを待つ
            if self.session is None:
                session = ModelSession(self.workspace, self._emit, connection_factory=self.connection_factory)
                await session.start()
                self.session = session
            elif not self.session.alive:
                await self.session.restart(restore=True, reason="MCP サーバーを起動し直しています")
            return self.session

    def start(self) -> bool:
        """MCP サーバーを起動する(前回の自動保存があれば読み込み、3D 表示を出す)。ほかの処理とは別に動く。"""
        if self._closed or (self.session is not None and self.session.alive) or self._starting:
            return False
        self._starting = True

        async def go() -> None:
            try:
                await self._ensure_session()
            except Exception as exc:
                self._emit("note", {"kind": "error", "text": f"MCP サーバーを起動できませんでした: {redact(exc)}"})
            finally:
                self._starting = False

        asyncio.run_coroutine_threadsafe(go(), self._loop)
        return True

    async def _switch_workspace(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None
        self.surface = None
        self._reset_conversation_now("作業フォルダを変えたので、会話をリセットしました")
        await self._ensure_session()

    # ------------------------------------------------------------------ AI
    def send(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        return self._start_op("ai", self._run_ai(text))

    def resend(self) -> bool:
        """履歴をそのまま送り直す(API のエラーの後の「再送」)。"""
        if self.conv.empty:
            return False
        return self._start_op("ai", self._run_ai(None))

    async def _run_ai(self, text: str | None) -> str:
        status = "error"
        try:
            status = await self._run_ai_inner(text)
        finally:
            self.last_status = status
            self._emit("done", {"status": status})
        return status

    async def _run_ai_inner(self, text: str | None) -> str:
        s = self._settings.copy()
        probs = s.problems()
        if probs:
            self._emit("note", {"kind": "error", "text": "設定を直してください: " + " / ".join(probs)})
            return "config"
        if self.conv.format != s.provider_kind():
            if self.conv.empty:
                self.conv = Conversation(s.provider_kind())
            else:
                self._emit("note", {"kind": "error", "text": (
                    f"会話の形式({KIND_LABELS.get(self.conv.format, self.conv.format)})と設定の形式"
                    f"({KIND_LABELS.get(s.provider_kind(), s.provider_kind())})が違います。会話をリセットしてから送ってください")})
                return "config"
        key = self.resolver.resolve(s)
        self._emit_cb("key", {"label": key.label, "ok": key.ok, "source": key.source})
        if not key.ok:
            self._emit("note", {"kind": "error", "text": (
                f"API キーがありません({key.label})。設定でキーを入力するか、環境変数を設定してください")})
            return "config"
        session = await self._ensure_session()
        try:
            provider = make_provider(s, key, http_client=self.http_client_factory(s) if self.http_client_factory else None)
        except ProviderError as err:
            self._emit("note", {"kind": "error", "text": redact(err.describe())})
            return "config"
        if not self.conv.frozen:
            tools = session.tools_for_ai(send_images=s.send_images)
            self.conv.freeze(build_system_prompt(session.instructions, send_images=s.send_images,
                                                 tool_limit=s.tool_limit), tools)
        notes, self._gui_notes = self._gui_notes, []
        if text is not None:
            self._emit("user", {"text": text, "notes": notes})
        agent = Agent(provider=provider, session=session, conv=self.conv, settings=s, emit=self._emit,
                      confirm=self._confirm)
        self.agent = agent
        status = "error"
        try:
            status = await agent.run(text, notes)
        finally:
            self.agent = None
            await provider.aclose()
            self._autosave_conversation()
        tp = getattr(provider, "token_param", None)
        if tp is not None and tp != s.effective_token_param():
            self._settings.current.token_param = tp
            self._emit("settings", {"token_param": tp})
        return status

    async def _confirm(self, call: ToolCall) -> bool:
        self._confirm_seq += 1
        rid = self._confirm_seq
        fut: asyncio.Future = self._loop.create_future()
        self._confirms[rid] = fut
        self._emit("confirm", {"id": rid, "name": call.name, "args": call.arguments})
        try:
            return bool(await fut)
        finally:
            self._confirms.pop(rid, None)

    def answer_confirm(self, rid: int, ok: bool) -> None:
        def go() -> None:
            f = self._confirms.get(rid)
            if f is not None and not f.done():
                f.set_result(bool(ok))

        self._loop.call_soon_threadsafe(go)

    # ------------------------------------------------------------------ 停止
    def stop(self, grace: float = 2.0) -> None:
        """止める。API の応答待ちはすぐ切る。ツールの実行中は ``grace`` 秒待ち、終わらなければ強制停止
        (MCP サーバーを止めて起動し直し、直前の自動保存に戻す)。"""
        self._loop.call_soon_threadsafe(self._request_stop, grace)

    def _request_stop(self, grace: float) -> None:
        for f in list(self._confirms.values()):
            if not f.done():
                f.set_result(False)
        a = self.agent
        if a is not None:
            a.request_stop()
            self._emit("note", {"kind": "info", "text": "止めています…"})
            if a.api_task is not None:
                a.api_task.cancel()
            if a.tool_task is not None:
                self._loop.create_task(self._force_after(grace, a.tool_task))
            return
        t = self._manual_task
        if t is not None and not t.done():
            self._emit("note", {"kind": "info", "text": "止めています…"})
            self._loop.create_task(self._force_after(grace, t))

    async def _force_after(self, grace: float, task: asyncio.Task) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(task), grace)
        except (asyncio.TimeoutError, TimeoutError):
            self._emit("note", {"kind": "warning", "text": (
                "ツールが終わらないので強制停止します(MCP サーバーを止めて、直前の状態に戻します)")})
            task.cancel()
        except BaseException:
            pass

    # ------------------------------------------------------------------ 手での操作
    async def _manual(self, name: str, args: dict[str, Any], describe: str | None = None) -> Any:
        session = await self._ensure_session()
        task = asyncio.ensure_future(session.call(name, args))
        self._manual_task = task
        try:
            r = await task
        except McpServerDied as exc:
            await session.recover(f"MCP サーバーが {name} の実行中に異常終了しました。起動し直して直前の状態に戻しました"
                                  f"({redact(exc)})")
            return None
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            self._emit("note", {"kind": "warning", "text": f"{name} を止めました"})
            return None
        finally:
            self._manual_task = None
        self._emit("manual", {"name": name, "args": args, "ok": not r.is_error, "text": r.text, "data": r.data,
                              "image": r.images[0] if r.images else None})
        if not r.is_error:
            if not session.is_read_only(name):
                await session.after_change()
                self._gui_notes.append(describe or f"{name} {json.dumps(args, ensure_ascii=False)} を実行しました")
            if name == "export_pmesh" and isinstance(r.data, dict) and r.data.get("path"):
                self._emit("pmesh", {"path": str((session.workspace / str(r.data["path"])).resolve())})
        return r

    def run_tool(self, name: str, args: dict[str, Any]) -> bool:
        """ツールを手で実行する(GUI の「ツールを手で実行」の欄)。"""
        return self._start_op(f"tool:{name}", self._manual(name, dict(args or {}),
                                                          f"ツール {name}({json.dumps(args, ensure_ascii=False)})を手で実行しました"))

    def undo(self, steps: int = 1) -> bool:
        return self._start_op("undo", self._manual("undo", {"steps": int(steps)}, "「元に戻す」で最後の手順を取り消しました"))

    def assign_patch(self, faces: list[int], name: str, kind: str | None = None) -> bool:
        args: dict[str, Any] = {"name": name, "select": {"faces": [int(f) for f in faces]}, "mode": "add", "steal": True}
        if kind:
            args["kind"] = kind
        desc = f"面 {sorted(int(f) for f in faces)} をパッチ {name}" + (f"(kind {kind})" if kind else "") + " に手で付けました"
        return self._start_op("assign_patch", self._manual("assign_patch", args, desc))

    def save_state(self, path: str | Path) -> bool:
        rel = self._rel_in_workspace(path)
        if rel is None:
            self._emit("note", {"kind": "error", "text": f"作業フォルダ({self.workspace})の中を選んでください: {path}"})
            return False
        return self._start_op("model_save", self._manual("model_save", {"path": rel}))

    def load_state(self, path: str | Path) -> bool:
        p = Path(path)
        rel = self._rel_in_workspace(p)
        if rel is None:  # 作業フォルダの外: 写してから読む
            dst = self.workspace / GUI_DIR / "imports" / p.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            rel = dst.relative_to(self.workspace).as_posix()
        return self._start_op("model_load", self._manual("model_load", {"path": rel}, f"状態 {rel} を読み込みました"))

    def refresh_surface(self) -> bool:
        async def go() -> None:
            s = await self._ensure_session()
            await s.refresh_surface(force=True)

        return self._start_op("surface", go())

    def _rel_in_workspace(self, path: str | Path) -> str | None:
        p = Path(path)
        if not p.is_absolute():
            return p.as_posix()
        try:
            return p.resolve().relative_to(Path(self.workspace).resolve()).as_posix()
        except ValueError:
            return None

    # ------------------------------------------------------------------ 会話
    def reset_conversation(self) -> bool:
        if self.busy:
            return False
        self._reset_conversation_now("会話をリセットしました(モデルの状態はそのままです)")
        return True

    def _reset_conversation_now(self, note: str) -> None:
        self.conv = Conversation(self._settings.provider_kind())
        self._gui_notes = []
        self._emit_cb("conversation", {"records": [], "usage": self.conv.usage.to_dict(), "reset": True})
        self._emit("note", {"kind": "info", "text": note})

    def _model_state(self) -> Any:
        if self.session is None:
            return None
        p = self.session.autosave_path
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _public_settings(self) -> dict[str, Any]:
        s = self._settings
        return {"format": s.format, "kind": s.provider_kind(), "base_url": s.base_url, "model": s.model,
                "max_tokens": s.max_tokens,
                "tool_limit": s.tool_limit, "send_images": s.send_images}

    def _autosave_conversation(self) -> None:
        if self.session is None or self.conv.empty:
            return
        try:
            self.conv.save(self.workspace / CONVERSATION_AUTOSAVE, model_state=self._model_state(),
                           settings=self._public_settings())
        except OSError as exc:  # pragma: no cover
            logger.warning("会話の自動保存に失敗しました: %s", exc)

    def save_conversation(self, path: str | Path) -> Path:
        """会話を保存する(GUI のスレッドから呼んでよい。会話が動いていないときに)。"""
        return self.conv.save(Path(path), model_state=self._model_state(), settings=self._public_settings())

    def load_conversation(self, path: str | Path) -> bool:
        return self._start_op("load_conversation", self._load_conversation(Path(path)))

    async def _load_conversation(self, path: Path) -> bool:
        conv, data = Conversation.load(path)
        if conv.format != self._settings.provider_kind():
            want = KIND_LABELS.get(conv.format, conv.format)
            self._emit("note", {"kind": "error", "text": (
                f"この会話は {want} の形です。設定の形式(と OpenAI の API の種類)を合わせてから開いてください")})
            return False
        session = await self._ensure_session()
        self.conv = conv
        self._gui_notes = []
        self._emit_cb("conversation", {"records": [dict(r) for r in conv.records], "usage": conv.usage.to_dict(),
                                       "reset": False})
        state = data.get("model_state")
        if isinstance(state, dict):
            dst = self.workspace / GUI_DIR / "loaded_state.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            r = await session.call("model_load", {"path": f"{GUI_DIR}/loaded_state.json"})
            if r.is_error:
                self._emit("note", {"kind": "warning", "text": f"会話のモデルの状態を読めませんでした: {r.text[:300]}"})
            else:
                await session.after_change()
                self._gui_notes.append("保存した会話を開き、そのときのモデルの状態を読み込みました(メッシュは作り直しが要ります)")
        self._emit("note", {"kind": "info", "text": f"会話を開きました: {path.name}"})
        return True

    # ------------------------------------------------------------------ モデルの一覧
    def list_models(self, settings: AiSettings | None = None, *, resolver: KeyResolver | None = None
                    ) -> concurrent.futures.Future:
        """接続先のモデルの一覧(``/v1/models``)。結果は Future(GUI のスレッドで待たないこと)。"""
        s = (settings or self._settings).copy()
        res = resolver or self.resolver

        async def go() -> list[str]:
            key = res.resolve(s)
            p = make_provider(s, key, http_client=self.http_client_factory(s) if self.http_client_factory else None)
            try:
                return await p.list_models()
            finally:
                await p.aclose()

        return asyncio.run_coroutine_threadsafe(go(), self._loop)

    # ------------------------------------------------------------------ 終わり
    def shutdown(self, timeout: float = 20.0) -> None:
        if self._closed:
            return
        self._closed = True

        async def go() -> None:
            a = self.agent
            if a is not None:
                a.request_stop()
                for t in (a.api_task, a.tool_task):
                    if t is not None:
                        t.cancel()
            if self._manual_task is not None:
                self._manual_task.cancel()
            if self._op is not None and not self._op.done():
                self._op.cancel()
            try:  # 起動の途中なら、起動が終わってから閉じる(子プロセスを残さない)
                await asyncio.wait_for(self._session_lock.acquire(), 60)
                self._session_lock.release()
            except Exception:
                pass
            if self.session is not None:
                await self.session.close()

        try:
            asyncio.run_coroutine_threadsafe(go(), self._loop).result(timeout)
        except BaseException:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5.0)
