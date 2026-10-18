"""3D 表示(pyvista)。

表示の方式は 2 つ:

- ``"interactive"``: pyvistaqt の ``QtInteractor``(マウスで回転・拡大できる。通常はこちら)
- ``"static"``: オフスクリーンの ``pyvista.Plotter`` で描いた画像を貼る(Qt の ``offscreen`` プラットフォーム、
  テスト、スクリーンショットの作成、OpenGL が使えないとき)

どちらも同じ :class:`Scene`(描くものの一覧と視点)を受け取り、:func:`apply_scene` で描く。
環境変数 ``GSIS_GUI_3D`` に ``static`` / ``interactive`` / ``none`` を入れると方式を選べる。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import theme
from ..fields import OBLIQUE_VIEW
from .common import Legend, muted

VIEWS = {"iso": "斜めから", "xy": "上から(z 軸方向)", "xz": "横から(y 軸方向)", "focus": "管の付近",
         "plane": "断面に垂直"}


@dataclass
class SceneItem:
    """描くもの 1 つ(pyvista の ``add_mesh`` の引数に対応)。"""

    mesh: Any
    scalars: str | None = None
    cmap: str | None = None
    clim: tuple[float, float] | None = None
    color: str | None = None  # テーマのトークンか系列色(HEX)
    show_edges: bool = False
    edge_color: str | None = None
    line_width: float = 1.0
    opacity: float = 1.0
    scalar_bar_title: str | None = None  # None なら色の帯を出さない
    style: str | None = None  # "surface" / "wireframe"
    lighting: bool = True
    n_colors: int = 256
    render_lines_as_tubes: bool = False
    scalar_bar_pos: str = "right"  # "right" / "left"(色の帯が 2 本のとき)


@dataclass
class Scene:
    items: list[SceneItem] = field(default_factory=list)
    view: str = "iso"
    focus_bounds: tuple[float, float, float, float, float, float] | None = None
    legend: list[tuple[str, str]] = field(default_factory=list)
    caption: str = ""
    parallel: bool = False
    plane_normal: tuple[float, float, float] | None = None  # 「断面に垂直」に見るときの向き


# VTK の色の帯の題で描けない文字の置き換え(2026-10-16 に描いて確かめた: 「|」「∣」「‖」と ρ・σ は
# 描かれず、[ ] は丸かっこになる。√・² ³ と ASCII は描ける。日本語は描けない)
_VTK_TEXT = {"ρ": "rho", "σ": "sigma", "[": "(", "]": ")"}
_VTK_ABS = re.compile(r"\|([^|]+)\|")


def vtk_safe_text(text: str) -> str:
    """VTK の文字(色の帯の題)に渡す文字列(描けない文字を置き換える。|u| → u_mag)。"""
    text = _VTK_ABS.sub(r"\1_mag", text)
    return "".join(_VTK_TEXT.get(c, c) for c in text)


def default_backend() -> str:
    env = os.environ.get("GSIS_GUI_3D", "").strip().lower()
    if env in ("static", "interactive", "none"):
        return env
    if QGuiApplication.platformName() in ("offscreen", "minimal"):
        return "static"
    return "interactive"


def apply_scene(plotter: Any, scene: Scene, t: dict[str, str], keep_camera: bool = False) -> None:
    """``plotter``(pyvista の Plotter / QtInteractor)に場面を描く。"""
    cam = plotter.camera_position if keep_camera and plotter.renderer.actors else None
    plotter.clear()
    plotter.set_background(t["bg"])
    for item in scene.items:
        kw: dict[str, Any] = dict(opacity=item.opacity, line_width=item.line_width, lighting=item.lighting)
        if item.render_lines_as_tubes:
            kw["render_lines_as_tubes"] = True
        if item.scalars is not None:
            kw.update(scalars=item.scalars, cmap=item.cmap or "cividis", n_colors=item.n_colors)
            if item.clim is not None:
                kw["clim"] = item.clim
            if item.scalar_bar_title is not None:
                kw["show_scalar_bar"] = True
                kw["scalar_bar_args"] = dict(
                    # 題の後の改行で、題と一番上の目盛りが重ならないようにする
                    title=vtk_safe_text(item.scalar_bar_title) + "\n", color=t["text"], fmt="%.4g", n_labels=6,
                    vertical=True,
                    position_x=0.03 if item.scalar_bar_pos == "left" else 0.86, position_y=0.1, width=0.08,
                    height=0.78, title_font_size=13, label_font_size=11, shadow=False,
                )
            else:
                kw["show_scalar_bar"] = False
        else:
            kw["color"] = item.color or t["text_muted"]
            kw["show_scalar_bar"] = False
        if item.show_edges:
            kw.update(show_edges=True, edge_color=item.edge_color or t["border"])
        if item.style:
            kw["style"] = item.style
        plotter.add_mesh(item.mesh, **kw)
    for bar in list(getattr(plotter, "scalar_bars", {}).values()):
        try:  # 色の帯・題・目盛りの背景(場面の上に重なっても読めるように)
            bar.SetDrawBackground(True)
            prop = bar.GetBackgroundProperty()
            r, g, b = (int(t["bg"][i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            prop.SetColor(r, g, b)
            prop.SetOpacity(0.88)
            for tp in (bar.GetTitleTextProperty(), bar.GetLabelTextProperty()):
                tp.SetBackgroundColor(r, g, b)
                tp.SetBackgroundOpacity(0.88)
        except AttributeError:
            pass
    if scene.parallel:
        plotter.enable_parallel_projection()
    else:
        plotter.disable_parallel_projection()
    if cam is not None:
        plotter.camera_position = cam
    else:
        set_view(plotter, scene.view, scene.focus_bounds, scene.parallel, scene.plane_normal)


# 斜めから見る向き: 管の軸(x)を横に、少し上(+z)と手前(−y)から見る(入口の面と z = 0 の断面が見える)
OBLIQUE = OBLIQUE_VIEW


def _normal_view(plotter: Any, normal) -> None:
    """法線の向き(+n の側)から断面を見る。"""
    n = np.asarray(normal, dtype=float)
    n = n / np.linalg.norm(n)
    up = (0.0, 1.0, 0.0) if abs(n[2]) > 0.9 else (0.0, 0.0, 1.0)
    plotter.view_vector(tuple(n), viewup=up)


def set_view(plotter: Any, view: str, focus_bounds=None, parallel: bool = False, plane_normal=None) -> None:
    """視点: ``iso``(斜めから全体)・``xy``(上から全体)・``xz``・``plane``(断面に垂直、全体)・
    ``focus``(管の付近を拡大。断面の表示では断面に垂直に見る)。"""
    if view == "xy":
        plotter.view_xy()
    elif view == "xz":
        plotter.view_xz()
    elif view == "plane" and plane_normal is not None:
        _normal_view(plotter, plane_normal)
    elif view == "focus" and focus_bounds is not None:
        if parallel:
            if plane_normal is not None:
                _normal_view(plotter, plane_normal)
            else:
                plotter.view_xy()
        else:
            plotter.view_vector(OBLIQUE, viewup=(0.0, 0.0, 1.0))
        plotter.reset_camera(bounds=focus_bounds)
        plotter.camera.zoom(1.1 if parallel else 1.25)
        return
    else:
        plotter.view_vector(OBLIQUE, viewup=(0.0, 0.0, 1.0))
    plotter.reset_camera()


class VtkView(QWidget):
    """3D 表示の部品。:meth:`set_scene` で描く。"""

    def __init__(self, parent: QWidget | None = None, backend: str | None = None):
        super().__init__(parent)
        self.backend = backend or default_backend()
        self._scene: Scene | None = None
        self._tokens = theme.tokens("light")
        self.plotter: Any = None
        self._image_label: QLabel | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.setMinimumSize(320, 240)
        if self.backend == "interactive":
            try:
                from pyvistaqt import QtInteractor

                self.plotter = QtInteractor(self, auto_update=False)
                self.plotter.set_background(self._tokens["bg"])
                lay.addWidget(self.plotter, 1)
            except Exception as exc:  # OpenGL が使えないなど
                self.backend = "static"
                self._init_error = str(exc)
        if self.backend == "static":
            self._image_label = QLabel()
            self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            lay.addWidget(self._image_label, 1)
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(150)
            self._timer.timeout.connect(self._render_static)
        if self.backend == "none":
            lay.addWidget(muted("3D 表示は無効です(GSIS_GUI_3D=none)"), 1)
        self.legend = Legend()
        self.caption = muted("", wrap=True)
        lay.addWidget(self.legend)
        lay.addWidget(self.caption)
        self._static_image: np.ndarray | None = None

    # --- 公開 ---
    def set_scene(self, scene: Scene, keep_camera: bool = False) -> None:
        self._scene = scene
        self.legend.set_entries(scene.legend)
        self.caption.setText(scene.caption)
        self.caption.setVisible(bool(scene.caption))
        if self.backend == "interactive" and self.plotter is not None:
            apply_scene(self.plotter, scene, self._tokens, keep_camera=keep_camera)
            self._add_plane_widget()  # clear() で表示が消えるので付け直す
            self.plotter.render()
        elif self.backend == "static":
            self._render_static()

    def scene(self) -> Scene | None:
        return self._scene

    # --- 平面ウィジェット(対話型の 3D 表示だけ) ---
    @property
    def supports_plane_widget(self) -> bool:
        return self.backend == "interactive" and self.plotter is not None

    def set_plane_widget(self, enabled: bool, origin=None, normal=None, bounds=None, callback=None) -> bool:
        """断面を手で動かす平面ウィジェットを出す・消す。``callback(normal, origin)``(origin は m 単位)は
        マウスを離したときに呼ばれる。出せなければ False。"""
        self._plane_widget = None
        if enabled and self.supports_plane_widget and origin is not None and normal is not None:
            self._plane_widget = dict(origin=tuple(float(v) for v in origin), normal=tuple(float(v) for v in normal),
                                      bounds=tuple(bounds) if bounds is not None else None, callback=callback)
        if self.supports_plane_widget:
            self._add_plane_widget()
            self.plotter.render()
        return self._plane_widget is not None or not enabled

    def _add_plane_widget(self) -> None:
        if not self.supports_plane_widget:
            return
        try:
            self.plotter.clear_plane_widgets()
        except Exception:
            pass
        w = getattr(self, "_plane_widget", None)
        if not w:
            return
        cb = w["callback"]

        def on_end(normal, origin) -> None:
            w["origin"] = tuple(float(v) for v in origin)
            w["normal"] = tuple(float(v) for v in normal)
            if cb is not None:
                cb(tuple(float(v) for v in normal), tuple(float(v) for v in origin))

        widget = self.plotter.add_plane_widget(on_end, normal=w["normal"], origin=w["origin"], bounds=w["bounds"],
                                               factor=1.0, color=self._tokens["accent"], implicit=True,
                                               normal_rotation=True, outline_translation=False,
                                               origin_translation=True, test_callback=False, interaction_event="end")
        try:  # 法線の矢印と原点の球を小さく・薄くし、外枠と面の縁は控えめな色にする(断面を隠さないように)
            widget.SetDiagonalRatio(0.08)
            widget.SetHandleSize(0.008)  # 画面の大きさに対する割合(既定のままだと断面に垂直に見たとき大きい)
            widget.GetNormalProperty().SetOpacity(0.35)  # 断面に垂直に見たとき、矢印の先で断面が隠れないように
            r, g, b = (int(self._tokens["text_muted"][i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            for prop in (widget.GetOutlineProperty(), widget.GetEdgesProperty()):
                prop.SetColor(r, g, b)
                prop.SetOpacity(0.5)
        except AttributeError:
            pass

    def set_view(self, view: str) -> None:
        if self._scene is None:
            return
        self._scene.view = view
        if self.backend == "interactive" and self.plotter is not None:
            set_view(self.plotter, view, self._scene.focus_bounds, self._scene.parallel, self._scene.plane_normal)
            self.plotter.render()
        elif self.backend == "static":
            self._render_static()

    def clear(self) -> None:
        self._scene = None
        self.legend.set_entries([])
        self.caption.setText("")
        if self.backend == "interactive" and self.plotter is not None:
            self.plotter.clear()
            self.plotter.set_background(self._tokens["bg"])
            self.plotter.render()
        elif self._image_label is not None:
            self._image_label.clear()

    def restyle(self, scheme: str) -> None:
        self._tokens = theme.tokens(scheme)
        if self._scene is not None:
            self.set_scene(self._scene, keep_camera=True)
        elif self.backend == "interactive" and self.plotter is not None:
            self.plotter.set_background(self._tokens["bg"])
            self.plotter.render()

    def screenshot(self, path: str | Path | None = None, size: tuple[int, int] | None = None) -> np.ndarray | None:
        """いま描いている場面の画像(RGB)。``path`` があれば PNG に書く。"""
        img: np.ndarray | None = None
        if self.backend == "interactive" and self.plotter is not None:
            img = self.plotter.screenshot(return_img=True)
        elif self.backend == "static" and self._scene is not None:
            w, h = size or (max(self.width(), 640), max(self._image_area_height(), 480))
            img = self._offscreen_image(w, h)
        if img is not None and path is not None:
            from PIL import Image

            Image.fromarray(np.asarray(img)).save(str(path))
        return img

    def close_view(self) -> None:
        if self.plotter is not None:
            try:
                self.plotter.close()
            except Exception:
                pass
            self.plotter = None

    # --- 静止画の方式 ---
    def _image_area_height(self) -> int:
        h = self.height() - self.legend.sizeHint().height() - (self.caption.sizeHint().height() if self.caption.isVisible() else 0)
        return max(h - 6, 100)

    def _offscreen_image(self, w: int, h: int) -> np.ndarray:
        import pyvista as pv

        p = pv.Plotter(off_screen=True, window_size=(int(w), int(h)))
        try:
            apply_scene(p, self._scene, self._tokens)
            img = p.screenshot(return_img=True)
        finally:
            p.close()
        return np.asarray(img)

    def _render_static(self) -> None:
        if self._image_label is None or self._scene is None:
            return
        w = max(self._image_label.width(), 320)
        h = max(self._image_label.height(), 240)
        try:
            img = self._offscreen_image(w, h)
        except Exception as exc:  # 描けないときは文字で知らせる
            self._image_label.setText(f"3D 表示を描けませんでした: {exc}")
            return
        self._static_image = img
        img = np.ascontiguousarray(img[:, :, :3])
        qimg = QImage(img.data, img.shape[1], img.shape[0], 3 * img.shape[1], QImage.Format.Format_RGB888).copy()
        self._image_label.setPixmap(QPixmap.fromImage(qimg))

    def resizeEvent(self, ev) -> None:  # noqa: N802
        super().resizeEvent(ev)
        if self.backend == "static" and self._scene is not None:
            self._timer.start()
