"""重い処理(メッシュ生成、VTU の読み込みなど)を別スレッドで行う補助。"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class Task(QThread):
    """関数を別スレッドで呼ぶ。結果は :attr:`done`、例外は :attr:`failed`(説明, トレースバック)で返す。"""

    done = Signal(object)
    failed = Signal(str, str)

    def __init__(self, fn: Callable[..., Any], *args: Any, parent: QObject | None = None, **kwargs: Any):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # 別スレッド
        try:
            res = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # 画面に知らせる
            self.failed.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        else:
            self.done.emit(res)


def start_task(
    owner: QObject,
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str, str], None] | None = None,
    **kwargs: Any,
) -> Task:
    """``owner`` が持つ別スレッドの処理を始める(終わるまで参照を保つ)。"""
    task = Task(fn, *args, parent=owner, **kwargs)
    tasks = owner.__dict__.setdefault("_gsis_tasks", set())
    tasks.add(task)
    if on_done is not None:
        task.done.connect(on_done)
    if on_error is not None:
        task.failed.connect(on_error)
    task.finished.connect(lambda: tasks.discard(task))
    task.start()
    return task


class JobRunner(QObject):
    """別スレッドの処理を 1 つずつ順に流す(同じ結果のデータに対する VTK の処理が重ならないように)。

    同じ ``key`` の要求がまだ待っていれば、新しい要求で置き換える(場を続けて切り替えたときなどは、
    最後の要求だけを計算する)。:attr:`busy_changed` は処理中かどうかが変わったときに送る。
    """

    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._running: str | None = None
        self._pending: dict[str, tuple] = {}
        self._order: list[str] = []

    def busy(self) -> bool:
        return self._running is not None or bool(self._order)

    def running_key(self) -> str | None:
        return self._running

    def submit(self, key: str, fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], None] | None = None,
               on_error: Callable[[str, str], None] | None = None, **kwargs: Any) -> None:
        was_busy = self.busy()
        self._pending[key] = (fn, args, kwargs, on_done, on_error)
        if key not in self._order:
            self._order.append(key)
        if not was_busy:
            self.busy_changed.emit(True)
        self._kick()

    def cancel_pending(self) -> None:
        """まだ始めていない要求を捨てる(実行中の処理は最後まで走る)。"""
        self._pending.clear()
        self._order.clear()

    def _kick(self) -> None:
        if self._running is not None or not self._order:
            return
        key = self._order.pop(0)
        fn, args, kwargs, on_done, on_error = self._pending.pop(key)
        self._running = key

        def finished_ok(res: Any) -> None:
            self._running = None
            try:
                if on_done is not None:
                    on_done(res)
            finally:
                self._next()

        def failed(msg: str, tb: str) -> None:
            self._running = None
            try:
                if on_error is not None:
                    on_error(msg, tb)
            finally:
                self._next()

        start_task(self, fn, *args, on_done=finished_ok, on_error=failed, **kwargs)

    def _next(self) -> None:
        if self._order:
            self._kick()
        else:
            self.busy_changed.emit(False)


class _Bridge(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    """``logging`` の記録を Qt のシグナルで画面に送る(別スレッドから呼ばれてもよい)。"""

    def __init__(self, level: int = logging.INFO):
        super().__init__(level)
        self.bridge = _Bridge()
        self.setFormatter(logging.Formatter("%(message)s"))
        from .ai.secrets import RedactingFilter

        self.addFilter(RedactingFilter())  # 画面のログに API キーを出さない(念のため)

    @property
    def message(self):
        return self.bridge.message

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.message.emit(self.format(record))
        except RuntimeError:  # 画面が閉じられた後
            pass
