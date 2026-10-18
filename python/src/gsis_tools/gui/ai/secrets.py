"""API キーの扱い(平文では保存しない)。

キーの読み込み元は 3 つ(上ほど優先):

1. その場の入力(設定の画面で入れたキー。そのセッションの間だけメモリに持つ)
2. OS の資格情報マネージャー(``keyring``。Windows は資格情報マネージャー)。ユーザーが保存を選んだときだけ書く
3. 環境変数 ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``。公式の接続先にだけ使う(ほかの接続先に送るのは、
   形式ごとの設定「環境変数のキーをこの接続先にも使う」を選んだときだけ。別のサーバーにキーが渡らないように)

キーは :class:`Secret` に包み、``repr``・``str``・``format`` では伏せ字になる。ログ・会話の記録・画面に出す
文字列は :func:`redact` を通し、このプロセスで扱ったキーと、キーらしい文字列(``sk-...``)を伏せ字にする。
キーを設定ファイル・作業フォルダ・会話の記録には書かない。
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .settings import ENV_KEY, FORMAT_OPENAI, AiSettings, normalize_base_url

KEYRING_SERVICE = "GSIS-PBC gsis-gui"
MASK = "***"

_LOCK = threading.Lock()
_REGISTERED: set[str] = set()
# キーらしい文字列(登録していないキーが混ざったときの念のため)
_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(x-api-key|authorization)(\s*[:=]\s*)(bearer\s+)?[^\s,;\"']{6,}"),
)


def register(value: str | None) -> None:
    """伏せ字にする値を登録する(6 文字未満は登録しない。普通の語を誤って消さないように)。"""
    if value and len(value) >= 6:
        with _LOCK:
            _REGISTERED.add(value)


def redact(text: Any) -> str:
    """文字列からキーを除く(登録したキーと、キーらしい文字列を伏せ字にする)。"""
    s = text if isinstance(text, str) else str(text)
    with _LOCK:
        values = sorted(_REGISTERED, key=len, reverse=True)
    for v in values:
        if v in s:
            s = s.replace(v, MASK)
    s = _PATTERNS[0].sub(MASK, s)
    s = _PATTERNS[1].sub(MASK, s)
    s = _PATTERNS[2].sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}{MASK}", s)
    return s


# API が返す不透明な値(暗号化された推論・考えの署名)。中身はキーを含みえず、書き換えると送り返せなくなる
# (URL で使える base64 の ``-`` の後に ``sk-`` が来ると、キーらしい文字列の規則に当たることがある)
OPAQUE_KEYS = frozenset({"encrypted_content", "signature"})


def redact_obj(obj: Any) -> Any:
    """入れ子の dict / list の文字列を伏せ字にする(会話の記録を書く前など)。API が返す不透明な値はそのまま。"""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: (v if k in OPAQUE_KEYS and isinstance(v, str) else redact_obj(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


class Secret:
    """API キーを包む。表示・ログ・pickle ではキーを出さない。"""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not isinstance(value, str):
            raise TypeError("キーは文字列にしてください")
        self._value = value.strip()
        register(self._value)

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return f"Secret({MASK})"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    __hash__ = None  # type: ignore[assignment]

    def __reduce__(self):  # pragma: no cover - 誤って保存しないように
        raise TypeError("Secret は保存できません")


class RedactingFilter(logging.Filter):
    """ログの記録の文字列からキーを除く(GUI のログの受け手に付ける)。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_log_redaction(logger_names: tuple[str, ...] = ("", "anthropic", "openai", "httpx2", "httpcore2", "mcp",
                                                              "gsis_tools")) -> RedactingFilter:
    """ログの受け手(handler)すべてに伏せ字の filter を付ける(SDK のデバッグログなどに念のため)。"""
    flt = RedactingFilter()
    for name in logger_names:
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactingFilter) for f in lg.filters):
            lg.addFilter(flt)
        for h in lg.handlers:
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(flt)
    return flt


def keyring_user(fmt: str, base_url: str) -> str:
    """資格情報マネージャーの項目の名前(形式と接続先ごと)。"""
    return f"{fmt}|{normalize_base_url(base_url, fmt)}"


class KeyStore:
    """OS の資格情報マネージャー(keyring)。``backend`` は keyring モジュールと同じ関数を持つもの(試験用)。"""

    def __init__(self, backend: Any = None):
        self._backend = backend

    def _kr(self):
        if self._backend is None:
            import keyring

            self._backend = keyring
        return self._backend

    def available(self) -> tuple[bool, str]:
        """使えるかと、使っている仕組みの名前。"""
        try:
            kr = self._kr()
            get = getattr(kr, "get_keyring", None)
            b = get() if callable(get) else kr
            name = type(b).__name__
            prio = getattr(b, "priority", 1)
            if callable(prio):  # pragma: no cover
                prio = prio()
            if "fail" in name.lower() or (isinstance(prio, (int, float)) and prio <= 0):
                return False, name
            return True, name
        except Exception as exc:  # keyring がない・壊れている
            return False, f"{type(exc).__name__}: {redact(exc)}"

    def get(self, fmt: str, base_url: str) -> Secret | None:
        try:
            v = self._kr().get_password(KEYRING_SERVICE, keyring_user(fmt, base_url))
        except Exception:
            return None
        return Secret(v) if v else None

    def set(self, fmt: str, base_url: str, secret: Secret) -> None:
        """保存する(失敗したら RuntimeError。説明にキーは入れない)。"""
        try:
            self._kr().set_password(KEYRING_SERVICE, keyring_user(fmt, base_url), secret.reveal())
        except Exception as exc:
            raise RuntimeError(f"資格情報マネージャーに保存できませんでした({type(exc).__name__}: {redact(exc)})") from None

    def delete(self, fmt: str, base_url: str) -> bool:
        try:
            self._kr().delete_password(KEYRING_SERVICE, keyring_user(fmt, base_url))
            return True
        except Exception:
            return False


@dataclass
class ResolvedKey:
    secret: Secret | None
    source: str  # "session" / "keyring" / "env" / "no_key" / "none"
    label: str  # 画面に出す説明(キーの値は入れない)

    @property
    def ok(self) -> bool:
        return self.secret is not None or self.source == "no_key"


class KeyResolver:
    """設定(形式・接続先)に合うキーを、その場の入力 → 資格情報マネージャー → 環境変数の順に探す。"""

    def __init__(self, store: KeyStore | None = None, environ: Mapping[str, str] | None = None):
        self.store = store or KeyStore()
        self._environ = environ  # None なら os.environ(試験では差し替える)
        self._session: dict[str, Secret] = {}

    @property
    def environ(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def set_session(self, fmt: str, base_url: str, secret: Secret | None) -> None:
        user = keyring_user(fmt, base_url)
        if secret:
            self._session[user] = secret
        else:
            self._session.pop(user, None)

    def session_key(self, fmt: str, base_url: str) -> Secret | None:
        return self._session.get(keyring_user(fmt, base_url))

    def clear_session(self) -> None:
        self._session.clear()

    def resolve(self, settings: AiSettings) -> ResolvedKey:
        fmt, url = settings.format, settings.base_url
        if fmt == FORMAT_OPENAI and settings.current.no_key:
            return ResolvedKey(None, "no_key", "キーなし(Authorization を送らない)")
        k = self.session_key(fmt, url)
        if k:
            return ResolvedKey(k, "session", "この画面で入力したキー(このセッションの間だけ)")
        k = self.store.get(fmt, url)
        if k:
            return ResolvedKey(k, "keyring", "OS の資格情報マネージャーに保存したキー")
        env = ENV_KEY[fmt]
        v = (self.environ.get(env) or "").strip()
        if v:
            if settings.is_official() or settings.current.use_env_key:
                return ResolvedKey(Secret(v), "env", f"環境変数 {env}")
            return ResolvedKey(None, "none",
                               f"なし(環境変数 {env} は公式の接続先にだけ使う設定です)")
        return ResolvedKey(None, "none", "なし")
