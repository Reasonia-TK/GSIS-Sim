"""G1 の Qt なしの計算・プロジェクト・CAD の試験。"""

import json
import math
from pathlib import Path

import numpy as np
import pytest
import tomlkit

from gsis_tools.gui.general_settings import GASES, GeneralSettings, derived, estimate_vram, velocity_grid
from gsis_tools.gui.project import STAGES, Project, case_document, load_project, save_project


def test_velocity_auto_covers_boundary_envelope():
    s = GeneralSettings(T0=400, T_min=50, T_max=1200, u_max=2100)
    d = derived(s)
    R = d["R"]
    for n, (lo, hi) in zip(d["n"], d["ranges"]):
        scale = math.sqrt(R * s.T0)
        assert n % 2 == 0
        assert hi * scale >= s.u_max + 5 * math.sqrt(R * s.T_max) - 1e-9
        assert lo == -hi
        assert (hi - lo) * scale / n <= 0.7 * math.sqrt(R * s.T_min)


def test_velocity_manual_and_boundary_defaults():
    s = GeneralSettings(velocity_auto=False, vel_n=[30, 24, 24], vel_range=[[-10, 10], [-8, 8], [-8, 8]])
    assert velocity_grid(s)[0] == [30, 24, 24]
    s.update_boundary_defaults([dict(temperature=200, pressure=1, velocity=[3, 4, 0]),
                                dict(temperature=500, pressure=7), dict(type="vacuum")])
    assert (s.T0, s.T_min, s.T_max, s.p_ref, s.u_max) == (500, 200, 500, 7, 5)
    assert s.wall_temperature == 273.15
    s.update_boundary_defaults([dict(type="mass_flow_inlet", mass_flow=1, temperature=300, pressure=None, velocity=None)])
    assert s.p_ref == 7  # 圧力境界がなければ入力値を保つ。


@pytest.mark.parametrize("key,value", [("T_min", 0), ("T_max", float("nan")), ("p_ref", -1),
                                      ("molecular_mass", float("inf")), ("u_max", -1),
                                      ("velocity_chunk", -1), ("n_cis", 1.5), ("estimated_cells", 0)])
def test_invalid_settings(key, value):
    s = GeneralSettings()
    setattr(s, key, value)
    with pytest.raises(ValueError):
        derived(s)


def test_gas_values_and_mean_free_path():
    assert GASES["He"] == dict(molecular_mass=6.65e-27, mu_ref=1.916e-5, T_ref=273.15, omega=0.66)
    for key, mass, mu, omega in (("Ne", 33.5e-27, 2.975e-5, 0.66), ("Ar", 66.3e-27, 2.117e-5, 0.81),
                                  ("Kr", 139.1e-27, 2.328e-5, 0.80), ("Xe", 218.0e-27, 2.107e-5, 0.85)):
        g = GASES[key]
        assert g == dict(molecular_mass=mass, mu_ref=mu, T_ref=273.0, omega=omega)
    s = GeneralSettings()
    d = derived(s)
    assert d["mean_free_path"] == pytest.approx(s.mu_ref / s.p_ref * math.sqrt(math.pi * d["R"] * s.T0 / 2))
    assert d["Kn"] * s.L_ref == d["mean_free_path"]


def test_vram_budget_and_chunk_reduction():
    m = estimate_vram(135000, 17280, chunk=2048)
    assert m["distribution"] == 9_331_200_000
    assert m["workspace"] == 4_423_680_000
    assert m["fits"]
    auto = estimate_vram(135000, 17280, free_bytes=11_000_000_000)
    assert 0 < auto["chunk"] < 2048 and auto["total"] <= auto["budget"]
    assert not estimate_vram(135000, 17280, free_bytes=10_000_000_000)["fits"]
    assert not estimate_vram(135000, 17280, chunk=2048, free_bytes=11_000_000_000)["fits"]
    nsf = estimate_vram(135000, 17280, mode="nsf")
    assert nsf["distribution"] == nsf["workspace"] == 0


def test_stage_gating_and_invalidation():
    p = Project()
    with pytest.raises(ValueError, match="形状"):
        p.complete("settings")
    for key in STAGES:
        p.complete(key)
    p.mesh["validated"] = True
    p.change("settings")
    assert p.stages["geometry"] == "done"
    assert all(p.stages[k] == "redo" for k in STAGES[1:])
    assert "validated" not in p.mesh
    p.complete("settings")
    assert not p.blocked_reason("boundaries")
    assert p.blocked_reason("run")


def test_project_roundtrip_and_portable_save_as(tmp_path):
    p = Project(name="外部流の試験", directory=tmp_path / "original")
    save_project(p)
    geom = p.asset("geometry/source.brep")
    geom.write_text("test asset", encoding="utf-8")
    p.geometry = dict(source="geometry/source.brep", domain="geometry/source.brep", faces=[dict(id=1, normal=None)])
    p.complete("geometry")
    p.boundaries = [dict(patch="wall", type="maxwell_wall", temperature=300, accommodation=0.5,
                         faces=[1], fingerprints=[dict(area=1, centroid=[0, 0, 0], normal=[1, 0, 0])]),
                    dict(patch="inlet", type="mass_flow_inlet", mass_flow=1e-9, temperature=300,
                         tangential_velocity=[0, 0, 0])]
    save_project(p)
    q = load_project(p.directory)
    assert q.boundaries == p.boundaries and q.settings == p.settings and q.stages == p.stages
    save_project(q, tmp_path / "copy")
    assert q.asset("geometry/source.brep").read_text() == "test asset"
    doc = tomlkit.parse(q.asset("case.toml").read_text(encoding="utf-8"))
    assert doc["boundary"][0]["accommodation"] == 0.5
    assert "faces" not in doc["boundary"][0] and "fingerprints" not in doc["boundary"][0]
    assert doc["boundary"][1]["mass_flow"] == 1e-9
    assert not p.dirty and not q.dirty


def test_project_rejects_future_version_and_escaping_paths(tmp_path):
    p = Project(directory=tmp_path / "project")
    path = save_project(p)
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace("version = 1", "version = 2"), encoding="utf-8")
    with pytest.raises(ValueError, match="版"):
        load_project(path)
    path.write_text(original, encoding="utf-8")
    p.geometry["source"] = "../escape.brep"
    with pytest.raises(ValueError, match="フォルダ内"):
        save_project(p)
    assert path.read_text(encoding="utf-8") == original


def test_save_as_rejects_nested_and_nonempty_destinations(tmp_path):
    p = Project(directory=tmp_path / "project")
    save_project(p)
    with pytest.raises(ValueError, match="入れ子"):
        save_project(p, p.directory / "copy")
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="空"):
        save_project(p, other)
    assert (other / "keep.txt").read_text() == "keep"


def test_missing_domain_invalidates_later_stages(tmp_path):
    p = Project(directory=tmp_path / "p", geometry=dict(domain="geometry/missing.brep"))
    for key in STAGES:
        p.complete(key)
    save_project(p)
    q = load_project(p.directory)
    assert q.stages["geometry"] == "redo" and q.blocked_reason("settings")


@pytest.fixture
def cad_files(tmp_path):
    from gsis_tools.mcp.engine import ModelEngine
    from gsis_tools.mcp.workspace import Workspace
    e = ModelEngine(Workspace(tmp_path))
    try:
        e.call("model_new", {"unit": "mm"})
        e.call("add_box", dict(corner=[0, 0, 0], size=[10, 20, 30]))
        for ext in ("step", "iges", "brep"):
            e.call("export", {"path": f"box.{ext}"})
        e.call("add_box", dict(corner=[100, 0, 0], size=[1, 1, 1]))
        e.call("export", {"path": "multiple.brep"})
    finally:
        e.close()
    return tmp_path


@pytest.mark.parametrize("extension", ["step", "iges", "brep"])
def test_cad_formats_and_face_normals(cad_files, extension):
    from gsis_tools.gui.geometry import prepare_geometry
    result = prepare_geometry(cad_files / f"box.{extension}", cad_files / "project")
    m = result.metadata
    assert m["valid"] and m["volume"] == pytest.approx(6e-6)
    assert m["length_ref"] == pytest.approx(0.01, abs=1e-8)
    assert len(m["faces"]) == 6
    assert sum(f["area"] for f in m["faces"]) == pytest.approx(0.0022)
    for f in m["faces"]:
        assert np.linalg.norm(f["normal"]) == pytest.approx(1)
        assert np.dot(np.asarray(f["centroid"]) - [0.005, 0.01, 0.015], f["normal"]) > 0
    assert result.surface.points.max() == pytest.approx(0.03)


def test_external_domain_and_multiple_solids(cad_files):
    from gsis_tools.gui.geometry import prepare_geometry
    result = prepare_geometry(cad_files / "box.brep", cad_files / "external", flow="external", box_factor=3)
    m = result.metadata
    assert m["volume"] == pytest.approx(26 * 6e-6, rel=1e-5)
    assert len(m["faces"]) == 12
    # 内側の物体の面の法線は物体へ向く。
    assert any(np.dot(np.asarray(f["centroid"]) - [0.005, 0.01, 0.015], f["normal"]) < 0 for f in m["faces"])
    with pytest.raises(ValueError, match="立体"):
        prepare_geometry(cad_files / "multiple.brep", cad_files / "bad")


def test_brep_units_and_step_physical_units(cad_files):
    from gsis_tools.gui.geometry import prepare_geometry
    # BREP は単位情報がない。同じ座標 10 を m で読むと 10 m。
    a = prepare_geometry(cad_files / "box.brep", cad_files / "m", unit="m")
    assert a.metadata["volume"] == pytest.approx(6000)
    # STEP の mm はメタデータで読み、m への変換で物理的な長さを保つ。
    b = prepare_geometry(cad_files / "box.step", cad_files / "step_m", unit="m")
    assert b.metadata["volume"] == pytest.approx(6e-6)


def test_tiny_features_are_reported_without_removal(tmp_path):
    from gsis_tools.mcp.engine import ModelEngine
    from gsis_tools.mcp.workspace import Workspace
    from gsis_tools.gui.geometry import inspect_geometry
    e = ModelEngine(Workspace(tmp_path))
    try:
        e.call("model_new", dict(unit="mm"))
        e.call("add_box", dict(corner=[0, 0, 0], size=[1e-5, 10, 10]))
        report = inspect_geometry(e)
        assert report["valid"]
        assert any("小さい面" in w for w in report["warnings"])
        assert any("短い辺" in w for w in report["warnings"])
        assert len(report["faces"]) == 6
    finally:
        e.close()


def test_broken_cad_is_an_input_error(tmp_path):
    from gsis_tools.gui.geometry import prepare_geometry
    path = tmp_path / "broken.brep"
    path.write_text("not a BREP", encoding="utf-8")
    with pytest.raises(ValueError, match="CAD"):
        prepare_geometry(path, tmp_path / "project")
