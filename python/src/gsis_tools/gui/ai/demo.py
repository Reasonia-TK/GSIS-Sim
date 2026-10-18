"""模擬の AI(試験・スクリーンショット・画面の確認用)。

実際の API は呼ばない。SDK に ``httpx2.MockTransport`` を持つ HTTP のクライアントを渡し、決まった応答の列を
順に返す(Anthropic 形式は Messages API の JSON と SSE、OpenAI 形式はリクエストのパスで Chat Completions
(``/chat/completions``)か Responses(``/responses``)の JSON と SSE)。
受け取ったリクエスト(パス・ヘッダー・本文)は :attr:`ScriptedApi.requests` に残るので、試験で形を確かめられる。

    api = ScriptedApi("anthropic", tube_script())
    backend = AiBackend(..., http_client_factory=api.client_factory)
"""

from __future__ import annotations

import itertools
import json
import math
import threading
from dataclasses import dataclass, field
from typing import Any

import httpx2

_ids = itertools.count(1)


@dataclass
class ScriptTurn:
    """模擬の応答 1 回。"""

    text: str = ""
    thinking: str = ""  # Anthropic は thinking のブロック、OpenAI は reasoning_content(表示だけ)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stop: str | None = None  # None なら呼び出しがあれば tool_use、なければ end_turn
    status: int | None = None  # HTTP のエラーを返す(例 401、429、500)
    error_body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    exception: BaseException | None = None  # 通信の失敗(httpx2.ConnectError など)
    usage: tuple[int, int, int, int] = (1500, 120, 0, 0)  # 入力(キャッシュ以外)、出力、キャッシュ読み、書き
    stream_error: dict[str, Any] | None = None  # SSE の途中の error イベント(Anthropic・Responses)
    raw_arguments: dict[int, str] = field(default_factory=dict)  # OpenAI: 呼び出しの番号 → そのままの引数の文字列
    delay: float = 0.0  # 応答を返す前に待つ秒数(停止の試験)
    model: str | None = None


class ScriptedApi:
    """決まった応答を返す模擬の API(スレッドに安全)。"""

    def __init__(self, fmt: str, turns: list[ScriptTurn], *, model: str | None = None,
                 models: list[str] | None = None, final_text: str = "(模擬の AI: 応答の列が終わりました)"):
        self.format = fmt
        self.turns = list(turns)
        self.model = model or ("claude-opus-5-5" if fmt == "anthropic" else "demo-model")
        self.models = models or ([self.model, "claude-fable-5-1"] if fmt == "anthropic" else [self.model, "other-model"])
        self.final_text = final_text
        self.requests: list[dict[str, Any]] = []
        self.index = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ SDK に渡すもの
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handler)

    def client_factory(self, _settings: Any = None) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=self.transport())

    # ------------------------------------------------------------------ 応答
    def handler(self, request: httpx2.Request) -> Any:
        """MockTransport の処理(``delay`` のある応答は、待ってから返すコルーチンを返す。非同期のクライアント用)。"""
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        with self._lock:
            self.requests.append({"method": request.method, "path": request.url.path, "url": str(request.url),
                                  "query": dict(request.url.params), "headers": dict(request.headers), "json": body})
            if request.url.path.endswith("/models"):
                return self._models()
            if self.index < len(self.turns):
                turn = self.turns[self.index]
            else:
                turn = ScriptTurn(text=self.final_text)
            self.index += 1
        responses = request.url.path.endswith("/responses")
        if turn.delay:
            import asyncio

            async def later() -> httpx2.Response:
                await asyncio.sleep(turn.delay)
                return self._respond(turn, body, responses)

            return later()
        return self._respond(turn, body, responses)

    def _respond(self, turn: ScriptTurn, body: dict[str, Any], responses: bool = False) -> httpx2.Response:
        if turn.exception is not None:
            raise turn.exception
        if turn.status is not None:
            err = turn.error_body or {"type": "error", "error": {"type": "api_error", "message": f"模擬のエラー {turn.status}"}}
            return httpx2.Response(turn.status, json=err, headers=turn.headers)
        stream = bool(body.get("stream"))
        if self.format == "anthropic":
            return self._anthropic(turn, body, stream)
        if responses:
            return self._responses(turn, body, stream)
        return self._openai(turn, body, stream)

    def _models(self) -> httpx2.Response:
        if self.format == "anthropic":
            data = [{"type": "model", "id": m, "display_name": m, "created_at": "2026-09-21T00:00:00Z"} for m in self.models]
            return httpx2.Response(200, json={"data": data, "has_more": False, "first_id": self.models[0],
                                              "last_id": self.models[-1]})
        return httpx2.Response(200, json={"object": "list", "data": [
            {"id": m, "object": "model", "created": 1760000000, "owned_by": "demo"} for m in self.models]})

    # ---- Anthropic ----
    def _anthropic_blocks(self, turn: ScriptTurn) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if turn.thinking:
            blocks.append({"type": "thinking", "thinking": turn.thinking, "signature": f"sig{next(_ids)}"})
        if turn.text:
            blocks.append({"type": "text", "text": turn.text})
        for name, args in turn.calls:
            blocks.append({"type": "tool_use", "id": f"toolu_{next(_ids):04d}", "name": name, "input": args})
        return blocks

    def _anthropic(self, turn: ScriptTurn, body: dict[str, Any], stream: bool) -> httpx2.Response:
        blocks = self._anthropic_blocks(turn)
        stop = turn.stop or ("tool_use" if turn.calls else "end_turn")
        inp, out, cr, cw = turn.usage
        model = turn.model or body.get("model") or self.model
        mid = f"msg_{next(_ids):06d}"
        usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
                 "cache_creation_input_tokens": cw}
        if not stream:
            return httpx2.Response(200, json={"id": mid, "type": "message", "role": "assistant", "model": model,
                                              "content": blocks, "stop_reason": stop, "stop_sequence": None,
                                              "usage": usage})
        ev: list[tuple[str, dict[str, Any]]] = []
        ev.append(("message_start", {"type": "message_start", "message": {
            "id": mid, "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {**usage, "output_tokens": 1}}}))
        for i, b in enumerate(blocks):
            if b["type"] == "thinking":
                ev.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                   "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
                for part in _chunks(b["thinking"], 12):
                    ev.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                       "delta": {"type": "thinking_delta", "thinking": part}}))
                ev.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                   "delta": {"type": "signature_delta", "signature": b["signature"]}}))
            elif b["type"] == "text":
                ev.append(("content_block_start", {"type": "content_block_start", "index": i,
                                                   "content_block": {"type": "text", "text": ""}}))
                for part in _chunks(b["text"], 10):
                    ev.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                       "delta": {"type": "text_delta", "text": part}}))
            else:
                ev.append(("content_block_start", {"type": "content_block_start", "index": i, "content_block": {
                    "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}))
                js = json.dumps(b["input"], ensure_ascii=False)
                for part in _chunks(js, 40):
                    ev.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                                                       "delta": {"type": "input_json_delta", "partial_json": part}}))
            ev.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
            if turn.stream_error is not None and i == 0:
                ev.append(("error", {"type": "error", "error": turn.stream_error}))
                break
        ev.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                                     "usage": {"output_tokens": out}}))
        ev.append(("message_stop", {"type": "message_stop"}))
        text = "".join(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in ev)
        return httpx2.Response(200, content=text.encode("utf-8"), headers={"content-type": "text/event-stream"})

    # ---- OpenAI の Responses ----
    def _responses(self, turn: ScriptTurn, body: dict[str, Any], stream: bool) -> httpx2.Response:
        resp = _responses_payload(self, turn, body)
        if not stream:
            return httpx2.Response(200, json=resp)
        seq = itertools.count()
        ev: list[dict[str, Any]] = []

        def add(d: dict[str, Any]) -> None:
            ev.append({**d, "sequence_number": next(seq)})

        add({"type": "response.created", "response": {**resp, "status": "in_progress", "output": [], "usage": None}})
        for idx, item in enumerate(resp["output"]):
            t = item["type"]
            if t == "reasoning":
                add({"type": "response.output_item.added", "output_index": idx,
                     "item": {"type": "reasoning", "id": item["id"], "summary": []}})
                for part in _chunks(item["summary"][0]["text"], 12):
                    add({"type": "response.reasoning_summary_text.delta", "item_id": item["id"], "output_index": idx,
                         "summary_index": 0, "delta": part})
            elif t == "message":
                add({"type": "response.output_item.added", "output_index": idx,
                     "item": {**item, "status": "in_progress", "content": []}})
                c = item["content"][0]
                if c["type"] == "output_text":
                    for part in _chunks(c["text"], 10):
                        add({"type": "response.output_text.delta", "item_id": item["id"], "output_index": idx,
                             "content_index": 0, "delta": part, "logprobs": []})
                else:
                    add({"type": "response.refusal.delta", "item_id": item["id"], "output_index": idx,
                         "content_index": 0, "delta": c["refusal"]})
            else:
                add({"type": "response.output_item.added", "output_index": idx,
                     "item": {**item, "arguments": "", "status": "in_progress"}})
                for part in _chunks(item["arguments"], 30):
                    add({"type": "response.function_call_arguments.delta", "item_id": item["id"],
                         "output_index": idx, "delta": part})
                add({"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": idx,
                     "arguments": item["arguments"], "name": item["name"]})
            add({"type": "response.output_item.done", "output_index": idx, "item": item})
            if turn.stream_error is not None and idx == 0:
                add({"type": "error", "code": turn.stream_error.get("code", "server_error"),
                     "message": turn.stream_error.get("message", "error"), "param": None})
                break
        else:
            add({"type": {"incomplete": "response.incomplete", "failed": "response.failed"}.get(
                resp["status"], "response.completed"), "response": resp})
        text = "".join(f"event: {d['type']}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for d in ev)
        return httpx2.Response(200, content=text.encode("utf-8"), headers={"content-type": "text/event-stream"})

    # ---- OpenAI ----
    def _openai(self, turn: ScriptTurn, body: dict[str, Any], stream: bool) -> httpx2.Response:
        model = turn.model or body.get("model") or self.model
        cid = f"chatcmpl-{next(_ids)}"
        calls = [{"id": f"call_{next(_ids):04d}", "type": "function",
                  "function": {"name": name, "arguments": turn.raw_arguments.get(i, json.dumps(args, ensure_ascii=False))}}
                 for i, (name, args) in enumerate(turn.calls)]
        finish = turn.stop or ("tool_calls" if calls else "stop")
        inp, out, cr, _cw = turn.usage
        usage = {"prompt_tokens": inp + cr, "completion_tokens": out, "total_tokens": inp + cr + out,
                 "prompt_tokens_details": {"cached_tokens": cr}, "completion_tokens_details": {"reasoning_tokens": 0}}
        if not stream:
            msg: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
            if calls:
                msg["tool_calls"] = calls
            if turn.thinking:
                msg["reasoning_content"] = turn.thinking
            return httpx2.Response(200, json={"id": cid, "object": "chat.completion", "created": 1760000000,
                                              "model": model, "choices": [{"index": 0, "message": msg,
                                                                           "finish_reason": finish}],
                                              "usage": usage})
        chunks: list[dict[str, Any]] = []

        def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> None:
            chunks.append({"id": cid, "object": "chat.completion.chunk", "created": 1760000000, "model": model,
                           "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]})

        chunk({"role": "assistant", "content": ""})
        if turn.thinking:
            for part in _chunks(turn.thinking, 12):
                chunk({"reasoning_content": part})
        for part in _chunks(turn.text, 10):
            chunk({"content": part})
        for i, c in enumerate(calls):
            chunk({"tool_calls": [{"index": i, "id": c["id"], "type": "function",
                                   "function": {"name": c["function"]["name"], "arguments": ""}}]})
            for part in _chunks(c["function"]["arguments"], 30):
                chunk({"tool_calls": [{"index": i, "function": {"arguments": part}}]})
        chunk({}, finish)
        if (body.get("stream_options") or {}).get("include_usage"):
            chunks.append({"id": cid, "object": "chat.completion.chunk", "created": 1760000000, "model": model,
                           "choices": [], "usage": usage})
        text = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx2.Response(200, content=text.encode("utf-8"), headers={"content-type": "text/event-stream"})


def _responses_payload(api: ScriptedApi, turn: ScriptTurn, body: dict[str, Any]) -> dict[str, Any]:
    """Responses API の応答の全体(output の項目と usage)。"""
    model = turn.model or body.get("model") or api.model
    items: list[dict[str, Any]] = []
    if turn.thinking:
        items.append({"type": "reasoning", "id": f"rs_{next(_ids):04d}",
                      "summary": [{"type": "summary_text", "text": turn.thinking}],
                      "encrypted_content": f"gAAAA-encrypted-{next(_ids)}"})
    stop = turn.stop
    if turn.text or stop == "refusal":
        part = ({"type": "refusal", "refusal": turn.text or "お答えできません"} if stop == "refusal"
                else {"type": "output_text", "text": turn.text, "annotations": []})
        items.append({"type": "message", "id": f"msg_{next(_ids):04d}", "role": "assistant", "status": "completed",
                      "content": [part]})
    for i, (name, args) in enumerate(turn.calls):
        items.append({"type": "function_call", "id": f"fc_{next(_ids):04d}", "call_id": f"call_{next(_ids):04d}",
                      "name": name, "arguments": turn.raw_arguments.get(i, json.dumps(args, ensure_ascii=False)),
                      "status": "completed"})
    inp, out, cr, _cw = turn.usage
    status, incomplete, error = "completed", None, None
    if stop in ("max_output_tokens", "max_tokens", "length", "content_filter"):
        status = "incomplete"
        incomplete = {"reason": "content_filter" if stop == "content_filter" else "max_output_tokens"}
    elif stop == "failed":
        status, error = "failed", {"code": "server_error", "message": "The model failed to generate a response."}
    return {"id": f"resp_{next(_ids):06d}", "object": "response", "created_at": 1760000000, "status": status,
            "model": model, "output": items, "incomplete_details": incomplete, "error": error,
            "instructions": None, "metadata": {}, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "temperature": None, "top_p": None, "store": bool(body.get("store", True)),
            "usage": {"input_tokens": inp + cr, "input_tokens_details": {"cached_tokens": cr},
                      "output_tokens": out, "output_tokens_details": {"reasoning_tokens": 0},
                      "total_tokens": inp + cr + out}}


def _chunks(s: str, n: int) -> list[str]:
    return [s[i:i + n] for i in range(0, len(s), n)] if s else []


# ---------------------------------------------------------------------------
# 管(論文 §4.1: 円管 → 真空)を作る模擬の会話
# ---------------------------------------------------------------------------

TUBE_REQUEST = ("論文 §4.1 の円管 → 真空の計算領域を作ってください。管の半径 R = 1 mm、長さ 10R。上流槽は半径 8R・"
                "長さ 8R、下流槽は半径 4R・長さ 4R。x 軸に沿って並べ、x = 0 の面を inlet、x = 22R の面を outlet、"
                "残りを wall にして、tube/tube.pmesh に書き出してください。")


TUBE_REQUEST_SHAPE = ("論文 §4.1 の円管 → 真空の計算領域を作ってください。管の半径 R = 1 mm、長さ 10R。上流槽は半径 8R・"
                      "長さ 8R、下流槽は半径 4R・長さ 4R で、x 軸に沿って並べます。x = 0 の面を inlet、x = 22R の面を "
                      "outlet、残りを wall にしてください。メッシュはまだ作らないでください。")
TUBE_REQUEST_MESH = "管の中を細かくしてメッシュを作り、tube/tube.pmesh に書き出してください。"


def tube_script_two_steps(*, fine: bool = True) -> tuple[list[ScriptTurn], list[ScriptTurn]]:
    """2 回の指示に分けた管の会話(形状とパッチ → メッシュと書き出し)。スクリーンショット用。"""
    t = tube_script(fine=fine)
    part1 = t[:3] + [ScriptTurn(text=("形状を作り、パッチを付けました。体積は解析値 586π mm³ と一致し(相対誤差 1e−16 程度)、"
                                      "面 7 枚がすべて inlet・outlet・wall のどれかに入っています。メッシュを作るときは"
                                      "知らせてください。"), usage=(1900, 150, 10500, 300))]
    part2 = t[3:]
    return part1, part2


def tube_script(*, fine: bool = False) -> list[ScriptTurn]:
    """管の計算領域を作り .pmesh まで書き出す応答の列(``fine`` はスクリーンショット用の細かいメッシュ)。"""
    exact = 586 * math.pi
    mesh_settings = {"size_max": 0.8 if fine else 2.0}
    turns = [
        ScriptTurn(text=("上流槽・管・下流槽を x 軸に沿った円柱 3 本の和で作り、体積を解析値 586π mm³ と比べて"
                         "確かめます。単位は mm にします。"),
                   calls=[("model_new", {"name": "tube", "unit": "mm"}),
                          ("batch", {"steps": [
                              {"tool": "add_cylinder", "args": {"base": [0, 0, 0], "axis": [8, 0, 0], "radius": 8,
                                                                 "name": "upstream"}},
                              {"tool": "add_cylinder", "args": {"base": [8, 0, 0], "axis": [10, 0, 0], "radius": 1,
                                                                 "name": "pipe"}},
                              {"tool": "add_cylinder", "args": {"base": [18, 0, 0], "axis": [4, 0, 0], "radius": 4,
                                                                 "name": "downstream"}},
                              {"tool": "boolean", "args": {"op": "fuse", "objects": ["upstream"],
                                                           "tools": ["pipe", "downstream"], "name": "tube"}},
                              {"tool": "measure", "args": {"expected_volume": exact, "rtol": 1e-9}}]})],
                   usage=(9800, 420, 0, 9200)),
        ScriptTurn(thinking="体積は解析値と一致した。形状の検査と、面の一覧を確かめる。",
                   calls=[("check_geometry", {"mesh_test": True}), ("list_faces", {})],
                   usage=(1300, 90, 9200, 600)),
        ScriptTurn(text="面は 7 枚です。x = 0 の平面を inlet、x = 22 の平面を outlet、残りをまとめて wall にします。",
                   calls=[("assign_patch", {"name": "inlet", "select": {"on_plane": {"point": [0, 0, 0], "normal": [1, 0, 0]}}}),
                          ("assign_patch", {"name": "outlet", "select": {"on_plane": {"point": [22, 0, 0], "normal": [1, 0, 0]}}}),
                          ("assign_patch", {"name": "wall", "select": {"unassigned": True}}),
                          ("list_patches", {})],
                   usage=(2100, 260, 9800, 700)),
        ScriptTurn(text="すべての面にパッチが付きました。管の中と出入口の近くを細かくして、四面体のメッシュを作ります。",
                   calls=[("mesh_settings", mesh_settings)]
                   + ([("mesh_refine", {"name": "pipe", "target": "cylinder", "base": [7, 0, 0], "axis": [12, 0, 0],
                                        "radius": 1.4, "size": 0.25})] if fine else [])
                   + [("mesh_generate", {"max_cells": 1_000_000})],
                   usage=(1700, 230, 10500, 500)),
        ScriptTurn(thinking="メッシュができた。品質を見てから .pmesh と状態を書き出す。",
                   calls=[("mesh_quality", {}),
                          ("export_pmesh", {"path": "tube/tube.pmesh", "report": "tube/tube_quality.md"}),
                          ("model_save", {"path": "tube/tube_state.json"})],
                   usage=(1900, 210, 11000, 600)),
        ScriptTurn(text=("できました。円管 → 真空の計算領域(R = 1 mm、体積は解析値 586π mm³ と一致)に、パッチ inlet"
                         "(x = 0)・outlet(x = 22)・wall を付け、四面体のメッシュを tube/tube.pmesh(座標は m)に"
                         "書き出しました。品質レポートは tube/tube_quality.md、状態は tube/tube_state.json です。"
                         "セル数と品質は上のツールの結果を見てください。"),
                   usage=(2300, 180, 11600, 400)),
    ]
    return turns
