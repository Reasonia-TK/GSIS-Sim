"""TK 標準の warm clay テーマ(ライト・ダーク)の色トークン。

色はこのファイルのトークンだけから使う(画面・グラフ・3D 表示に HEX を直書きしない)。
ライトとダークは同じトークン名の 2 組で、切り替えは組の差し替えで行う。

- :data:`LIGHT` / :data:`DARK` … UI の色
- :data:`SERIES` … グラフ・可視化の系列色(6 色)
- :func:`mpl_rc` … matplotlib の rcParams(図の背景は ``bg``、軸・目盛は ``text_muted``・``border``)
- 連続量のカラーマップは ``cividis``(既定)か ``viridis``(テーマ色で自作しない)

Qt 用の部分(パレット・スタイルシート・モード切替)は :mod:`gsis_tools.gui.qtheme`。
"""

from __future__ import annotations

LIGHT = dict(
    bg="#FAF9F5", surface="#F0EEE6", surface_2="#E8E6DC", border="#E0DDD4",
    text="#141413", text_muted="#6B6A65", text_disabled="#A8A69E",
    accent="#C96442", accent_strong="#B4532F", accent_hover="#A84F31",
    accent_fg="#FFFFFF", accent_text="#A84F31", selection="#C9644226", focus="#C96442",
    success="#2F7D4F", warning="#9A6700", error="#B42318", info="#3B6EA8",
)
DARK = dict(
    bg="#262624", surface="#30302E", surface_2="#3A3A37", border="#45453F",
    text="#F5F4EE", text_muted="#A6A39A", text_disabled="#6B6A65",
    accent="#D97757", accent_strong="#D97757", accent_hover="#E08A6A",
    accent_fg="#1F1E1D", accent_text="#E08A6A", selection="#D9775733", focus="#D97757",
    success="#6FBF8B", warning="#E3B341", error="#F07167", info="#7AA7D9",
)
SERIES = {
    "light": ["#C96442", "#3B6EA8", "#2F7D4F", "#9A6700", "#7B5EA7", "#5F7A7A"],
    "dark": ["#D97757", "#7AA7D9", "#6FBF8B", "#E3B341", "#B39DDB", "#9DB8B8"],
}
COLORMAPS = ("cividis", "viridis")
MODES = ("system", "light", "dark")
MODE_LABELS = {"system": "システムに合わせる", "light": "ライト", "dark": "ダーク"}


def tokens(scheme: str) -> dict[str, str]:
    """``"light"`` / ``"dark"`` のトークン。"""
    return dict(DARK if scheme == "dark" else LIGHT)


def series(scheme: str) -> list[str]:
    return list(SERIES["dark" if scheme == "dark" else "light"])


def rgba(hex_color: str) -> tuple[int, int, int, int]:
    """``#RRGGBB`` / ``#RRGGBBAA``(CSS の並び)→ (r, g, b, a)。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = int(h[6:8], 16) if len(h) == 8 else 255
    return r, g, b, a


def rgb_float(hex_color: str) -> tuple[float, float, float]:
    r, g, b, _ = rgba(hex_color)
    return r / 255.0, g / 255.0, b / 255.0


def patch_color(scheme: str, name: str, kind: str | None, index: int) -> str:
    """境界パッチの色(系列色から。壁は落ち着いた 6 番、入口は 2 番、出口は 3 番)。"""
    s = series(scheme)
    key = (kind or "").lower() or name.lower()
    if "inlet" in key or key.startswith("in"):
        return s[1]
    if "outlet" in key or key.startswith("out") or "vacuum" in key:
        return s[2]
    if "wall" in key:
        return s[5]
    if "sym" in key:
        return s[4]
    return s[(index + 3) % len(s)]


def mpl_rc(t: dict[str, str], colors: list[str]) -> dict:
    """matplotlib の rcParams(テーマのトークンから)。"""
    from cycler import cycler

    return {
        "figure.facecolor": t["bg"],
        "axes.facecolor": t["bg"],
        "savefig.facecolor": t["bg"],
        "savefig.edgecolor": t["bg"],
        "axes.edgecolor": t["border"],
        "grid.color": t["border"],
        "text.color": t["text"],
        "axes.labelcolor": t["text"],
        "axes.titlecolor": t["text"],
        "xtick.color": t["text_muted"],
        "ytick.color": t["text_muted"],
        "legend.facecolor": t["bg"],
        "legend.edgecolor": t["border"],
        "legend.labelcolor": t["text"],
        "axes.prop_cycle": cycler(color=colors),
        "axes.grid": True,
        "grid.linewidth": 0.6,
        "font.family": ["Yu Gothic UI", "Meiryo", "Segoe UI", "DejaVu Sans"],
        "mathtext.fontset": "dejavusans",
        "font.size": 9.5,
        "axes.unicode_minus": False,
    }


def apply_mpl(scheme: str) -> None:
    """matplotlib の全体設定にテーマを入れる(これから作る図に効く)。"""
    import matplotlib as mpl

    mpl.rcParams.update(mpl_rc(tokens(scheme), series(scheme)))
