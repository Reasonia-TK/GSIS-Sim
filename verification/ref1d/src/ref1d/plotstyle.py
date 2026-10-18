"""図の配色(TK 標準の warm clay テーマのトークン)。図の色はここ以外に直書きしない。

PNG は表示側でモードを切り替えられないので、既定はライトモード。`apply(mode="dark")` でダークも出せる。
- 種類の違う系列(運動論 / NSF / 自由分子など): SERIES の順に使い、線種・マーカーも変えて色だけに頼らない
- Kn のような順序のある量: viridis の一部(明るすぎる黄色の端は背景と区別しにくいので使わない)
"""

from __future__ import annotations

import matplotlib as mpl
import numpy as np

LIGHT = dict(
    bg="#FAF9F5", surface="#F0EEE6", surface_2="#E8E6DC", border="#E0DDD4",
    text="#141413", text_muted="#6B6A65", text_disabled="#A8A69E",
    accent="#C96442", accent_strong="#B4532F", accent_hover="#A84F31",
    accent_fg="#FFFFFF", accent_text="#A84F31", focus="#C96442",
    success="#2F7D4F", warning="#9A6700", error="#B42318", info="#3B6EA8",
)
DARK = dict(
    bg="#262624", surface="#30302E", surface_2="#3A3A37", border="#45453F",
    text="#F5F4EE", text_muted="#A6A39A", text_disabled="#6B6A65",
    accent="#D97757", accent_strong="#D97757", accent_hover="#E08A6A",
    accent_fg="#1F1E1D", accent_text="#E08A6A", focus="#D97757",
    success="#6FBF8B", warning="#E3B341", error="#F07167", info="#7AA7D9",
)
SERIES = {
    "light": ["#C96442", "#3B6EA8", "#2F7D4F", "#9A6700", "#7B5EA7", "#5F7A7A"],
    "dark": ["#D97757", "#7AA7D9", "#6FBF8B", "#E3B341", "#B39DDB", "#9DB8B8"],
}
# 系列ごとの線種とマーカー(色と併用する 2 つ目の手がかり)
LINESTYLES = ["-", "--", "-.", ":", (0, (5, 1, 1, 1)), (0, (3, 1, 1, 1, 1, 1))]
MARKERS = ["o", "s", "^", "D", "v", "P"]

_mode = "light"


def tokens() -> dict:
    return LIGHT if _mode == "light" else DARK


def series(i: int) -> str:
    return SERIES[_mode][i % len(SERIES[_mode])]


def ordinal_colors(n: int) -> list:
    """順序のある系列(Kn など)の色。viridis の 0.0–0.82 の範囲を等分する。"""
    cmap = mpl.colormaps["viridis"]
    return [cmap(v) for v in np.linspace(0.0, 0.82, n)]


def apply(mode: str = "light") -> None:
    global _mode
    _mode = mode
    t = tokens()
    mpl.rcParams.update(
        {
            "figure.facecolor": t["bg"],
            "axes.facecolor": t["bg"],
            "savefig.facecolor": t["bg"],
            "axes.edgecolor": t["border"],
            "grid.color": t["border"],
            "grid.linewidth": 0.8,
            "grid.linestyle": "-",
            "axes.grid": True,
            "axes.axisbelow": True,
            "text.color": t["text"],
            "axes.labelcolor": t["text"],
            "axes.titlecolor": t["text"],
            "xtick.color": t["text_muted"],
            "ytick.color": t["text_muted"],
            "xtick.labelcolor": t["text_muted"],
            "ytick.labelcolor": t["text_muted"],
            "legend.frameon": False,
            "legend.labelcolor": t["text"],
            "axes.prop_cycle": mpl.cycler(color=SERIES[mode]),
            "lines.linewidth": 1.6,
            "lines.markersize": 5,
            "font.family": ["Yu Gothic UI", "Segoe UI", "Meiryo", "sans-serif"],
            "font.size": 9.5,
            "axes.titlesize": 10.5,
            "axes.labelsize": 9.5,
            "legend.fontsize": 8.5,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.unicode_minus": False,
            "axes.formatter.useoffset": False,
            "mathtext.fontset": "dejavusans",
        }
    )
