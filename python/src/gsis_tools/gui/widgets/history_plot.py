"""収束の履歴(ε・残差・更新量)とパッチごとの質量流量のグラフ。"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from .. import theme
from ..history import COLUMN_LABELS, History, mass_balance, mass_flow_series
from .plots import PlotPanel


def column_label(name: str) -> str:
    return COLUMN_LABELS.get(name, name)


class HistoryPlot(QWidget):
    """上: 収束(対数軸)、下: 質量流量。列は「描く列」のメニューで選ぶ。"""

    def __init__(self, parent: QWidget | None = None, toolbar: bool = True):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        bar = QHBoxLayout()
        self.columns_btn = QToolButton()
        self.columns_btn.setText("収束のグラフに描く列")
        self.columns_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self)
        self.columns_btn.setMenu(self.menu)
        self.time_axis = QCheckBox("横軸を経過時間にする")
        self.other_patches = QCheckBox("壁なども描く")
        bar.addWidget(self.columns_btn)
        bar.addWidget(self.time_axis)
        bar.addWidget(self.other_patches)
        bar.addSpacing(12)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        self.info.setWordWrap(True)
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bar.addWidget(self.info, 1)
        lay.addLayout(bar)
        self.panel = PlotPanel(toolbar=toolbar)
        lay.addWidget(self.panel, 1)
        self.hist: History | None = None
        self.nd_to_Mdot: float | None = None
        self.mass_flow_scale: float | None = None
        self.patch_types: dict[str, str] | None = None
        self.tolerance: float | None = None
        self.reference_mdot: list[tuple[str, float]] = []
        self.selected: list[str] | None = None
        self._kind = None
        self.scheme = "light"
        self.time_axis.toggled.connect(lambda _b: self.redraw())
        self.other_patches.toggled.connect(lambda _b: self.redraw())
        self.redraw()

    def set_context(self, *, nd_to_Mdot: float | None = None, mass_flow_scale: float | None = None,
                    patch_types: dict[str, str] | None = None, tolerance: float | None = None,
                    reference_mdot: list[tuple[str, float]] | None = None) -> None:
        self.nd_to_Mdot = nd_to_Mdot
        self.mass_flow_scale = mass_flow_scale
        self.patch_types = patch_types
        self.tolerance = tolerance
        self.reference_mdot = list(reference_mdot or [])

    def set_history(self, hist: History | None) -> None:
        kind_changed = hist is None or self.hist is None or hist.columns != self.hist.columns
        self.hist = hist
        if kind_changed:
            self._rebuild_menu()
        self.redraw()

    def _rebuild_menu(self) -> None:
        self.menu.clear()
        if self.hist is None or not self.hist.columns:
            self.selected = None
            return
        cands = self.hist.convergence_candidates()
        default = self.hist.convergence_columns()
        if self.selected is None or not set(self.selected) & set(cands):
            self.selected = [c for c in default if c in cands]
        for c in cands:
            act = QAction(column_label(c) + (f"({c})" if column_label(c) != c else ""), self.menu)
            act.setCheckable(True)
            act.setChecked(c in self.selected)
            act.toggled.connect(lambda on, name=c: self._toggle(name, on))
            self.menu.addAction(act)

    def _toggle(self, name: str, on: bool) -> None:
        sel = list(self.selected or [])
        if on and name not in sel:
            sel.append(name)
        if not on and name in sel:
            sel.remove(name)
        self.selected = sel
        self.redraw()

    def restyle(self, scheme: str) -> None:
        self.scheme = scheme
        self.panel.restyle(scheme)
        self.redraw()

    def redraw(self) -> None:
        t = theme.tokens(self.scheme)
        fig = self.panel.figure
        fig.clear()
        ax1, ax2 = fig.subplots(2, 1, sharex=True)
        h = self.hist
        use_time = self.time_axis.isChecked()
        xlabel = "経過時間 [s]" if use_time else "反復"
        if h is None or h.n == 0:
            for ax in (ax1, ax2):
                ax.text(0.5, 0.5, "履歴はまだありません", ha="center", va="center", transform=ax.transAxes,
                        color=t["text_muted"])
            ax2.set_xlabel(xlabel)
            ax1.set_ylabel("収束")
            ax2.set_ylabel("質量流量")
            self.info.setText("")
            self.panel.draw()
            return
        x = h.elapsed_seconds() if use_time and h.elapsed_seconds() is not None else h.iterations()
        # 収束
        drawn = False
        for c in self.selected or []:
            if not h.has(c):
                continue
            y = np.abs(h.column(c))
            ok = np.isfinite(y) & (y > 0)
            if ok.any():
                lw = 1.8 if c in ("epsilon", "update_p") else 1.1
                ax1.semilogy(x[ok], y[ok], label=column_label(c), linewidth=lw)
                drawn = True
        if self.tolerance and drawn:
            ax1.axhline(self.tolerance, color=t["text_muted"], linestyle="--", linewidth=0.9,
                        label=f"収束判定 {self.tolerance:g}")
        ax1.set_ylabel("収束(対数)")
        if drawn:
            ax1.legend(loc="upper right", fontsize=8, frameon=True)
        # 質量流量
        series, ylabel = mass_flow_series(h, nd_to_Mdot=self.nd_to_Mdot, mass_flow_scale=self.mass_flow_scale,
                                          patch_types=self.patch_types, include_other=self.other_patches.isChecked())
        colors = theme.series(self.scheme)
        patch_index = {p: i for i, p in enumerate(dict.fromkeys(s.patch for s in series))}
        for s in series:
            ok = np.isfinite(s.y)
            color = colors[(patch_index[s.patch] + 1) % len(colors)]
            if s.source == "pbc":  # GSIS の PBC 側は同じ色の破線
                ax2.plot(x[ok], s.y[ok], label=s.label, color=color, linewidth=1.1, linestyle="--")
            else:
                ax2.plot(x[ok], s.y[ok], label=s.label, color=color, linewidth=1.4)
        for label, val in self.reference_mdot:
            if val is not None and math.isfinite(val):
                ax2.axhline(val, color=t["text_muted"], linestyle="--", linewidth=0.9, label=label)
        if series:
            # 初めの過渡で大きく振れるので、後半の値が見える範囲にする
            tail = np.concatenate([s.y[len(s.y) // 3:] for s in series if s.y.size])
            tail = tail[np.isfinite(tail)]
            ref = [v for _, v in self.reference_mdot if v is not None and math.isfinite(v)]
            vals = np.concatenate([tail, np.asarray(ref, dtype=float)]) if ref else tail
            if vals.size:
                lo, hi = float(np.min(vals)), float(np.max(vals))
                pad = max(0.15 * (hi - lo), 0.02 * max(abs(hi), abs(lo), 1e-12))
                ax2.set_ylim(lo - pad, hi + pad)
            ax2.legend(loc="best", fontsize=8, frameon=True)
        ax2.set_ylabel(ylabel)
        ax2.set_xlabel(xlabel)
        # 右上の要約
        parts = [f"{h.n} 行"]
        it = h.last("iteration")
        if math.isfinite(it):
            parts.append(f"反復 {int(it)}")
        for c in ("epsilon", "update_p"):
            v = h.last(c)
            if math.isfinite(v):
                parts.append(f"{column_label(c)} {v:.3e}")
                break
        for s in series:
            if s.source == "main" and s.y.size and math.isfinite(s.y[-1]):
                parts.append(f"{s.label} {s.y[-1]:.5g}")
        mb = mass_balance(series)
        if math.isfinite(mb):
            parts.append(f"流入と流出の差 {mb:.1e}")
        self.info.setText("、".join(parts))
        self.panel.draw()
