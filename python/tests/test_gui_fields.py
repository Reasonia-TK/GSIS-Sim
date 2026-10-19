"""GUI の 3D 表示の計算(gsis_tools.gui.fields): 種の置き方、任意の断面、流線、線上の分布。

解析的な場を入れた箱の格子(x ∈ [0, 22]、y・z ∈ [−2, 2]、L_ref = 1 mm)で確かめる。Qt は使わない。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

pv = pytest.importorskip("pyvista")
pytest.importorskip("tomlkit")  # gsis_tools.gui.results がケース TOML を読む(extra gui)

from gsis_tools.gui.fields import (  # noqa: E402
    LINE_PRESETS,
    PLANE_PRESETS,
    SHOW,
    PlaneSpec,
    ResultBundle,
    StreamSpec,
    VisRequest,
    build_vis,
    cloud_points,
    csv_column_name,
    disk_points,
    line_points,
    plane_basis,
    plane_offset_range,
    profile_scale,
    sample_line,
    split_profile_key,
    write_profile_csv,
)
from gsis_tools.gui.results import RunResult, Scales  # noqa: E402

L = 1e-3
SCALES = Scales(length=L, temperature=273.15, pressure=20.0, density=20.0 / (2076.16 * 273.15), velocity=753.06,
                heat_flux=20.0 * 753.06, mass_flux=20.0 / (2076.16 * 273.15) * 753.06)
V2 = math.sqrt(2.0) * SCALES.velocity
U0 = 0.3  # u_x / √(2RT0)


def make_bundle(tmp_path: Path) -> ResultBundle:
    """箱の格子に、p = p_ref (1 − 0.04 x/L)、u = (U0 √(2RT0), 0, 0)、T = T0 を入れる。"""
    img = pv.ImageData(dimensions=(45, 17, 17), spacing=(0.5 * L, 0.25 * L, 0.25 * L), origin=(0.0, -2 * L, -2 * L))
    g = img.cast_to_unstructured_grid()
    c = np.asarray(g.cell_centers().points)
    n = g.n_cells
    g.cell_data["p"] = SCALES.pressure * (1.0 - 0.04 * c[:, 0] / L)
    g.cell_data["rho"] = np.full(n, SCALES.density)
    g.cell_data["u"] = np.column_stack([np.full(n, U0 * V2), np.zeros(n), np.zeros(n)])
    g.cell_data["T"] = np.full(n, SCALES.temperature)
    run = RunResult(directory=tmp_path, summary={"case": "box"}, scales=SCALES)
    return ResultBundle(run=run, grid=g, surface=g.extract_surface(algorithm="dataset_surface"))


def test_seed_points():
    d = disk_points((7.0, 0.0, 0.0), (1.0, 0.0, 0.0), 1.5, 60)
    assert d.shape == (60, 3)
    assert np.allclose(d[:, 0], 7.0)  # 法線 x の円盤
    assert np.all(np.hypot(d[:, 1], d[:, 2]) <= 1.5 + 1e-12)
    t = disk_points((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), 2.0, 40)
    assert np.allclose(t @ (np.ones(3) / math.sqrt(3.0)), 0.0)  # 傾いた円盤も平面の上
    ln = line_points((0.0, 0.0, 0.0), (1.0, 2.0, 3.0), 5)
    assert np.allclose(ln[0], 0.0) and np.allclose(ln[-1], (1.0, 2.0, 3.0)) and ln.shape == (5, 3)
    c1 = cloud_points((1.0, 2.0, 3.0), 0.5, 100)
    assert np.array_equal(c1, cloud_points((1.0, 2.0, 3.0), 0.5, 100))  # 毎回同じ点
    assert np.all(np.linalg.norm(c1 - (1.0, 2.0, 3.0), axis=1) <= 0.5 + 1e-12)
    e1, e2 = plane_basis((0.0, 0.0, 1.0))
    assert abs(e1 @ e2) < 1e-12 and abs(e1[2]) < 1e-12 and abs(np.linalg.norm(e2) - 1.0) < 1e-12


def test_presets_and_profile_keys():
    assert set(PLANE_PRESETS) == {"z0", "y0", "x", "free"} and PLANE_PRESETS["x"][2] == (1.0, 0.0, 0.0)
    assert LINE_PRESETS["axis_tube"][1:] == ((8.0, 0.0, 0.0), (18.0, 0.0, 0.0))
    assert split_profile_key("u_x") == ("u", "0") and split_profile_key("|q|") == ("q", "mag")
    assert split_profile_key("sigma_xy") == ("sigma", "3") and split_profile_key("Kn_local") == ("Kn_local", "value")
    assert csv_column_name("u_x", SCALES, True) == "u_x/sqrt(2RT0)" and csv_column_name("p", SCALES, False) == "p[Pa]"
    f, label = profile_scale("u_x", SCALES, True)
    assert f == pytest.approx(1.0 / V2) and label == "u_x / √(2RT0)"


def test_arbitrary_slice_and_contours(tmp_path):
    b = make_bundle(tmp_path)
    for origin, normal in (((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)), ((11.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
                           ((13.0, 0.3, -0.2), (1.0, 0.0, 0.0))):
        plane = PlaneSpec(origin, tuple(np.asarray(normal) / np.linalg.norm(normal)))
        res = build_vis(b, VisRequest(field="p", mode="contour", plane=plane, n_contours=8))
        pts = np.asarray(res.slice.points)
        assert np.allclose((pts - np.asarray(origin) * L) @ np.asarray(plane.normal), 0.0, atol=1e-12)
        # 断面の値はセル値 p/p_ref(x の 1 次式)そのもの
        x = np.asarray(res.slice.cell_centers().points)[:, 0] / L
        assert np.max(np.abs(res.slice.cell_data[SHOW] - (1.0 - 0.04 * x))) < 0.04 * 0.5  # セルの幅 0.5 の中の差
        assert res.label == "p / p_ref"
        if normal == (1.0, 0.0, 0.0):
            assert res.contours is None  # p は x だけの関数なので、x 一定の断面には等値線がない
        else:
            assert res.contours is not None and res.contours.n_points > 0
    # 3D: 既定の斜めの視点から見て断面の奥の側の壁を残す(法線 z → z ≤ 0 の側)
    res = build_vis(b, VisRequest(field="p", mode="3d", plane=PlaneSpec((0, 0, 0), (0, 0, 1))))
    assert res.half is not None and res.half.bounds[5] <= 1e-12
    res = build_vis(b, VisRequest(field="p", mode="3d", plane=PlaneSpec((13.0, 0, 0), (1.0, 0, 0))))
    assert res.half.bounds[0] >= 13.0 * L - 1e-12  # 法線 x(視点は −x の側)→ x ≥ 13 の側
    # 色の範囲
    res = build_vis(b, VisRequest(field="p", mode="slice", plane=PlaneSpec((13.0, 0, 0), (1.0, 0, 0)),
                                  range_mode="view"))
    assert res.clim[1] - res.clim[0] < 0.05  # 断面の中だけ(x = 13 の 1 層)
    res = build_vis(b, VisRequest(field="p", mode="slice", range_mode="manual", clim=(0.2, 0.4)))
    assert res.clim == (0.2, 0.4)
    assert build_vis(b, VisRequest(field="p", mode="none")).slice is None
    lo, hi = plane_offset_range(b.grid.bounds, PlaneSpec((13.0, 0, 0), (1.0, 0, 0)), L)
    assert lo == pytest.approx(-13.0) and hi == pytest.approx(9.0)


def test_streamlines_in_uniform_flow(tmp_path):
    b = make_bundle(tmp_path)
    spec = StreamSpec(kind="line", a=(3.0, -1.0, 0.5), b=(3.0, 1.0, 0.5), n=5, direction="both", max_length=60.0)
    res = build_vis(b, VisRequest(field="p", mode="none", stream=spec))
    lines = res.streams
    assert lines is not None and lines.n_cells >= 5
    pts = np.asarray(lines.points) / L
    # 一様な流れ(x 向き)なので流線はまっすぐ: y・z は種の値のまま、x は領域の端から端まで
    assert np.allclose(pts[:, 2], 0.5, atol=1e-9)
    assert np.all(np.min(np.abs(pts[:, 1][:, None] - np.linspace(-1.0, 1.0, 5)[None, :]), axis=1) < 1e-9)
    assert pts[:, 0].min() < 1.0 and pts[:, 0].max() > 21.0  # 端の手前(1 歩以内)で止まる
    assert np.allclose(lines.point_data["__speed_show__"], U0, rtol=1e-9)  # 色は速さ |u| / √(2RT0)
    assert res.stream_label == "|u| / √(2RT0)" and "計算" in res.stream_info
    again = build_vis(b, VisRequest(field="T", mode="slice", stream=spec))
    assert "前の計算を使った" in again.stream_info  # 同じ設定の流線は作り直さない
    # 円盤の種、逆向きだけ
    disk = StreamSpec(kind="disk", center=(15.0, 0.0, 0.0), normal=(1.0, 0.0, 0.0), radius=1.0, n=10,
                      direction="backward")
    pts = np.asarray(build_vis(b, VisRequest(field="p", mode="none", stream=disk)).streams.points) / L
    assert pts[:, 0].max() <= 15.0 + 1e-9 and pts[:, 0].min() < 1.0  # 上流へだけ伸びる(端の手前で止まる)


def test_plane_streamlines_stay_on_plane(tmp_path):
    b = make_bundle(tmp_path)
    plane = PlaneSpec((0.0, 0.0, 0.3), (0.0, 0.0, 1.0))
    spec = StreamSpec(kind="plane2d", n=20, direction="both", max_length=60.0)
    res = build_vis(b, VisRequest(field="p", mode="slice", plane=plane, stream=spec))
    pts = np.asarray(res.streams.points) / L
    assert res.streams.n_cells >= 10
    assert np.allclose(pts[:, 2], 0.3, atol=1e-9)
    assert res.stream_label == "|u_t| / √(2RT0)"
    # 断面に垂直な速度は投影で消える: 法線 x の断面では面内の速さは 0 → 流線にならない
    res = build_vis(b, VisRequest(field="p", mode="slice", plane=PlaneSpec((10.0, 0, 0), (1.0, 0, 0)), stream=spec))
    assert res.streams is None


def test_sample_line_and_csv(tmp_path):
    b = make_bundle(tmp_path)
    prof = sample_line(b, (2.0, -0.3, 0.1), (20.0, 0.4, -0.2), 50)
    assert prof.s.size == 50 and prof.n_valid == 50
    assert prof.s[-1] == pytest.approx(math.dist((2.0, -0.3, 0.1), (20.0, 0.4, -0.2)))
    x = prof.points[:, 0]
    # 1 次式の場は、点へ補間しても(内部の点では)そのまま
    assert np.allclose(prof.values["p"] / SCALES.pressure, 1.0 - 0.04 * x, atol=1e-9)
    assert np.allclose(prof.values["u_x"] / V2, U0) and np.allclose(prof.values["|u|"] / V2, U0)
    out = prof = sample_line(b, (-5.0, 0.0, 0.0), (5.0, 0.0, 0.0), 21)  # 半分は領域の外
    assert out.n_valid == 11 and np.isnan(out.values["p"][0])
    path = write_profile_csv(tmp_path / "line.csv", prof, SCALES, True)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("s,x,y,z,p/p_in,rho/rho_in,u_x/sqrt(2RT0)")
    assert lines[1].split(",")[4] == ""  # 領域の外は空欄
    d = np.genfromtxt(path, delimiter=",", skip_header=1)
    assert d.shape == (21, len(lines[0].split(",")))
    with pytest.raises(ValueError):
        sample_line(b, (1.0, 0.0, 0.0), (1.0, 0.0, 0.0), 10)
