"""メッシュ品質の計算とレポート。

指標(幾何量は pmesh §4 の定義による):

- **非直交角** … 内部面で面法線 n と d = x_N - x_O のなす角 [deg]。境界面は n と x_f - x_O のなす角
- **歪み(skewness)** … 内部面は OpenFOAM の定義: x_O と x_N を結ぶ線分が面の平面と交わる点
  (法線方向の距離で内分した点)と面中心との距離を |d| で割ったもの。境界面は
  |(x_f - x_O) の面内成分| / (2 × 法線方向の距離)(境界の外に鏡映したセルを想定)
- **隣接セルの体積比** … 内部面ごとの max(V_O/V_N, V_N/V_O)
- **セル寸法** … h_V = V^(1/3)(体積の 3 乗根)と、セルの辺の長さ(最大・最小・平均)
- **辺の長さの比** … セルごとの最大辺 / 最小辺
- **要素の種類** … 面の構成から判定(四面体・ピラミッド・プリズム・六面体・その他の多面体)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .pmesh import PolyMesh, check_mesh
from .topology import CELL_TYPE_NAMES, classify_cells

NONORTH_BINS = (0, 10, 20, 30, 40, 50, 60, 70, 80, 90)


@dataclass
class MeshQuality:
    """品質の指標(面ごと・セルごとの配列)。"""

    cell_type: np.ndarray  # (n_cells,) topology.CELL_* の番号
    non_orthogonality: np.ndarray  # 内部面 [deg]
    boundary_non_orthogonality: np.ndarray  # 境界面 [deg]
    skewness: np.ndarray  # 内部面
    boundary_skewness: np.ndarray  # 境界面
    volume_ratio: np.ndarray  # 内部面
    cell_size: np.ndarray  # V^(1/3)
    cell_max_edge: np.ndarray
    cell_min_edge: np.ndarray
    cell_mean_edge: np.ndarray
    cell_max_non_orthogonality: np.ndarray  # セルの内部面の最大 [deg]
    cell_max_skewness: np.ndarray  # セルの内部面の最大

    @property
    def edge_ratio(self) -> np.ndarray:
        return self.cell_max_edge / self.cell_min_edge

    def cell_type_counts(self) -> dict[str, int]:
        return {CELL_TYPE_NAMES[k]: int(np.count_nonzero(self.cell_type == k)) for k in CELL_TYPE_NAMES if np.any(self.cell_type == k)}

    def cell_data(self) -> dict[str, np.ndarray]:
        """VTU に書くセルデータ。"""
        return {
            "cell_type": self.cell_type.astype(np.int32),
            "cell_size": self.cell_size,
            "max_edge": self.cell_max_edge,
            "edge_ratio": self.edge_ratio,
            "max_non_orthogonality": self.cell_max_non_orthogonality,
            "max_skewness": self.cell_max_skewness,
        }


def _angle_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # atan2(|a×b|, a·b) は 0° 付近でも精度が落ちない(arccos は 0° 付近で分解能が約 1e-6° に止まる)
    cross = np.linalg.norm(np.cross(a, b), axis=1)
    dot = np.einsum("ij,ij->i", a, b)
    return np.degrees(np.arctan2(cross, dot))


def face_edge_lengths(mesh: PolyMesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """面ごとの辺の長さの最大・最小・合計。"""
    fo = mesh.face_offsets
    fn = mesh.face_nodes.astype(np.int64)
    starts = fo[:-1]
    nxt = np.arange(fn.shape[0]) + 1
    nxt[fo[1:] - 1] = starts
    L = np.linalg.norm(mesh.points[fn[nxt]] - mesh.points[fn], axis=1)
    return (
        np.maximum.reduceat(L, starts),
        np.minimum.reduceat(L, starts),
        np.add.reduceat(L, starts),
    )


def compute_quality(mesh: PolyMesh) -> MeshQuality:
    g = mesh.geometry()
    ni, nf, n = mesh.n_internal_faces, mesh.n_faces, mesh.n_cells
    own = mesh.owner.astype(np.int64)
    nei = mesh.neighbour.astype(np.int64)
    S, xf, xc, V = g.face_area_vectors, g.face_centres, g.cell_centres, g.cell_volumes

    # 非直交角
    d = xc[nei] - xc[own[:ni]]
    nonorth = _angle_deg(S[:ni], d)
    db = xf[ni:] - xc[own[ni:]]
    b_nonorth = _angle_deg(S[ni:], db)

    # 歪み
    A = np.linalg.norm(S, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        nhat = S / A[:, None]
        d_own = np.abs(np.einsum("ij,ij->i", nhat[:ni], xf[:ni] - xc[own[:ni]]))
        d_nei = np.abs(np.einsum("ij,ij->i", nhat[:ni], xc[nei] - xf[:ni]))
        tot = d_own + d_nei
        x_int = xc[own[:ni]] * (d_nei / tot)[:, None] + xc[nei] * (d_own / tot)[:, None]
        skew = np.linalg.norm(xf[:ni] - x_int, axis=1) / np.linalg.norm(d, axis=1)
        dn = np.einsum("ij,ij->i", nhat[ni:], db)
        tang = db - dn[:, None] * nhat[ni:]
        b_skew = np.linalg.norm(tang, axis=1) / (2.0 * np.abs(dn))

    # 体積比
    with np.errstate(invalid="ignore", divide="ignore"):
        r = V[own[:ni]] / V[nei]
        vratio = np.maximum(r, 1.0 / r)

    # 辺の長さ
    fmax, fmin, fsum = face_edge_lengths(mesh)
    sizes = mesh.face_sizes()
    cells = np.concatenate([own, nei])
    faces = np.concatenate([np.arange(nf), np.arange(ni)])
    cmax = np.full(n, -np.inf)
    np.maximum.at(cmax, cells, fmax[faces])
    cmin = np.full(n, np.inf)
    np.minimum.at(cmin, cells, fmin[faces])
    cmean = np.bincount(cells, weights=fsum[faces], minlength=n) / np.bincount(cells, weights=sizes[faces], minlength=n)

    # セルごとの最大(内部面)
    c_nonorth = np.zeros(n)
    np.maximum.at(c_nonorth, own[:ni], nonorth)
    np.maximum.at(c_nonorth, nei, nonorth)
    c_skew = np.zeros(n)
    np.maximum.at(c_skew, own[:ni], skew)
    np.maximum.at(c_skew, nei, skew)

    return MeshQuality(
        cell_type=classify_cells(mesh),
        non_orthogonality=nonorth,
        boundary_non_orthogonality=b_nonorth,
        skewness=skew,
        boundary_skewness=b_skew,
        volume_ratio=vratio,
        cell_size=np.cbrt(V),
        cell_max_edge=cmax,
        cell_min_edge=cmin,
        cell_mean_edge=cmean,
        cell_max_non_orthogonality=c_nonorth,
        cell_max_skewness=c_skew,
    )


def stats(a: np.ndarray) -> dict[str, float]:
    """最小・平均・中央値・95/99 パーセンタイル・最大。"""
    a = np.asarray(a, dtype=np.float64)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {k: float("nan") for k in ("min", "mean", "p50", "p95", "p99", "max")}
    p = np.percentile(a, [50, 95, 99])
    return {
        "min": float(a.min()),
        "mean": float(a.mean()),
        "p50": float(p[0]),
        "p95": float(p[1]),
        "p99": float(p[2]),
        "max": float(a.max()),
    }


def summary(mesh: PolyMesh, q: MeshQuality | None = None, *, scale: float = 1.0) -> dict:
    """主要な統計(寸法は ``scale`` で割った値。管なら R を渡すと R 単位になる)。"""
    q = q or compute_quality(mesh)
    g = mesh.geometry()
    out = {
        "n_points": mesh.n_points,
        "n_faces": mesh.n_faces,
        "n_internal_faces": mesh.n_internal_faces,
        "n_cells": mesh.n_cells,
        "cell_types": q.cell_type_counts(),
        "total_volume": float(g.cell_volumes.sum()),
        "patches": {
            p.name: {"kind": p.kind, "faces": p.count, "area": float(g.face_areas[p.faces].sum())}
            for p in mesh.patches
        },
        "cell_size": stats(q.cell_size / scale),
        "cell_max_edge": stats(q.cell_max_edge / scale),
        "cell_min_edge": stats(q.cell_min_edge / scale),
        "edge_ratio": stats(q.edge_ratio),
        "non_orthogonality": stats(q.non_orthogonality),
        "boundary_non_orthogonality": stats(q.boundary_non_orthogonality),
        "skewness": stats(q.skewness),
        "boundary_skewness": stats(q.boundary_skewness),
        "volume_ratio": stats(q.volume_ratio),
    }
    return out


def _fmt(v: float) -> str:
    if not np.isfinite(v):
        return "-"
    if v == 0:
        return "0"
    if abs(v) >= 1e4 or abs(v) < 1e-3:
        return f"{v:.3e}"
    return f"{v:.4g}"


def _stats_row(name: str, s: dict[str, float]) -> str:
    return f"| {name} | " + " | ".join(_fmt(s[k]) for k in ("min", "mean", "p50", "p95", "p99", "max")) + " |"


def format_report(
    mesh: PolyMesh,
    q: MeshQuality | None = None,
    *,
    title: str = "メッシュ品質",
    length_scale: float = 1.0,
    length_label: str = "m",
    regions: Mapping[str, np.ndarray] | None = None,
    exact_volume: float | None = None,
    exact_areas: Mapping[str, float] | None = None,
    notes: list[str] | None = None,
    elapsed: Mapping[str, float] | None = None,
) -> str:
    """Markdown の品質レポート。

    ``length_scale`` / ``length_label`` は寸法の表示単位(管なら R と ``"R"``)。
    ``regions`` は領域名 → セルの真偽配列(領域別のセル寸法の表を作る)。
    """
    q = q or compute_quality(mesh)
    g = mesh.geometry()
    res = check_mesh(mesh)
    L = length_scale
    lines = [f"# {title}", ""]
    if notes:
        lines += [f"- {n}" for n in notes]
        lines.append("")
    lines += [
        "## 概要",
        "",
        "| 項目 | 値 |",
        "|---|---|",
        f"| セル数 | {mesh.n_cells:,} |",
        f"| 面の数(内部 / 境界) | {mesh.n_faces:,}({mesh.n_internal_faces:,} / {mesh.n_boundary_faces:,}) |",
        f"| 節点数 | {mesh.n_points:,} |",
        "| 要素の内訳 | " + "、".join(f"{k} {v:,}" for k, v in q.cell_type_counts().items()) + " |",
    ]
    vol = float(g.cell_volumes.sum())
    if exact_volume:
        lines.append(f"| 体積の合計 [{length_label}³] | {_fmt(vol / L**3)}(解析値 {_fmt(exact_volume / L**3)}、相対差 {(vol - exact_volume) / exact_volume:+.3e}) |")
    else:
        lines.append(f"| 体積の合計 [{length_label}³] | {_fmt(vol / L**3)} |")
    lines.append(f"| 検査(pmesh §4) | {'OK' if res.ok else 'エラー'}(最大 \\|ΣS\\|/Σ\\|S\\| = {_fmt(res.info.get('最大 |ΣS|/Σ|S|', float('nan')))}、内部面の向きの違反 {res.info.get('内部面の向きの違反', '-')} 面) |")
    if elapsed:
        for k, v in elapsed.items():
            lines.append(f"| {k} | {v:.1f} s |")
    lines += ["", "## パッチ", "", f"| 名前 | kind | 面の数 | 面積 [{length_label}²] |" + (" 解析値 | 相対差 |" if exact_areas else ""),
              "|---|---|---|---|" + ("---|---|" if exact_areas else "")]
    for p in mesh.patches:
        a = float(g.face_areas[p.faces].sum())
        row = f"| {p.name} | {p.kind} | {p.count:,} | {_fmt(a / L**2)} |"
        if exact_areas:
            ex = exact_areas.get(p.name)
            row += f" {_fmt(ex / L**2)} | {(a - ex) / ex:+.3e} |" if ex else " - | - |"
        lines.append(row)

    head = "| 指標 | 最小 | 平均 | 中央値 | 95% | 99% | 最大 |"
    sep = "|---|---|---|---|---|---|---|"
    lines += [
        "",
        f"## セル寸法 [{length_label}]",
        "",
        "h_V = V^(1/3)(体積の 3 乗根)。辺の長さはセルの辺についての最大・最小・平均。",
        "",
        head,
        sep,
        _stats_row("h_V", stats(q.cell_size / L)),
        _stats_row("最大辺", stats(q.cell_max_edge / L)),
        _stats_row("平均辺", stats(q.cell_mean_edge / L)),
        _stats_row("最小辺", stats(q.cell_min_edge / L)),
        _stats_row("最大辺 / 最小辺", stats(q.edge_ratio)),
    ]
    if regions:
        lines += [
            "",
            f"### 領域別 [{length_label}]",
            "",
            "| 領域 | セル数 | h_V 平均 | h_V 95% | h_V 最大 | 平均辺 平均 | 最大辺 最大 |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, mask in regions.items():
            mask = np.asarray(mask, dtype=bool)
            if not mask.any():
                lines.append(f"| {name} | 0 | - | - | - | - | - |")
                continue
            hv = q.cell_size[mask] / L
            lines.append(
                f"| {name} | {int(mask.sum()):,} | {_fmt(hv.mean())} | {_fmt(np.percentile(hv, 95))} | {_fmt(hv.max())} | "
                f"{_fmt(q.cell_mean_edge[mask].mean() / L)} | {_fmt(q.cell_max_edge[mask].max() / L)} |"
            )

    lines += [
        "",
        "## 非直交角・歪み・体積比",
        "",
        head,
        sep,
        _stats_row("非直交角 [deg](内部面)", stats(q.non_orthogonality)),
        _stats_row("非直交角 [deg](境界面)", stats(q.boundary_non_orthogonality)),
        _stats_row("歪み(内部面)", stats(q.skewness)),
        _stats_row("歪み(境界面)", stats(q.boundary_skewness)),
        _stats_row("隣接セルの体積比", stats(q.volume_ratio)),
        "",
        "### 非直交角の分布(内部面)",
        "",
        "| 範囲 [deg] | 面の数 | 割合 |",
        "|---|---|---|",
    ]
    hist, _ = np.histogram(q.non_orthogonality, bins=NONORTH_BINS)
    total = max(1, q.non_orthogonality.size)
    for lo, hi, c in zip(NONORTH_BINS[:-1], NONORTH_BINS[1:], hist):
        lines.append(f"| {lo}–{hi} | {int(c):,} | {100.0 * c / total:.2f}% |")
    if res.warnings or res.errors:
        lines += ["", "## 検査の指摘", ""]
        lines += [f"- [エラー] {e}" for e in res.errors]
        lines += [f"- [警告] {w}" for w in res.warnings]
    lines.append("")
    return "\n".join(lines)
