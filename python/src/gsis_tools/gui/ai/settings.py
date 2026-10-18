"""AI モデリングの設定(Qt なし)。API キーはここに入れない(:mod:`.secrets`)。

設定は GUI の設定ファイル(QSettings の ini)に、``ai/`` で始まるキーで保存する。形式(Anthropic /
OpenAI)ごとに、接続先・モデル・モデルの一覧・最大トークンなどを別に持つ(形式を切り替えても戻る)。

モデル名と API の引数は 2026-10-18 に公式資料で確かめた:

- Anthropic: 最新の 4 つ(``claude-fable-5-1``・``claude-opus-5-5``・``claude-sonnet-5-5``・``claude-haiku-5-5``)。
  既定は ``claude-opus-5-5``(ユーザーの決定 2026-10-18。公式資料の Models overview は「迷ったら多くの用途は
  Claude Opus 5.5 から」とし、最も高性能な ``claude-fable-5-1`` は 1 トークンあたり 2.5 倍)。考えの途中の報告
  ``thinking.display: "updates"``(beta ``thinking-display-updates-2026-08-18``)は Fable 5.1・Fable 5・Mythos 5.1・
  Opus 5.5・Sonnet 5.5 だけ。安全の分類で断られたときのサーバー側の切り替え ``fallbacks: "default"``
  (beta ``server-side-fallback-2026-07-01``)は Claude API(公式の接続先)だけ
- OpenAI: API の種類を選べる(Responses API / Chat Completions API)。既定は、公式の接続先なら Responses
  (GPT-6 Astra・GPT-6.1 Sol のツールの呼び出しは Responses が要る。developers.openai.com の Function calling)、
  ほかの接続先(互換サーバー)は Chat Completions。Responses は既定で ``store: false``(OpenAI 側に応答を保存しない。
  保存すると既定で 30 日。推論の項目は暗号化された内容ごと次のリクエストで送り返す)
- OpenAI の Chat Completions の ``max_tokens`` は非推奨で、o 系などの推論モデルでは使えない(SDK の型の説明)。
  公式の接続先では ``max_completion_tokens`` を、それ以外(互換サーバー)では ``max_tokens`` を既定にする
  (Responses は ``max_output_tokens``)
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FORMAT_ANTHROPIC = "anthropic"
FORMAT_OPENAI = "openai"
FORMATS = (FORMAT_ANTHROPIC, FORMAT_OPENAI)
FORMAT_LABELS = {
    FORMAT_ANTHROPIC: "Anthropic 形式(Messages API)",
    FORMAT_OPENAI: "OpenAI 形式(Responses API・Chat Completions API。互換サーバーを含む)",
}
OFFICIAL_BASE_URL = {FORMAT_ANTHROPIC: "https://api.anthropic.com", FORMAT_OPENAI: "https://api.openai.com/v1"}
OFFICIAL_HOST = {FORMAT_ANTHROPIC: "api.anthropic.com", FORMAT_OPENAI: "api.openai.com"}
ENV_KEY = {FORMAT_ANTHROPIC: "ANTHROPIC_API_KEY", FORMAT_OPENAI: "OPENAI_API_KEY"}
# 接続先の例(編集できる一覧に出す。ローカルのサーバーはよく使われる既定のポート)
BASE_URL_EXAMPLES = {
    FORMAT_ANTHROPIC: ["https://api.anthropic.com"],
    FORMAT_OPENAI: ["https://api.openai.com/v1", "http://localhost:11434/v1", "http://localhost:8000/v1",
                    "http://localhost:1234/v1", "http://localhost:8080/v1"],
}
DEFAULT_MODELS = {
    FORMAT_ANTHROPIC: ["claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5", "claude-haiku-5-5"],
    FORMAT_OPENAI: [],  # 固定しない(一覧から選ぶか入力する)
}
DEFAULT_MODEL = {FORMAT_ANTHROPIC: "claude-opus-5-5", FORMAT_OPENAI: ""}
# OpenAI 形式の API の種類
OPENAI_API_AUTO = "auto"
OPENAI_API_RESPONSES = "responses"
OPENAI_API_CHAT = "chat"
OPENAI_APIS = (OPENAI_API_AUTO, OPENAI_API_RESPONSES, OPENAI_API_CHAT)
OPENAI_API_LABELS = {
    OPENAI_API_AUTO: "自動(公式の接続先は Responses、ほかは Chat Completions)",
    OPENAI_API_RESPONSES: "Responses API",
    OPENAI_API_CHAT: "Chat Completions API",
}
# 会話の形の種類(履歴の形が違うので、会話の途中では変えられない)
KIND_ANTHROPIC = "anthropic"
KIND_OPENAI_CHAT = "openai-chat"
KIND_OPENAI_RESPONSES = "openai-responses"
KIND_LABELS = {KIND_ANTHROPIC: "Anthropic(Messages API)", KIND_OPENAI_CHAT: "OpenAI(Chat Completions API)",
               KIND_OPENAI_RESPONSES: "OpenAI(Responses API)"}
DEFAULT_MAX_TOKENS = {FORMAT_ANTHROPIC: 32000, FORMAT_OPENAI: 16384}

THINKING_DISPLAYS = ("auto", "omit", "updates", "summarized")
THINKING_LABELS = {
    "auto": "自動(対応するモデルでは途中の報告を表示)",
    "omit": "送らない(モデルの既定)",
    "updates": "途中の報告(display: updates、beta)",
    "summarized": "考えの要約(display: summarized)",
}
EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
TOKEN_PARAMS = ("auto", "max_completion_tokens", "max_tokens", "none")
TOKEN_PARAM_LABELS = {
    "auto": "自動(公式は max_completion_tokens、ほかは max_tokens)",
    "max_completion_tokens": "max_completion_tokens",
    "max_tokens": "max_tokens(古い互換サーバー向け)",
    "none": "送らない",
}
UPDATES_MODELS = frozenset({"claude-fable-5-1", "claude-fable-5", "claude-mythos-5-1", "claude-opus-5-5",
                            "claude-sonnet-5-5"})
# 安全の分類で断るモデルのうち、サーバー側の切り替えのあるもの(Haiku 5.5 にはない。Refusals and fallback、2026-10-18)
FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
BETA_THINKING_UPDATES = "thinking-display-updates-2026-08-18"
BETA_FALLBACK_DEFAULT = "server-side-fallback-2026-07-01"


def default_workspace(root: Path) -> Path:
    """既定の作業フォルダ: 環境変数 ``GSIS_GUI_AI_WORKSPACE``、なければ(試験・スクリーンショットで設定ファイルを
    ``GSIS_GUI_SETTINGS`` で差し替えているとき)その隣の ``ai_workspace``、なければ ``<リポジトリ>/mcp_workspace/gui``
    (git の対象外)。"""
    env = os.environ.get("GSIS_GUI_AI_WORKSPACE", "").strip()
    if env:
        return Path(env).expanduser()
    s = os.environ.get("GSIS_GUI_SETTINGS", "").strip()
    if s:
        return Path(s).expanduser().parent / "ai_workspace"
    return Path(root) / "mcp_workspace" / "gui"


def normalize_base_url(url: str | None, fmt: str) -> str:
    """接続先の URL(空なら公式。末尾の / は除く)。"""
    u = (url or "").strip()
    if not u:
        u = OFFICIAL_BASE_URL[fmt]
    return u.rstrip("/")


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


@dataclass
class FormatSettings:
    """形式ごとの設定。"""

    base_url: str = ""
    model: str = ""
    models: list[str] = field(default_factory=list)
    max_tokens: int = 16384
    no_key: bool = False  # キーなしで送る(OpenAI 互換のローカルサーバー)
    use_env_key: bool = False  # 公式以外の接続先にも、環境変数のキーを使う
    token_param: str = "auto"  # OpenAI 形式(Chat Completions): 出力の上限の引数
    extra_body: str = ""  # 追加の引数(JSON のオブジェクト)。リクエストの本文に足す
    api: str = "auto"  # OpenAI 形式: API の種類(auto / responses / chat)
    store: bool = False  # OpenAI 形式(Responses): 応答を OpenAI 側に保存する(store)。既定は保存しない


def default_format_settings(fmt: str) -> FormatSettings:
    return FormatSettings(base_url=OFFICIAL_BASE_URL[fmt], model=DEFAULT_MODEL[fmt], models=list(DEFAULT_MODELS[fmt]),
                          max_tokens=DEFAULT_MAX_TOKENS[fmt])


@dataclass
class AiSettings:
    """AI モデリングの設定。"""

    format: str = FORMAT_ANTHROPIC
    per_format: dict[str, FormatSettings] = field(
        default_factory=lambda: {f: default_format_settings(f) for f in FORMATS})
    tool_limit: int = 40  # 1 回の指示あたりのツール呼び出しの上限
    send_images: bool = False  # render の画像を AI に送る
    workspace: str = ""  # 作業フォルダ(空なら <リポジトリ>/mcp_workspace/gui)
    stream: bool = True  # 応答を逐次受け取る(ストリーミング)
    prompt_cache: bool = True  # Anthropic のプロンプトキャッシュ(システムとツールの定義、会話)
    thinking_display: str = "auto"  # Anthropic の考えの表示
    effort: str = ""  # Anthropic の output_config.effort(空ならモデルの既定)
    fallbacks: bool = True  # Anthropic 公式: 安全の分類で断られたら別のモデルで続ける(fallbacks: "default")
    confirm_tools: bool = False  # ツールを実行する前に毎回確かめる
    timeout_s: float = 600.0  # 1 回のリクエストのタイムアウト
    max_retries: int = 2  # 429・5xx・通信の失敗の再試行の回数(SDK の既定と同じ)

    # --- 今の形式の値 ---
    @property
    def current(self) -> FormatSettings:
        if self.format not in self.per_format:
            self.per_format[self.format] = default_format_settings(self.format)
        return self.per_format[self.format]

    @property
    def base_url(self) -> str:
        return normalize_base_url(self.current.base_url, self.format)

    @property
    def model(self) -> str:
        return self.current.model.strip()

    @property
    def max_tokens(self) -> int:
        return int(self.current.max_tokens)

    def is_official(self) -> bool:
        return host_of(self.base_url) == OFFICIAL_HOST[self.format]

    def effective_token_param(self) -> str:
        p = self.current.token_param
        if p in ("max_completion_tokens", "max_tokens", "none"):
            return p
        return "max_completion_tokens" if self.is_official() else "max_tokens"

    def effective_openai_api(self) -> str:
        """OpenAI 形式の API の種類(``responses`` / ``chat``)。"""
        a = self.current.api if self.format == FORMAT_OPENAI else OPENAI_API_CHAT
        if a in (OPENAI_API_RESPONSES, OPENAI_API_CHAT):
            return a
        return OPENAI_API_RESPONSES if self.is_official() else OPENAI_API_CHAT

    def provider_kind(self) -> str:
        """会話の形の種類(``anthropic`` / ``openai-chat`` / ``openai-responses``)。"""
        if self.format == FORMAT_ANTHROPIC:
            return KIND_ANTHROPIC
        return KIND_OPENAI_RESPONSES if self.effective_openai_api() == OPENAI_API_RESPONSES else KIND_OPENAI_CHAT

    def effective_thinking_display(self) -> str:
        """``omit`` / ``updates`` / ``summarized``(Anthropic)。"""
        d = self.thinking_display
        if d == "auto":
            return "updates" if (self.model in UPDATES_MODELS and self.is_official()) else "omit"
        return d if d in ("omit", "updates", "summarized") else "omit"

    def use_fallbacks(self) -> bool:
        return (self.format == FORMAT_ANTHROPIC and self.fallbacks and self.is_official()
                and self.model in FALLBACK_MODELS)

    def extra_body(self) -> dict[str, Any]:
        """追加の引数(JSON のオブジェクト)。書き方が誤っていれば ValueError。"""
        text = self.current.extra_body.strip()
        if not text:
            return {}
        try:
            v = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"追加の引数が JSON として読めません: {exc}") from exc
        if not isinstance(v, dict):
            raise ValueError("追加の引数は JSON のオブジェクト({...})にしてください")
        return v

    def workspace_path(self, root: Path) -> Path:
        if self.workspace.strip():
            return Path(self.workspace.strip()).expanduser()
        return default_workspace(root)

    def problems(self) -> list[str]:
        """送る前に直すべき設定の誤り。"""
        out = []
        if self.format not in FORMATS:
            out.append(f"形式が不明です: {self.format}")
            return out
        if not self.model:
            out.append("モデルを選ぶか入力してください(設定の「モデル」)")
        u = self.base_url
        if not u.startswith(("http://", "https://")) or not host_of(u):
            out.append(f"接続先(base URL)が正しくありません: {u}")
        if self.max_tokens < 1:
            out.append("最大トークンは 1 以上にしてください")
        if self.tool_limit < 1:
            out.append("ツール呼び出しの上限は 1 以上にしてください")
        try:
            self.extra_body()
        except ValueError as exc:
            out.append(str(exc))
        return out

    # --- 保存(QSettings などの value / setValue を持つもの) ---
    def save(self, store: Any) -> None:
        store.setValue("ai/format", self.format)
        for k in ("tool_limit", "send_images", "workspace", "stream", "prompt_cache", "thinking_display", "effort",
                  "fallbacks", "confirm_tools", "timeout_s", "max_retries"):
            store.setValue(f"ai/{k}", getattr(self, k))
        for fmt, fs in self.per_format.items():
            for k in ("base_url", "model", "max_tokens", "no_key", "use_env_key", "token_param", "extra_body", "api",
                      "store"):
                store.setValue(f"ai/{fmt}/{k}", getattr(fs, k))
            store.setValue(f"ai/{fmt}/models", json.dumps(list(fs.models), ensure_ascii=False))
        sync = getattr(store, "sync", None)
        if callable(sync):
            sync()

    @classmethod
    def load(cls, store: Any) -> AiSettings:
        s = cls()

        def get(key: str, default: Any) -> Any:
            v = store.value(key, None)
            return default if v is None else v

        fmt = str(get("ai/format", s.format))
        s.format = fmt if fmt in FORMATS else FORMAT_ANTHROPIC
        s.tool_limit = _to_int(get("ai/tool_limit", s.tool_limit), s.tool_limit, lo=1)
        s.send_images = _to_bool(get("ai/send_images", s.send_images))
        s.workspace = str(get("ai/workspace", s.workspace) or "")
        s.stream = _to_bool(get("ai/stream", s.stream))
        s.prompt_cache = _to_bool(get("ai/prompt_cache", s.prompt_cache))
        td = str(get("ai/thinking_display", s.thinking_display))
        s.thinking_display = td if td in THINKING_DISPLAYS else "auto"
        ef = str(get("ai/effort", s.effort) or "")
        s.effort = ef if ef in EFFORTS else ""
        s.fallbacks = _to_bool(get("ai/fallbacks", s.fallbacks))
        s.confirm_tools = _to_bool(get("ai/confirm_tools", s.confirm_tools))
        s.timeout_s = _to_float(get("ai/timeout_s", s.timeout_s), s.timeout_s, lo=1.0)
        s.max_retries = _to_int(get("ai/max_retries", s.max_retries), s.max_retries, lo=0)
        for f in FORMATS:
            d = default_format_settings(f)
            fs = FormatSettings(
                base_url=str(get(f"ai/{f}/base_url", d.base_url) or d.base_url),
                model=str(get(f"ai/{f}/model", d.model) or ""),
                max_tokens=_to_int(get(f"ai/{f}/max_tokens", d.max_tokens), d.max_tokens, lo=1),
                no_key=_to_bool(get(f"ai/{f}/no_key", False)),
                use_env_key=_to_bool(get(f"ai/{f}/use_env_key", False)),
                token_param=str(get(f"ai/{f}/token_param", "auto")),
                extra_body=str(get(f"ai/{f}/extra_body", "") or ""),
                api=str(get(f"ai/{f}/api", OPENAI_API_AUTO) or OPENAI_API_AUTO),
                store=_to_bool(get(f"ai/{f}/store", False)),
            )
            if fs.token_param not in TOKEN_PARAMS:
                fs.token_param = "auto"
            if fs.api not in OPENAI_APIS:
                fs.api = OPENAI_API_AUTO
            raw = get(f"ai/{f}/models", None)
            try:
                models = json.loads(raw) if isinstance(raw, str) else list(raw or [])
            except (TypeError, ValueError):
                models = []
            fs.models = [str(m) for m in models if str(m).strip()] or list(d.models)
            s.per_format[f] = fs
        return s

    def copy(self) -> AiSettings:
        import copy

        return copy.deepcopy(self)


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _to_int(v: Any, default: int, lo: int | None = None) -> int:
    try:
        x = int(float(v))
    except (TypeError, ValueError):
        return default
    return max(lo, x) if lo is not None else x


def _to_float(v: Any, default: float, lo: float | None = None) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return max(lo, x) if lo is not None else x
