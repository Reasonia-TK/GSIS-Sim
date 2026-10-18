"""MCP の tools/list から、AI に渡すツールの定義を作る。

- GUI 専用のツール(``_meta`` に ``gsis-pbc/gui-only`` の印、または名前が :data:`GUI_ONLY_TOOLS`)は AI に渡さない
- AI に画像を送らない設定では ``render`` も渡さない(画像が届かないのに呼んでも役に立たないため)
- スキーマの ``$ref``(``#/$defs/...``)は展開して ``$defs`` を除く(OpenAI 互換のサーバーには ``$ref`` を
  読めないものがあるため。両方の形式で同じ形にする)
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .types import ToolDef

# gsis_tools.mcp.tools の GUI_ONLY_TOOLS・GUI_ONLY_META と同じ(試験で一致を確かめる)
GUI_ONLY_TOOLS = frozenset({"gui_surface"})
GUI_ONLY_META_KEY = "gsis-pbc/gui-only"
IMAGE_TOOLS = frozenset({"render"})


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema の ``#/$defs/...`` の参照を展開する(再帰する参照は残し、そのときは ``$defs`` も残す)。"""
    schema = copy.deepcopy(schema or {})
    defs = dict(schema.get("$defs") or {})
    left: set[str] = set()

    def resolve(node: Any, stack: frozenset[str]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                if name in stack or name not in defs:
                    left.add(name)
                    return {k: resolve(v, stack) for k, v in node.items()}
                target = resolve(defs[name], stack | {name})
                merged = dict(target) if isinstance(target, dict) else {}
                for k, v in node.items():
                    if k != "$ref":
                        merged[k] = resolve(v, stack)
                return merged
            return {k: resolve(v, stack) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v, stack) for v in node]
        return node

    out = resolve(schema, frozenset())
    if left:
        out["$defs"] = {k: v for k, v in defs.items() if k in left}
    out.setdefault("type", "object")
    if out.get("type") == "object":
        out.setdefault("properties", {})
    return out


def is_gui_only(tool: Any) -> bool:
    name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else None)
    meta = getattr(tool, "meta", None) if not isinstance(tool, dict) else tool.get("_meta")
    return name in GUI_ONLY_TOOLS or bool(meta and meta.get(GUI_ONLY_META_KEY))


def tools_for_ai(mcp_tools: list[Any], *, send_images: bool) -> list[ToolDef]:
    """MCP の Tool(SDK の型か dict)の並びから、AI に渡すツールを作る(並びはサーバーの順のまま)。"""
    out: list[ToolDef] = []
    for t in mcp_tools:
        if is_gui_only(t):
            continue
        if isinstance(t, dict):
            name, desc = t["name"], t.get("description") or ""
            schema = t.get("inputSchema") or t.get("input_schema") or {}
            ann = t.get("annotations") or {}
            ro = bool(ann.get("readOnlyHint") or ann.get("read_only_hint"))
            title = t.get("title")
        else:
            name, desc, schema = t.name, t.description or "", t.input_schema
            ann = t.annotations
            ro = bool(getattr(ann, "read_only_hint", False)) if ann is not None else False
            title = getattr(t, "title", None)
        if name in IMAGE_TOOLS and not send_images:
            continue
        out.append(ToolDef(name=name, description=desc, input_schema=inline_refs(schema), read_only=ro, title=title))
    return out


def tools_size(tools: list[ToolDef]) -> int:
    """定義の JSON の大きさ(バイト。表示用)。"""
    return len(json.dumps([t.to_dict() for t in tools], ensure_ascii=False).encode("utf-8"))
