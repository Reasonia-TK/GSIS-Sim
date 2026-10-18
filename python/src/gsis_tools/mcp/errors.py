"""MCP のツールが返すエラー。"""

from __future__ import annotations

from typing import Any


class ToolFailure(Exception):
    """想定内の失敗(エージェントに理由を返して、直してもらうもの)。

    ``hint`` は直し方の手がかり、``data`` は追加の情報(JSON にできる値)。
    """

    def __init__(self, message: str, *, hint: str | None = None, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.data = data or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.message}
        if self.hint:
            out["hint"] = self.hint
        out.update(self.data)
        return out


class BatchFailure(ToolFailure):
    """batch の途中の手順が失敗した(全体を元に戻した)。"""

    def __init__(self, index: int, tool: str, cause: BaseException, completed: list[dict[str, Any]]):
        if isinstance(cause, ToolFailure):
            reason = cause.message
            hint = cause.hint
            extra = {k: v for k, v in cause.data.items()}
        else:
            reason = f"{type(cause).__name__}: {cause}"
            hint = None
            extra = {}
        super().__init__(
            f"batch の {index} 番目の手順({tool})で失敗したので、batch 全体を元に戻しました: {reason}",
            hint=hint,
            data={"failed": {"index": index, "tool": tool, "error": reason, **extra}, "rolled_back": True,
                  "completed_before_failure": len(completed)},
        )
        self.index = index
        self.tool = tool
        self.cause = cause
