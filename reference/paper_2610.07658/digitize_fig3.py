"""arXiv:2610.07658v1 Fig.3(円管の中心線上の圧力・軸方向速度)のデジタイズ。

使い方(リポジトリのルートから):
    uv run --project python python reference/paper_2610.07658/digitize_fig3.py [出力先]
出力先を省略するとこのファイルのあるディレクトリ(figures/ の原画像を読み、CSV と check/ を書く)。

処理の流れ(詳細は README.md):
  1. 軸の目盛り(枠の内側の短い線)を検出し、画素 → データの 1 次式を最小二乗で決める。
  2. CIS の記号(赤い四角・緑の下向き三角・青い菱形)は色で、DSMC の黒丸は形で抽出する。
     凡例の記号を型(テンプレート)にした相関で位置を求める。ほかの色や黒線に隠れた画素は
     評価から外す。記号の基準点は型の輪郭の重心(三角形も重心。凡例の行間隔で確認)。
  3. GSIS-PBC の 3 本の黒線は、記号・凡例・枠を除いた黒画素を、傾きが緩い所は列ごと(縦)、
     急な所は行ごと(横)の連続区間に分け、濃さで重み付けした中心を線の位置とする。
     どの線に属するかは、拡大画像から読んだ案内点(guides)を出発点に、抽出点の局所回帰で
     案内曲線を更新しながら決める。2 本の線が重なる所・記号に一部が隠れた所・破線の端は捨てる。
  4. 系列ごとの CSV と、CSV を読み戻して原画像に重ねた確認用 PNG(check/)を出力する。
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage, signal
from scipy.interpolate import PchipInterpolator

HERE = Path(__file__).resolve().parent
DEBUG = False   # True にすると線の抽出に使った黒画素の画像も check/ に出す


# ---------------------------------------------------------------------------
# 図ごとの設定
# ---------------------------------------------------------------------------
@dataclass
class FigConfig:
    key: str                      # 出力名に使う("pressure" / "velocity")
    image: str                    # figures/ の下の画像ファイル名
    value_name: str               # CSV の値の列名
    y_major_values: list[float]   # 検出した主目盛り(上から順)の値
    x_major_values: list[float]   # 検出した主目盛り(左から順)の値
    y_minor_step: float
    x_minor_step: float
    legend_box: tuple[int, int, int, int]          # (x0, y0, x1, y1) 画素。線の抽出から除く
    legend_symbols: dict[str, tuple[int, int]]     # 凡例の記号のおおよその中心(画素)
    guides: dict[str, list[tuple[float, float]]]   # 線の案内点(データ座標)。どの線の画素かの判定にだけ使う
    zooms: list[tuple[str, tuple[int, int, int, int], int]] = field(default_factory=list)
    legend_loc: tuple = ("upper right", None)   # 確認図の凡例の位置(loc, bbox_to_anchor)


CONFIGS = [
    FigConfig(
        key="pressure",
        image="Tube_He_P_delta1_delta10_delta100_gsis_cis_cmp.png",
        value_name="P",
        y_major_values=[0.8, 0.6, 0.4, 0.2],
        x_major_values=[0, 2, 4, 6, 8, 10],
        y_minor_step=0.05,
        x_minor_step=0.5,
        legend_box=(250, 765, 840, 1100),
        legend_symbols={"red": (348, 934), "green": (348, 980), "blue": (348, 1027), "black": (348, 1073)},
        guides={
            # 入口付近と交差部は拡大画像で読んだ値。ほかは第 1 段の抽出結果から
            "gsis_delta1": [(0.003, 0.918), (0.033, 0.915), (0.185, 0.901), (0.286, 0.893), (0.387, 0.882),
                            (0.570, 0.866), (0.701, 0.856), (0.823, 0.846), (0.955, 0.836), (1.066, 0.826),
                            (1.410, 0.799), (2.008, 0.754),
                            (2.5, 0.712), (3, 0.672), (3.5, 0.633), (4, 0.593), (4.5, 0.552), (5, 0.512), (6, 0.431),
                            (7, 0.352), (8, 0.265), (9, 0.172), (9.5, 0.119), (9.8, 0.084), (10, 0.064)],
            "gsis_delta10": [(0, 0.952), (0.1, 0.946), (0.2, 0.939), (0.3, 0.934), (0.5, 0.919), (1, 0.887),
                             (1.25, 0.872), (1.5, 0.857), (2, 0.823), (2.5, 0.791), (3, 0.758), (4, 0.689),
                             (4.5, 0.652), (5, 0.615), (6, 0.535), (7, 0.449), (8, 0.351), (9, 0.242), (9.5, 0.184),
                             (9.8, 0.130), (10, 0.107)],
            "gsis_delta100": [(0.003, 0.921), (0.190, 0.8945), (0.251, 0.8876), (0.322, 0.8796), (0.387, 0.8712),
                              (0.428, 0.8658), (0.495, 0.8584), (0.575, 0.8519), (0.661, 0.8450), (0.818, 0.8321),
                              (1.299, 0.811), (1.370, 0.8095), (1.476, 0.804), (1.679, 0.794), (1.917, 0.7865),
                              (2.119, 0.7765), (2.5, 0.759), (3, 0.741), (4, 0.696), (4.5, 0.678), (5, 0.652),
                              (6, 0.604), (7, 0.544), (8, 0.478), (9, 0.387), (9.8, 0.272), (10, 0.241)],
        },
        zooms=[("inlet", (245, 180, 485, 420), 4), ("cross", (530, 360, 770, 540), 4),
               ("middle", (740, 470, 1000, 760), 3), ("outlet", (1060, 740, 1270, 1115), 3)],
    ),
    FigConfig(
        key="velocity",
        image="Tube_He_ux_delta1_delta10_delta100_gsis_cis_cmp.png",
        value_name="Ux",
        y_major_values=[1.0, 0.8, 0.6, 0.4, 0.2],
        x_major_values=[0, 2, 4, 6, 8, 10],
        y_minor_step=0.05,
        x_minor_step=0.5,
        legend_box=(280, 180, 860, 512),
        legend_symbols={"red": (375, 348), "green": (375, 394), "blue": (375, 440), "black": (375, 487)},
        guides={
            # 第 1 段の抽出結果から
            "gsis_delta1": [(0, 0.074), (0.5, 0.083), (1, 0.087), (2, 0.095), (3, 0.105), (4, 0.118), (5, 0.134),
                            (6, 0.157), (7, 0.191), (8, 0.245), (9, 0.354), (9.5, 0.468), (9.8, 0.568), (10, 0.645)],
            "gsis_delta10": [(0, 0.145), (0.2, 0.158), (0.5, 0.169), (1, 0.179), (2, 0.190), (3, 0.204), (4, 0.221),
                             (5, 0.241), (6, 0.272), (7, 0.311), (8, 0.373), (8.6, 0.44), (9.2, 0.53), (9.5, 0.598),
                             (9.8, 0.687), (10, 0.755)],
            "gsis_delta100": [(0.02, 0.287), (0.1, 0.302), (0.2, 0.337), (0.3, 0.347), (0.5, 0.388), (0.75, 0.414),
                              (1, 0.436), (1.5, 0.457), (2, 0.481), (3, 0.522), (4, 0.559), (5, 0.597), (6, 0.636),
                              (7, 0.682), (8, 0.739), (9, 0.828), (9.5, 0.898), (9.8, 0.953), (10, 0.997)],
        },
        zooms=[("inlet", (250, 760, 470, 1115), 3), ("middle", (600, 540, 1000, 1080), 2),
               ("outlet", (1090, 170, 1270, 700), 3)],
        legend_loc=("upper left", (0.62, 0.875)),
    ),
]

MARKER_SERIES = {"red": "cis_delta1", "green": "cis_delta10", "blue": "cis_delta100", "black": "dsmc_delta100"}


# ---------------------------------------------------------------------------
# 画像の読み込みと色の重み
# ---------------------------------------------------------------------------
def load_layers(path: Path):
    im = np.asarray(Image.open(path).convert("RGB")).astype(float)
    r, g, b = im[:, :, 0], im[:, :, 1], im[:, :, 2]
    mx, mn = im.max(axis=2), im.min(axis=2)
    isgray = (mx - mn) < 12
    ink = np.where(isgray, (255.0 - im.mean(axis=2)) / 255.0, 0.0)   # 黒の濃さ 0..1(無彩色の画素のみ)
    w = {
        "red": np.clip((r - np.maximum(g, b)) / 210.0, 0, 1),
        "green": np.clip((g - np.maximum(r, b)) / 210.0, 0, 1),
        "blue": np.clip((b - np.maximum(r, g)) / 210.0, 0, 1),
        "black": ink,
    }
    return im.astype(np.uint8), w


# ---------------------------------------------------------------------------
# 軸の較正
# ---------------------------------------------------------------------------
def find_frame(ink):
    """太い枠線(5 px)の中心の行・列を返す"""
    dark = ink > 0.5
    rows = np.nonzero(dark.sum(axis=1) > 0.6 * ink.shape[1])[0]
    cols = np.nonzero(dark.sum(axis=0) > 0.6 * ink.shape[0])[0]
    top = rows[rows < ink.shape[0] / 2]
    bot = rows[rows > ink.shape[0] / 2]
    left = cols[cols < ink.shape[1] / 2]
    right = cols[cols > ink.shape[1] / 2]
    return dict(top=top.mean(), bottom=bot.mean(), left=left.mean(), right=right.mean(),
                top_in=top.max(), bottom_in=bot.min(), left_in=left.max(), right_in=right.min())


def tick_centers(profile, offset, thr=0.05):
    """1 次元のインク量から目盛り線の重み付き中心を返す"""
    on = profile > thr
    lab, n = ndimage.label(on)
    out = []
    for i in range(1, n + 1):
        idx = np.nonzero(lab == i)[0]
        lo, hi = max(idx[0] - 1, 0), min(idx[-1] + 1, len(profile) - 1)
        seg = np.arange(lo, hi + 1)
        w = profile[seg]
        out.append(((seg * w).sum() / w.sum() + offset, idx[-1] - idx[0] + 1))
    return out


def calibrate(ink, frame, cfg: FigConfig):
    # y 軸: 左枠の内側 4..14 px の列、x 軸: 下枠の内側 4..14 px の行
    li, bi = int(frame["left_in"]), int(frame["bottom_in"])
    ti, ri = int(frame["top_in"]), int(frame["right_in"])
    y0, y1 = ti + 3, bi - 3
    prof_y = ink[y0:y1, li + 3:li + 13].mean(axis=1)
    yt = tick_centers(prof_y, y0)
    # 主目盛りは長い(枠から 20 px 以上): li+18 列でもインクがあるもの
    long_y = ink[y0:y1, li + 17:li + 19].mean(axis=1)
    x0, x1 = li + 3, ri - 3
    prof_x = ink[bi - 13:bi - 3, x0:x1].mean(axis=0)
    xt = tick_centers(prof_x, x0)
    long_x = ink[bi - 19:bi - 17, x0:x1].mean(axis=0)

    def split(ticks, longprof, off):
        major, minor = [], []
        for c, length in ticks:
            if length > 6:
                # 目盛り線は太さ 1〜5 px。長いものは隅にある別の軸の目盛り(縦線)なので除く
                continue
            i = int(round(c)) - off
            (major if longprof[max(i - 1, 0):i + 2].max() > 0.3 else minor).append(c)
        return major, minor

    ymaj, ymin = split(yt, long_y, y0)
    xmaj, xmin = split(xt, long_x, x0)
    assert len(ymaj) == len(cfg.y_major_values), (ymaj, cfg.y_major_values)
    assert len(xmaj) == len(cfg.x_major_values), (xmaj, cfg.x_major_values)

    def fit(maj, vals, minor, step):
        a = np.polyfit(maj, vals, 1)              # 主目盛りだけの初期近似
        pts = list(zip(maj, vals))
        for c in minor:
            v = np.polyval(a, c)
            pts.append((c, round(v / step) * step))
        pts = np.array(sorted(pts))
        coef = np.polyfit(pts[:, 0], pts[:, 1], 1)
        resid_px = (pts[:, 1] - np.polyval(coef, pts[:, 0])) / coef[0]
        return coef, pts, resid_px

    cy, pty, ry = fit(ymaj, cfg.y_major_values, ymin, cfg.y_minor_step)
    cx, ptx, rx = fit(xmaj, cfg.x_major_values, xmin, cfg.x_minor_step)
    return dict(cx=cx, cy=cy, ticks_x=ptx, ticks_y=pty, resid_x_px=rx, resid_y_px=ry)


def px2data(cal, x, y):
    return np.polyval(cal["cx"], x), np.polyval(cal["cy"], y)


def data2px(cal, X, V):
    return (np.asarray(X) - cal["cx"][1]) / cal["cx"][0], (np.asarray(V) - cal["cy"][1]) / cal["cy"][0]


# ---------------------------------------------------------------------------
# 記号の抽出(凡例の記号を型にした相関)
# ---------------------------------------------------------------------------
HALF = 20  # 型の半幅(41x41)


def make_template(w, approx):
    ax, ay = approx
    patch = w[ay - HALF:ay + HALF + 1, ax - HALF:ax + HALF + 1].copy()
    patch[patch < 0.1] = 0
    # 連結成分のうち中心に最も近いものだけ残す(文字などの混入防止)
    lab, n = ndimage.label(patch > 0)
    if n > 1:
        best, bd = None, 1e9
        for i in range(1, n + 1):
            ys, xs = np.nonzero(lab == i)
            d = np.hypot(xs.mean() - HALF, ys.mean() - HALF)
            if d < bd:
                best, bd = i, d
        patch[lab != best] = 0
    ys, xs = np.nonzero(patch)
    ww = patch[ys, xs]
    cx, cy = (xs * ww).sum() / ww.sum(), (ys * ww).sum() / ww.sum()   # 輪郭の重み付き重心 = 基準点
    # 基準点が型の中心に来るよう整数分だけずらし、端数は anchor_off に残す
    sx, sy = int(round(cx - HALF)), int(round(cy - HALF))
    patch = w[ay + sy - HALF:ay + sy + HALF + 1, ax + sx - HALF:ax + sx + HALF + 1].copy()
    patch[patch < 0.1] = 0
    lab, n = ndimage.label(patch > 0)
    if n > 1:
        sizes = ndimage.sum(np.ones_like(patch), lab, range(1, n + 1))
        keep = 1 + int(np.argmax(sizes))
        patch[lab != keep] = 0
    ys, xs = np.nonzero(patch)
    ww = patch[ys, xs]
    cx, cy = (xs * ww).sum() / ww.sum(), (ys * ww).sum() / ww.sum()
    bbox = (xs.min(), ys.min(), xs.max(), ys.max())
    origin = (ax + sx - HALF, ay + sy - HALF)   # 型の左上の画素の位置(原画像)
    return dict(T=patch, anchor_off=(cx - HALF, cy - HALF), bbox=bbox, origin=origin,
                legend_anchor=(origin[0] + cx, origin[1] + cy),
                legend_bbox_center=(origin[0] + (bbox[0] + bbox[2]) / 2, origin[1] + (bbox[1] + bbox[3]) / 2))


def corr(img, T):
    return signal.fftconvolve(img, T[::-1, ::-1], mode="same")


def ring_coverage(w, occl, cx, cy, T, anchor_off, nsec=16):
    """型の画素を中心からの角度で nsec 区画に分け、各区画でインクがある割合を返す"""
    ys, xs = np.nonzero(T > 0.3)
    ys = ys - HALF - anchor_off[1]
    xs = xs - HALF - anchor_off[0]
    ang = np.arctan2(ys, xs)
    sec = ((ang + np.pi) / (2 * np.pi) * nsec).astype(int) % nsec
    px = np.round(cx + xs).astype(int)
    py = np.round(cy + ys).astype(int)
    vals = w[py, px]
    occ = occl[py, px]
    inked, hidden = 0, 0
    for s in range(nsec):
        m = sec == s
        if occ[m].mean() > 0.5:
            hidden += 1
        elif vals[m].mean() > 0.35:
            inked += 1
    return inked, hidden


def detect_markers(w, color, tmpl, region, thr=0.55, min_vis=0.35):
    T = tmpl["T"]
    target = w[color]
    others = sum(w[c] for c in w if c != color)
    occl = np.clip(others, 0, 1)
    num = corr(target, T)
    den = corr(1.0 - occl, T)
    tot = T.sum()
    score = num / np.maximum(den, 1e-6)
    vis = den / tot
    x0, y0, x1, y1 = region
    valid = np.zeros_like(score, dtype=bool)
    valid[y0:y1, x0:x1] = True
    valid &= vis > min_vis
    sc = np.where(valid, score, 0)
    peaks = (sc == ndimage.maximum_filter(sc, size=13)) & (sc > thr)
    out = []
    for py, px in zip(*np.nonzero(peaks)):
        # 3x3 の 2 次近似で小数画素の位置を求める
        blk = sc[py - 1:py + 2, px - 1:px + 2]
        dx = 0.5 * (blk[1, 0] - blk[1, 2]) / (blk[1, 0] - 2 * blk[1, 1] + blk[1, 2] + 1e-12)
        dy = 0.5 * (blk[0, 1] - blk[2, 1]) / (blk[0, 1] - 2 * blk[1, 1] + blk[2, 1] + 1e-12)
        dx, dy = float(np.clip(dx, -1, 1)), float(np.clip(dy, -1, 1))
        cx = px + dx + tmpl["anchor_off"][0]
        cy = py + dy + tmpl["anchor_off"][1]
        rec = dict(x=cx, y=cy, score=float(sc[py, px]), vis=float(vis[py, px]))
        if color == "black":
            inked, hidden = ring_coverage(target, occl, px + tmpl["anchor_off"][0], py + tmpl["anchor_off"][1],
                                          T, tmpl["anchor_off"])
            rec["sectors_inked"], rec["sectors_hidden"] = inked, hidden
            if not (inked >= 0.8 * (16 - hidden) and (16 - hidden) >= 8):
                continue
        out.append(rec)
    out.sort(key=lambda r: r["x"])
    return out


# ---------------------------------------------------------------------------
# 線の抽出
# ---------------------------------------------------------------------------
LINE_T = 5.0          # 線の太さ(画素)。凡例の線見本の 1 列あたりのインク量から
OUTLIER_PX = 1.5      # 近くの抽出点の局所回帰からこれ以上離れた点は捨てる
STEEP = 1.0          # 案内曲線の傾き(画素/画素)がこれより急な所は行ごとに走査する


def column_runs(prof, occ_col, thr=0.3):
    """1 列のインク量から縦方向の連続区間を求める。
    戻り値: (重み付き中心, インク量, 上端, 下端, 遮るものに接しているか) のリスト。
    区間のすぐ上下(2 画素以内)に遮るもの(色付きの記号・消した輪・除外領域)があれば
    「接している」とする。実際に隠れているかは、区間の長さと合わせて assign() で判定する。"""
    on = prof > thr
    lab, n = ndimage.label(on)
    runs = []
    H = len(prof)
    for i in range(1, n + 1):
        idx = np.nonzero(lab == i)[0]
        lo, hi = idx[0], idx[-1]
        seg = np.arange(max(lo - 1, 0), min(hi + 1, H - 1) + 1)
        w = prof[seg]
        mass = w.sum()
        if mass < 1.5:
            continue
        blocked = bool(occ_col[max(lo - 2, 0):lo].any() or occ_col[hi + 1:hi + 3].any())
        runs.append(((seg * w).sum() / mass, mass, lo, hi, blocked))
    return runs


def local_fit(xs, ys, xq, half=20.0, min_pts=5):
    """tricube 重みの局所回帰(点が多ければ 2 次、少なければ 1 次)。近くに点がない所は nan。"""
    out = np.full(len(xq), np.nan)
    for i, x in enumerate(xq):
        dist = np.abs(xs - x)
        m = dist < half
        if m.sum() < min_pts:
            continue
        if x < xs[m].min() - 3 or x > xs[m].max() + 3:
            continue   # 外挿はしない
        wts = (1 - (dist[m] / half) ** 3) ** 3
        span = xs[m].max() - xs[m].min()
        deg = 2 if (m.sum() >= 12 and span >= 15) else (1 if span >= 3 else 0)
        c = np.polyfit(xs[m] - x, ys[m], deg, w=np.sqrt(wts))
        out[i] = c[-1]
    return out


def smooth_guide(xs, ys, cols, base):
    """割り当てた点の局所回帰で案内曲線を更新する。
    点がない区間(両端の外側を含む)は、もとの案内曲線との差を線形補間・一定外挿して埋める。"""
    if len(xs) < 8:
        return base
    g = local_fit(xs, ys, cols)
    diff = g - base
    idx = np.arange(len(cols))
    good = ~np.isnan(diff)
    diff = np.interp(idx, idx[good], diff[good])
    return base + diff


def robust_inliers(xs, ys, tol=OUTLIER_PX, n_iter=3):
    """近くの点の局所回帰から tol 画素以上離れた点を外れ値とする(繰り返し)。
    近くに点が少なく判定できない点は残す。"""
    keep = np.ones(len(xs), dtype=bool)
    for _ in range(n_iter):
        fit = local_fit(xs[keep], ys[keep], xs, half=12.0, min_pts=5)
        res = ys - fit
        new = np.isnan(fit) | (np.abs(res) <= tol)
        if (new == keep).all():
            break
        keep = new
    return keep


def trim_segment_ends(pos, slope_at, steep):
    """破線の 1 片(連続する列、または連続する行)の両端は、線の端の切り口のせいで
    区間の中心がずれるので捨てる。捨てる数は切り口が走査方向にかかる幅
    (列走査: 線の太さ/2 × sinθ、行走査: 線の太さ/2 × cosθ)から決める。
    短い片(点線の点など)は中央の 1 つだけ残す。pos は整数の列番号または行番号。"""
    keep = np.zeros(len(pos), dtype=bool)
    if len(pos) == 0:
        return keep
    starts = [0] + [i for i in range(1, len(pos)) if pos[i] != pos[i - 1] + 1]
    ends = starts[1:] + [len(pos)]
    for a, b in zip(starts, ends):
        n = b - a
        s = abs(slope_at((a + b - 1) // 2))
        f = (1.0 / np.sqrt(1 + s * s)) if steep else (s / np.sqrt(1 + s * s))
        k = max(int(np.ceil(0.5 * LINE_T * f - 0.25)), 0)
        if k == 0:
            keep[a:b] = True
        elif n > 2 * k + 1:
            keep[a + k:b - k] = True
        else:
            keep[a + (n - 1) // 2] = True
    return keep


def intervals(mask):
    """bool 配列の True の連続区間 [(i0, i1)](両端を含む)"""
    out = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def extract_lines(ink, occ_color, cal, cfg: FigConfig, circles, circle_tmpl, keep_mask):
    H, W = ink.shape
    work = ink * keep_mask
    removed = np.zeros((H, W), dtype=bool)
    # 黒丸の輪を消す(輪の内側の線は残す)
    yy, xx = np.mgrid[0:H, 0:W]
    bx0, by0, bx1, by1 = circle_tmpl["bbox"]
    r_out = max(bx1 - bx0, by1 - by0) / 2 + 1.5
    r_in = r_out - 5.5
    for c in circles:
        sl = (slice(int(c["y"] - 16), int(c["y"] + 17)), slice(int(c["x"] - 16), int(c["x"] + 17)))
        d = np.hypot(xx[sl] - c["x"], yy[sl] - c["y"])
        ring = (d >= r_in) & (d <= r_out)
        work[sl][ring] = 0
        removed[sl][ring] = True
    occ = occ_color | removed | (keep_mask == 0)

    xpx0, _ = data2px(cal, 0.0, 0.0)
    xpx1, _ = data2px(cal, 10.0, 0.0)
    cols = np.arange(int(np.floor(xpx0)) - 2, int(np.ceil(xpx1)) + 3)
    col_runs = {int(x): column_runs(work[:, x], occ[:, x]) for x in cols}
    row_cache = {}

    def row_runs(y):
        if y not in row_cache:
            row_cache[y] = column_runs(work[y, :], occ[y, :])
        return row_cache[y]

    names = list(cfg.guides)
    guide = {}
    for s in names:
        g = np.array(cfg.guides[s])
        gx, gy = data2px(cal, g[:, 0], g[:, 1])
        guide[s] = PchipInterpolator(gx, gy, extrapolate=True)(cols)

    def assign(tol):
        slopes = {s: np.gradient(guide[s]) for s in names}

        def gy_at(t, x):
            return float(np.interp(x, cols, guide[t]))

        def sl_at(t, x):
            return float(np.interp(x, cols, slopes[t]))

        def hv(t, x):
            """線 t の 1 列あたりの縦の幅"""
            return LINE_T * np.sqrt(1 + sl_at(t, x) ** 2)

        def hh(t, x):
            """線 t の 1 行あたりの横の幅"""
            st = max(abs(sl_at(t, x)), 1e-3)
            return LINE_T * np.sqrt(1 + st * st) / st

        def dh(t, x, y):
            """点 (x, y) から線 t までの横方向の距離(線 t を局所的に直線とみなす)"""
            return abs(y - gy_at(t, x)) / max(abs(sl_at(t, x)), 1e-3)

        pts = {s: [] for s in names}       # (x, y, 0=列走査 / 1=行走査)
        skipped = {s: [] for s in names}

        def judge(s, run, full, mode, x, yg, xg=None, y=None):
            others = [t for t in names if t != s]
            if mode == 0:
                # 列走査: 同じ列で他の線の縦の幅と重なるか
                if any(abs(gy_at(t, x) - yg) < 0.5 * (hv(s, x) + hv(t, x)) - 0.5 for t in others):
                    return "close"
                if any(abs(run[0] - gy_at(t, x)) < abs(run[0] - yg) for t in others):
                    return "nearer_other"
            else:
                # 行走査: 同じ行で他の線の横の幅と重なるか
                if any(dh(t, xg, y) < 0.5 * (hh(s, xg) + hh(t, xg)) - 0.5 for t in others):
                    return "close"
                if any(dh(t, run[0], y) < abs(run[0] - xg) for t in others):
                    return "nearer_other"
            n = run[3] - run[2] + 1
            if n > full + 3:
                return "merged"           # 長すぎる(線同士・線と記号が重なった塊)
            if run[4] and n < full - 1.5:
                return "blocked"          # 遮るものに接していて、しかも短い(一部が隠れている)
            return None

        for s in names:
            steep = np.abs(slopes[s]) > STEEP
            # 列走査(傾きが緩い所)
            for k, x in enumerate(cols):
                if steep[k]:
                    continue
                yg = guide[s][k]
                cands = [r for r in col_runs[int(x)] if abs(r[0] - yg) < tol]
                if not cands:
                    continue
                r = min(cands, key=lambda r: abs(r[0] - yg))
                why = judge(s, r, hv(s, float(x)), 0, float(x), yg)
                if why:
                    skipped[s].append((float(x), why))
                else:
                    pts[s].append((float(x), r[0], 0))
            # 行走査(傾きが急な所)
            for k0, k1 in intervals(steep):
                xs_ = cols[k0:k1 + 1].astype(float)
                gs = guide[s][k0:k1 + 1]
                o = np.argsort(gs)
                for y in range(int(np.ceil(gs.min())), int(np.floor(gs.max())) + 1):
                    xg = float(np.interp(y, gs[o], xs_[o]))
                    cands = [r for r in row_runs(y) if abs(r[0] - xg) < tol]
                    if not cands:
                        continue
                    r = min(cands, key=lambda r: abs(r[0] - xg))
                    why = judge(s, r, hh(s, xg), 1, xg, float(y), xg=xg, y=float(y))
                    if why:
                        skipped[s].append((xg, why))
                    else:
                        pts[s].append((r[0], float(y), 1))
        return pts, skipped

    for tol in (8.0, 5.0, 4.0, 4.0):
        pts, skipped = assign(tol)
        for s in names:
            a = np.array(pts[s])
            if len(a):
                o = np.argsort(a[:, 0])
                guide[s] = smooth_guide(a[o, 0], a[o, 1], cols.astype(float), guide[s])

    final = {}
    for s in names:
        a = np.array(pts[s])
        slope = np.gradient(guide[s])
        keep = np.zeros(len(a), dtype=bool)
        n_trim = 0
        n_out = 0
        for kind in (0, 1):
            idx = np.nonzero(a[:, 2] == kind)[0]
            if len(idx) == 0:
                continue
            if kind == 0:   # 列走査: 列番号の順
                pos = a[idx, 0]
                val = a[idx, 1]
            else:           # 行走査: 行番号の順
                pos = a[idx, 1]
                val = a[idx, 0]
            o = np.argsort(pos)
            idx, pos, val = idx[o], pos[o], val[o]
            xs_for_slope = a[idx, 0]

            def slope_at(i, xs_for_slope=xs_for_slope):
                return float(np.interp(xs_for_slope[i], cols, slope))

            ke = trim_segment_ends(pos.astype(int), slope_at, steep=(kind == 1))
            kr = np.zeros(len(idx), dtype=bool)
            if ke.any():
                kr[ke] = robust_inliers(pos[ke], val[ke])
            keep[idx] = ke & kr
            n_trim += int((~ke).sum())
            n_out += int((ke & ~kr).sum())
        o = np.argsort(a[keep, 0])
        final[s] = dict(px=a[keep][o][:, :2], kind=a[keep][o][:, 2].astype(int), dropped=a[~keep][:, :2],
                        n_trim=n_trim, n_outlier=n_out, skipped=skipped[s], guide=guide[s], cols=cols)
    return final, work


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def write_csv(path: Path, header, rows, fmt=("%.4f", "%.4f")):
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f, lineterminator="\n")
        wr.writerow(header)
        for r in rows:
            wr.writerow([fmt[i] % v for i, v in enumerate(r)])


# 確認図の色(原図の赤・緑・青・黒と区別できる色。dataviz の検証スクリプトで確認済み)
SERIES_STYLE = {
    "gsis_delta1": dict(color="#c2189b", label="GSIS-PBC δ=1(線)"),
    "gsis_delta10": dict(color="#0a84c8", label="GSIS-PBC δ=10(線)"),
    "gsis_delta100": dict(color="#b86200", label="GSIS-PBC δ=100(線)"),
    "cis_delta1": dict(color="#8b0000", label="CIS δ=1(□ の中心)"),
    "cis_delta10": dict(color="#005a00", label="CIS δ=10(▽ の重心)"),
    "cis_delta100": dict(color="#1a1a9c", label="CIS δ=100(◇ の中心)"),
    "dsmc_delta100": dict(color="#000000", label="DSMC δ=100(○ の中心)"),
}
LINE_SERIES = ["gsis_delta1", "gsis_delta10", "gsis_delta100"]
MARKER_ORDER = ["cis_delta1", "cis_delta10", "cis_delta100", "dsmc_delta100"]


def read_csv(path: Path):
    a = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    return a[:, 0], a[:, 1]


def draw_overlay(im, cal, cfg: FigConfig, out_dir: Path, path: Path, box=None, scale=1, legend_loc=None):
    """CSV に書いた値をデータ座標で描き、較正式で位置を合わせた原画像(薄くしたもの)に重ねる。
    画素→データの較正と抽出の両方を確かめるための図。box=(x0,y0,x1,y1) は画素で指定する切り出し範囲。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for name in ("Yu Gothic", "Meiryo", "MS Gothic"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    H, W = im.shape[:2]
    if box is None:
        box = (0, 0, W, H)
    x0, y0, x1, y1 = box
    faded = (im.astype(float) * 0.45 + 255 * 0.55).astype(np.uint8)
    # 画素 i は [i-0.5, i+0.5] を占める
    ext = [np.polyval(cal["cx"], -0.5), np.polyval(cal["cx"], W - 0.5),
           np.polyval(cal["cy"], H - 0.5), np.polyval(cal["cy"], -0.5)]
    xl = (np.polyval(cal["cx"], x0 - 0.5), np.polyval(cal["cx"], x1 - 0.5))
    yl = (np.polyval(cal["cy"], y1 - 0.5), np.polyval(cal["cy"], y0 - 0.5))
    fig = plt.figure(figsize=((x1 - x0) * scale / 100, (y1 - y0) * scale / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(faded, extent=ext, aspect="auto", interpolation="nearest")
    # 較正した主目盛りの位置(原図の目盛りと重なるはず)
    for v in np.arange(0, 10.01, 1.0):
        ax.axvline(v, color="#3d8f3d", lw=0.6, alpha=0.6, zorder=1)
    for v in np.arange(0.0, 1.0001, 0.1):
        ax.axhline(v, color="#3d8f3d", lw=0.6, alpha=0.6, zorder=1)
    for s in LINE_SERIES:
        X, V = read_csv(out_dir / f"fig3_{cfg.key}_{s}.csv")
        st = SERIES_STYLE[s]
        # 抜けの所で線を切る
        dx = np.diff(X)
        brk = np.nonzero(dx > 0.06)[0]
        segs = np.split(np.arange(len(X)), brk + 1)
        for k, idx in enumerate(segs):
            ax.plot(X[idx], V[idx], "-", color=st["color"], lw=0.9, zorder=3,
                    label=st["label"] if k == 0 else None)
        ax.plot(X, V, ".", color=st["color"], ms=max(1.5, 0.9 * scale), zorder=3)
    for s in MARKER_ORDER:
        X, V = read_csv(out_dir / f"fig3_{cfg.key}_{s}.csv")
        st = SERIES_STYLE[s]
        ax.plot(X, V, "+", color=st["color"], ms=9 + 2 * scale, mew=1.0, zorder=4, label=st["label"])
    ax.set_xlim(*xl)
    ax.set_ylim(*yl)
    ax.set_axis_off()
    if legend_loc:
        ax.legend(loc=legend_loc[0], bbox_to_anchor=legend_loc[1], fontsize=10, framealpha=0.9,
                  title="抽出値(CSV を読み戻して描画)", title_fontsize=10)
    fig.savefig(path, dpi=100)
    plt.close(fig)


GAP_PX = 15  # これより長い(X で約 0.15)抜けを「取れなかった区間」として記録する


def line_summary(L, cal):
    xs = np.sort(L["px"][:, 0])
    gaps = []
    for a, b in zip(xs[:-1], xs[1:]):
        if b - a > GAP_PX:
            gaps.append([round(float(np.polyval(cal["cx"], a)), 3), round(float(np.polyval(cal["cx"], b)), 3)])
    reasons = {}
    for _, r in L["skipped"]:
        reasons[r] = reasons.get(r, 0) + 1
    return dict(n=int(len(L["px"])), n_trim=L["n_trim"], n_outlier=L["n_outlier"], skipped=reasons,
                X_first=round(float(np.polyval(cal["cx"], xs[0])), 3),
                X_last=round(float(np.polyval(cal["cx"], xs[-1])), 3), gaps_X=gaps)


def main():
    out_dir = HERE
    fig_dir = HERE / "figures"
    check_dir = HERE / "check"
    check_dir.mkdir(exist_ok=True)
    report = {}
    for cfg in CONFIGS:
        im, w = load_layers(fig_dir / cfg.image)
        ink = w["black"]
        frame = find_frame(ink)
        cal = calibrate(ink, frame, cfg)
        # 記号の探索範囲: 枠の内側。凡例は除く
        region = (int(frame["left_in"]) + 6, int(frame["top_in"]) + 6,
                  int(frame["right_in"]) - 6, int(frame["bottom_in"]) - 6)
        tmpls = {c: make_template(w[c], cfg.legend_symbols[c]) for c in ("red", "green", "blue", "black")}
        markers = {}
        for c in ("red", "green", "blue", "black"):
            found = detect_markers(w, c, tmpls[c], region)
            lb = cfg.legend_box
            found = [m for m in found if not (lb[0] <= m["x"] <= lb[2] and lb[1] <= m["y"] <= lb[3])]
            markers[c] = found
        # 線の抽出用マスク
        H, W = ink.shape
        keep = np.ones_like(ink)
        keep[:int(frame["top_in"]) + 4, :] = 0
        keep[int(frame["bottom_in"]) - 26:, :] = 0
        keep[:, :int(frame["left_in"]) + 26] = 0
        keep[:, int(frame["right_in"]) - 4:] = 0
        lb = cfg.legend_box
        keep[lb[1]:lb[3], lb[0]:lb[2]] = 0
        occ_color = (w["red"] + w["green"] + w["blue"]) > 0.15
        lines, work = extract_lines(ink, occ_color, cal, cfg, markers["black"], tmpls["black"], keep)

        # CSV
        for c, lst in markers.items():
            rows = [px2data(cal, m["x"], m["y"]) for m in lst]
            write_csv(out_dir / f"fig3_{cfg.key}_{MARKER_SERIES[c]}.csv", ["X", cfg.value_name], rows)
        for s, L in lines.items():
            rows = [px2data(cal, x, y) for x, y in L["px"]]
            write_csv(out_dir / f"fig3_{cfg.key}_{s}.csv", ["X", cfg.value_name], rows)
        draw_overlay(im, cal, cfg, out_dir, check_dir / f"fig3_{cfg.key}_overlay.png", legend_loc=cfg.legend_loc)
        for zname, box, sc in cfg.zooms:
            draw_overlay(im, cal, cfg, out_dir, check_dir / f"fig3_{cfg.key}_zoom_{zname}.png", box=box, scale=sc)
        if DEBUG:
            Image.fromarray((255 - np.clip(work, 0, 1) * 255).astype(np.uint8)).save(
                check_dir / f"debug_{cfg.key}_line_ink.png")
        # 凡例の記号は等間隔の行(□=3, ▽=4, ◇=5, ○=6 行目)に置かれる。点対称な □・◇・○ の
        # 中心から 4 行目の位置を求め、▽ の型の基準点(輪郭の重心)と比べる
        rows = np.array([3.0, 5.0, 6.0])
        ys = np.array([tmpls[c]["legend_anchor"][1] for c in ("red", "blue", "black")])
        b, a = np.polyfit(rows, ys, 1)
        g = tmpls["green"]
        tri_check = dict(row_pitch_px=float(b), expected_y=float(a + 4 * b),
                         centroid_y=float(g["legend_anchor"][1]),
                         bbox_center_y=float(g["legend_bbox_center"][1]))
        report[cfg.key] = dict(
            triangle_anchor_check=tri_check,
            frame=frame,
            cal=dict(cx=list(cal["cx"]), cy=list(cal["cy"]),
                     resid_x_px_max=float(np.abs(cal["resid_x_px"]).max()),
                     resid_y_px_max=float(np.abs(cal["resid_y_px"]).max()),
                     n_ticks_x=len(cal["ticks_x"]), n_ticks_y=len(cal["ticks_y"])),
            templates={c: dict(anchor_off=t["anchor_off"], bbox=[int(v) for v in t["bbox"]],
                               legend_anchor=t["legend_anchor"]) for c, t in tmpls.items()},
            markers={c: lst for c, lst in markers.items()},
            lines={s: line_summary(L, cal) for s, L in lines.items()},
        )
    with open(HERE / "check" / "digitize_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        HERE = Path(sys.argv[1]).resolve()
    main()
