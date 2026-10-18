"""形状とメッシュの画像(画面を出さずに描く)。

- 三角形を numpy の z バッファで描く(縦横 2 倍の解像度で描いてから縮め、縁を滑らかにする)。
  OpenGL もウィンドウも使わないので、画面のない環境でも動く
- 面(またはパッチ)の番号が隣の画素と変わるところに稜線を、三角形の番号が変わるところに
  メッシュの線を引く
- 断面: 平面の法線の側を画素ごとに捨て、切り口は三角形を平面で切った線分を偶奇規則で塗る
- 文字・凡例・軸の向きは matplotlib(Agg)で重ね、PNG に書く
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# 視点の名前 → (方位角, 仰角) [deg]。方位角は +x から +y へ、仰角は xy 面から +z へ
VIEWS: dict[str, tuple[float, float]] = {
    "iso": (-60.0, 25.0),
    "iso_back": (120.0, 25.0),
    "front": (-90.0, 0.0),
    "back": (90.0, 0.0),
    "right": (0.0, 0.0),
    "left": (180.0, 0.0),
    "top": (-90.0, 90.0),
    "bottom": (-90.0, -90.0),
}

PALETTE = [
    (0.122, 0.467, 0.706), (1.000, 0.498, 0.055), (0.173, 0.627, 0.173), (0.839, 0.153, 0.157),
    (0.580, 0.404, 0.741), (0.549, 0.337, 0.294), (0.890, 0.467, 0.761), (0.737, 0.741, 0.133),
    (0.090, 0.745, 0.812), (0.400, 0.400, 0.950),
]
UNASSIGNED = (0.80, 0.80, 0.80)
CUT_COLOR = (0.95, 0.94, 0.86)
CAP_COLOR = (0.60, 0.62, 0.68)
FONTS = ["Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP", "DejaVu Sans"]
SUPERSAMPLE = 2
_CAP = -2


_FONT_CACHE: list[str] | None = None


def _available_fonts() -> list[str]:
    """FONTS のうち、この環境にあるもの(ないフォントを指定すると matplotlib が警告を出し続けるため)。"""
    global _FONT_CACHE
    if _FONT_CACHE is None:
        from matplotlib import font_manager

        names = {f.name for f in font_manager.fontManager.ttflist}
        _FONT_CACHE = [f for f in FONTS if f in names] or ["DejaVu Sans"]
    return _FONT_CACHE


def face_palette(i: int) -> tuple[float, float, float]:
    """面ごとに色を変えるときの色(20 色を繰り返す)。"""
    import matplotlib

    cmap = matplotlib.colormaps["tab20"]
    return tuple(float(c) for c in cmap(i % 20)[:3])


@dataclass
class Scene:
    tris: np.ndarray  # (T, 3, 3)
    color: np.ndarray  # (T, 3)
    face_id: np.ndarray  # (T,) 稜線と文字の位置に使う番号(0 以上)
    line_id: np.ndarray | None = None  # (T,) メッシュの線を引くときの番号
    labels: dict[int, str] = field(default_factory=dict)
    legend: list[tuple[str, tuple[float, float, float]]] = field(default_factory=list)
    clip: tuple[np.ndarray, np.ndarray] | None = None  # (点, 法線)。法線の側を捨てる
    cap: bool = False  # 断面を塗る(閉じた面のとき)
    focus: np.ndarray | None = None  # (6,) この箱が収まるように描く


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("0 ベクトルです")
    return v / n


def camera_from_angles(azimuth: float, elevation: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(右, 上, カメラへ向かう向き) の正規直交基底。"""
    az, el = math.radians(azimuth), math.radians(elevation)
    d = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    return camera_from_direction(d)


def camera_from_direction(direction, up=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    d = _unit(direction)
    if up is None:
        up = np.array([0.0, 1.0, 0.0]) if abs(d[2]) > 0.999 else np.array([0.0, 0.0, 1.0])
    r = np.cross(up, d)
    if np.linalg.norm(r) < 1e-9:
        r = np.cross([1.0, 0.0, 0.0], d)
    r = _unit(r)
    u = np.cross(d, r)
    return r, u, d


def section_camera(normal, tilt_az: float = 25.0, tilt_el: float = 20.0):
    """断面を斜めから見る視点(取り除いた側から、平面の法線を少し傾けた向き)。"""
    n = _unit(normal)
    r0, u0, _ = camera_from_direction(n)
    a, e = math.radians(tilt_az), math.radians(tilt_el)
    d = n * math.cos(e) * math.cos(a) + r0 * math.cos(e) * math.sin(a) + u0 * math.sin(e)
    return camera_from_direction(d)


def slice_triangles(tris: np.ndarray, p0: np.ndarray, n: np.ndarray) -> np.ndarray:
    """三角形を平面で切った線分 (S, 2, 3)。"""
    d = (tris - p0) @ n  # (T, 3)
    pos = d > 0
    cnt = pos.sum(axis=1)
    m = (cnt == 1) | (cnt == 2)
    if not m.any():
        return np.zeros((0, 2, 3))
    T, D, P = tris[m], d[m], pos[m]
    pts = []
    valid = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        ch = P[:, a] != P[:, b]
        da, db = D[:, a], D[:, b]
        with np.errstate(invalid="ignore", divide="ignore"):
            t = np.where(ch, da / (da - db), 0.0)
        pts.append(T[:, a] + (T[:, b] - T[:, a]) * t[:, None])
        valid.append(ch)
    pts = np.stack(pts, axis=1)  # (S, 3, 3)
    valid = np.stack(valid, axis=1)  # (S, 3)
    order = np.argsort(~valid, axis=1, kind="stable")[:, :2]
    return np.take_along_axis(pts, order[:, :, None], axis=1)


def _rasterize(X, Y, Z, C, W, H, zbuf, tbuf, budget: int = 600_000) -> None:
    """三角形 (T, 3) を z バッファ(平らな配列、大きいほど手前)に描く。C > 0 の部分は捨てる。"""
    T = X.shape[0]
    if T == 0:
        return
    x0 = np.floor(X.min(axis=1) - 0.5).astype(np.int64)
    x1 = np.ceil(X.max(axis=1) - 0.5).astype(np.int64)
    y0 = np.floor(Y.min(axis=1) - 0.5).astype(np.int64)
    y1 = np.ceil(Y.max(axis=1) - 0.5).astype(np.int64)
    x0 = np.maximum(x0, 0)
    y0 = np.maximum(y0, 0)
    x1 = np.minimum(x1, W - 1)
    y1 = np.minimum(y1, H - 1)
    den = (Y[:, 1] - Y[:, 2]) * (X[:, 0] - X[:, 2]) + (X[:, 2] - X[:, 1]) * (Y[:, 0] - Y[:, 2])
    valid = (x1 >= x0) & (y1 >= y0) & (np.abs(den) > 1e-12)
    if C is not None:
        valid &= C.min(axis=1) <= 0
    ids = np.nonzero(valid)[0]
    if ids.size == 0:
        return
    w = (x1 - x0 + 1)[ids]
    npx = w * (y1 - y0 + 1)[ids]
    cum = np.cumsum(npx)
    start = 0
    while start < ids.size:
        base = cum[start - 1] if start > 0 else 0
        end = int(np.searchsorted(cum, base + budget, side="right"))
        end = max(end, start + 1)
        sub = ids[start:end]
        n_sub = npx[start:end]
        w_sub = w[start:end]
        rep = np.repeat(np.arange(sub.size), n_sub)
        offs = np.arange(int(n_sub.sum())) - np.repeat(np.cumsum(n_sub) - n_sub, n_sub)
        t = sub[rep]
        px = x0[t] + offs % w_sub[rep]
        py = y0[t] + offs // w_sub[rep]
        sx = px + 0.5
        sy = py + 0.5
        Xa, Ya, d = X[t], Y[t], den[t]
        l0 = ((Ya[:, 1] - Ya[:, 2]) * (sx - Xa[:, 2]) + (Xa[:, 2] - Xa[:, 1]) * (sy - Ya[:, 2])) / d
        l1 = ((Ya[:, 2] - Ya[:, 0]) * (sx - Xa[:, 2]) + (Xa[:, 0] - Xa[:, 2]) * (sy - Ya[:, 2])) / d
        l2 = 1.0 - l0 - l1
        m = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
        if C is not None:
            Ca = C[t]
            m &= (l0 * Ca[:, 0] + l1 * Ca[:, 1] + l2 * Ca[:, 2]) <= 0
        if m.any():
            t, px, py, l0, l1, l2 = t[m], px[m], py[m], l0[m], l1[m], l2[m]
            Za = Z[t]
            z = l0 * Za[:, 0] + l1 * Za[:, 1] + l2 * Za[:, 2]
            pix = py * W + px
            order = np.lexsort((-z, pix))
            ps = pix[order]
            first = np.ones(ps.size, dtype=bool)
            first[1:] = ps[1:] != ps[:-1]
            sel = order[first]
            pp, pz, pt = pix[sel], z[sel], t[sel]
            better = pz > zbuf[pp]
            zbuf[pp[better]] = pz[better]
            tbuf[pp[better]] = pt[better]
        start = end


def _fill_even_odd(sx0, sy0, sx1, sy1, W, H) -> np.ndarray:
    """線分 (画素座標) の集まりを偶奇規則で塗った (H, W) の真偽配列。"""
    mask = np.zeros((H, W), dtype=bool)
    lo = np.minimum(sy0, sy1)
    hi = np.maximum(sy0, sy1)
    keep = hi > lo
    if not keep.any():
        return mask
    sx0, sy0, sx1, sy1, lo, hi = sx0[keep], sy0[keep], sx1[keep], sy1[keep], lo[keep], hi[keep]
    r0 = np.maximum(np.ceil(lo - 0.5).astype(np.int64), 0)
    r1 = np.minimum(np.ceil(hi - 0.5).astype(np.int64) - 1, H - 1)  # 半開区間 lo <= y < hi
    n = np.maximum(r1 - r0 + 1, 0)
    if n.sum() == 0:
        return mask
    seg = np.repeat(np.arange(n.size), n)
    row = r0[seg] + (np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n))
    yr = row + 0.5
    x = sx0[seg] + (yr - sy0[seg]) * (sx1[seg] - sx0[seg]) / (sy1[seg] - sy0[seg])
    order = np.lexsort((x, row))
    row, x = row[order], x[order]
    # 行ごとに 2 つずつ組にする(行の中での順番が偶数の点から次の点まで)
    first_of_row = np.ones(row.size, dtype=bool)
    first_of_row[1:] = row[1:] != row[:-1]
    idx_in_row = np.arange(row.size) - np.maximum.accumulate(np.where(first_of_row, np.arange(row.size), 0))
    a = (idx_in_row % 2 == 0)
    a[:-1] &= row[1:] == row[:-1]
    a[-1] = False
    ia = np.nonzero(a)[0]
    rr = row[ia]
    c0 = np.clip(np.ceil(x[ia] - 0.5).astype(np.int64), 0, W)
    c1 = np.clip(np.floor(x[ia + 1] - 0.5).astype(np.int64) + 1, 0, W)
    ok = c1 > c0
    diff = np.zeros((H, W + 1), dtype=np.int32)
    np.add.at(diff, (rr[ok], c0[ok]), 1)
    np.add.at(diff, (rr[ok], c1[ok]), -1)
    mask = np.cumsum(diff, axis=1)[:, :W] > 0
    return mask


def _edges(idbuf: np.ndarray) -> np.ndarray:
    e = np.zeros(idbuf.shape, dtype=bool)
    d0 = idbuf[1:, :] != idbuf[:-1, :]
    d1 = idbuf[:, 1:] != idbuf[:, :-1]
    e[1:, :] |= d0
    e[:-1, :] |= d0
    e[:, 1:] |= d1
    e[:, :-1] |= d1
    return e


def render_scene(
    scene: Scene,
    path: Path,
    *,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
    width: int = 900,
    height: int = 700,
    title: str = "",
    zoom: float = 1.0,
) -> dict:
    """シーンを PNG に描く。戻り値は描いた内容の要約。"""
    R, U, D = basis
    ss = SUPERSAMPLE
    W, H = int(width) * ss, int(height) * ss
    tris = np.asarray(scene.tris, dtype=float)
    V = tris.reshape(-1, 3)
    xs, ys, zs = V @ R, V @ U, V @ D
    C = None
    p0 = n0 = None
    if scene.clip is not None:
        p0, n0 = np.asarray(scene.clip[0], float), _unit(scene.clip[1])
        C = ((V - p0) @ n0).reshape(-1, 3)
    # 描く範囲
    if scene.focus is not None:
        b = np.asarray(scene.focus, float)
        corners = np.array([[b[i], b[j], b[k]] for i in (0, 3) for j in (1, 4) for k in (2, 5)])
        fx, fy = corners @ R, corners @ U
    else:
        keep = np.ones(V.shape[0], dtype=bool) if C is None else np.repeat(C.min(axis=1) <= 0, 3)
        if not keep.any():
            keep[:] = True
        fx, fy = xs[keep], ys[keep]
    if fx.size == 0:
        fx = fy = np.array([0.0, 1.0])
    xmin, xmax, ymin, ymax = float(fx.min()), float(fx.max()), float(fy.min()), float(fy.max())
    span_x = max(xmax - xmin, 1e-12)
    span_y = max(ymax - ymin, 1e-12)
    if span_x < 1e-9 * span_y:
        span_x = span_y
    if span_y < 1e-9 * span_x:
        span_y = span_x
    margin = 0.06
    scale = min(W * (1 - 2 * margin) / span_x, H * (1 - 2 * margin) / span_y) * max(zoom, 1e-3)
    xc, yc = 0.5 * (xmin + xmax), 0.5 * (ymin + ymax)
    X = ((xs - xc) * scale + W / 2).reshape(-1, 3)
    Y = (H / 2 - (ys - yc) * scale).reshape(-1, 3)
    Z = zs.reshape(-1, 3)

    zbuf = np.full(W * H, -np.inf)
    tbuf = np.full(W * H, -1, dtype=np.int64)
    _rasterize(X, Y, Z, C, W, H, zbuf, tbuf)

    cap_pixels = 0
    if scene.cap and scene.clip is not None:
        segs = slice_triangles(tris, p0, n0)
        nd = float(n0 @ D)
        if segs.shape[0] and abs(nd) > 1e-6:
            s0, s1 = segs[:, 0], segs[:, 1]
            sx0 = (s0 @ R - xc) * scale + W / 2
            sx1 = (s1 @ R - xc) * scale + W / 2
            sy0 = H / 2 - (s0 @ U - yc) * scale
            sy1 = H / 2 - (s1 @ U - yc) * scale
            mask = _fill_even_odd(sx0, sy0, sx1, sy1, W, H).ravel()
            if mask.any():
                pix = np.nonzero(mask)[0]
                px = pix % W + 0.5
                py = pix // W + 0.5
                xr = (px - W / 2) / scale + xc
                yu = (H / 2 - py) / scale + yc
                zd = (float(n0 @ p0) - xr * float(n0 @ R) - yu * float(n0 @ U)) / nd
                better = zd > zbuf[pix] - 1e-9 * max(span_x, span_y)
                zbuf[pix[better]] = zd[better]
                tbuf[pix[better]] = _CAP
                cap_pixels = int(better.sum())

    # 陰影
    t0 = tris[:, 1] - tris[:, 0]
    t1 = tris[:, 2] - tris[:, 0]
    nt = np.cross(t0, t1)
    nl = np.linalg.norm(nt, axis=1)
    nl[nl == 0] = 1.0
    nt /= nl[:, None]
    L = _unit(D + 0.35 * R + 0.45 * U)
    inten = 0.32 + 0.68 * np.abs(nt @ L)
    img = np.ones((W * H, 3))
    hit = tbuf >= 0
    th = tbuf[hit]
    img[hit] = np.asarray(scene.color, float)[th] * inten[th, None]
    if cap_pixels:
        capm = tbuf == _CAP
        img[capm] = np.asarray(CAP_COLOR) * (0.32 + 0.68 * abs(float(n0 @ L)))
    fid = np.full(W * H, -1, dtype=np.int64)
    fid[hit] = np.asarray(scene.face_id, np.int64)[th]
    fid[tbuf == _CAP] = _CAP
    fid2 = fid.reshape(H, W)
    img = img.reshape(H, W, 3)
    if scene.line_id is not None:
        lid = np.full(W * H, -1, dtype=np.int64)
        lid[hit] = np.asarray(scene.line_id, np.int64)[th]
        lid[tbuf == _CAP] = _CAP
        me = _edges(lid.reshape(H, W))
        img[me] *= 0.55
    fe = _edges(fid2)
    img[fe] *= 0.18

    # 縮める
    img = img.reshape(H // ss, ss, W // ss, ss, 3).mean(axis=(1, 3))
    img = np.clip(img, 0.0, 1.0)

    # 文字の位置(見えている画素の重心に最も近い、見えている画素)
    label_pos: list[tuple[float, float, str]] = []
    if scene.labels:
        flat = fid2.ravel()
        order = np.argsort(flat, kind="stable")
        sf = flat[order]
        keys = np.array(sorted(scene.labels))
        starts = np.searchsorted(sf, keys, side="left")
        ends = np.searchsorted(sf, keys, side="right")
        for k, a, b in zip(keys, starts, ends):
            if b <= a:
                continue
            pix = order[a:b]
            py = pix // W
            px = pix % W
            cy, cx = py.mean(), px.mean()
            j = int(np.argmin((py - cy) ** 2 + (px - cx) ** 2))
            label_pos.append(((px[j] + 0.5) / ss, (py[j] + 0.5) / ss, scene.labels[int(k)]))

    _compose_png(img, path, label_pos, scene.legend, title, R, U)
    visible = np.unique(fid[fid >= 0]).size
    return {
        "width": int(width),
        "height": int(height),
        "triangles": int(tris.shape[0]),
        "visible_groups": int(visible),
        "section_pixels": cap_pixels,
    }


def _compose_png(img, path: Path, labels, legend, title: str, R, U) -> None:
    import matplotlib
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch
    import matplotlib.patheffects as pe

    h, w = img.shape[:2]
    with matplotlib.rc_context({"font.family": _available_fonts(), "font.size": 9}):
        fig = Figure(figsize=(w / 100.0, h / 100.0), dpi=100)
        FigureCanvasAgg(fig)
        ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
        ax.imshow(img, interpolation="nearest")
        ax.set_xlim(-0.5, w - 0.5)
        ax.set_ylim(h - 0.5, -0.5)
        ax.set_axis_off()
        stroke = [pe.withStroke(linewidth=2.5, foreground="white")]
        for x, y, text in labels:
            ax.text(x, y, text, ha="center", va="center", fontsize=8, color="black", path_effects=stroke, zorder=5)
        if legend:
            handles = [Patch(facecolor=c, edgecolor="0.3", label=t) for t, c in legend]
            ax.legend(handles=handles, loc="upper right", fontsize=8, framealpha=0.85)
        if title:
            ax.text(8, 14, title, ha="left", va="center", fontsize=9, color="0.15", path_effects=stroke)
        # 軸の向き(左下)
        ox, oy, L = 42.0, h - 42.0, 28.0
        for e, name, col in ((np.array([1.0, 0, 0]), "x", "#d62728"), (np.array([0, 1.0, 0]), "y", "#2ca02c"),
                             (np.array([0, 0, 1.0]), "z", "#1f77b4")):
            dx, dy = float(e @ R), -float(e @ U)
            if dx * dx + dy * dy < 1e-6:
                ax.plot([ox], [oy], marker="o", color=col, markersize=3)
            else:
                ax.annotate("", xy=(ox + L * dx, oy + L * dy), xytext=(ox, oy),
                            arrowprops=dict(arrowstyle="-|>", color=col, lw=1.4))
            ax.text(ox + (L + 9) * dx, oy + (L + 9) * dy, name, color=col, ha="center", va="center", fontsize=8,
                    path_effects=stroke)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(path), dpi=100)
