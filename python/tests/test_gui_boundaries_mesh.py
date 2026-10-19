"""G2 の境界、指紋、四面体と現行ソルバーとの接続(Qt なし)。"""

import copy
import json
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest
import tomlkit

from gsis_tools.gui.boundaries import (assign_boundary, fingerprint, remap_boundaries, validate_boundary,
                                       validate_boundaries, with_default)
from gsis_tools.gui.general_mesh import MeshCancelled, generate_mesh, mesh_memory, validate_options
from gsis_tools.gui.geometry import prepare_geometry
from gsis_tools.gui.project import STAGES, Project, case_document, load_project, save_project


def make_project(tmp_path, shape="box", unit="mm"):
    from gsis_tools.mcp.engine import ModelEngine
    from gsis_tools.mcp.workspace import Workspace
    cad = tmp_path / "cad"
    e = ModelEngine(Workspace(cad))
    try:
        e.call("model_new", dict(unit=unit))
        e.call("add_box", dict(corner=[-2, -2, -1], size=[4, 4, 2], name="plate"))
        if shape == "orifice":
            e.call("add_cylinder", dict(base=[0, 0, -2], axis=[0, 0, 4], radius=0.6, name="hole"))
            e.call("boolean", dict(op="cut", objects=["plate"], tools=["hole"], name="orifice"))
        e.call("export", dict(path="fluid.brep"))
    finally:
        e.close()
    p = Project(name=shape, directory=tmp_path / "project")
    result = prepare_geometry(cad / "fluid.brep", p.directory, unit=unit)
    p.geometry = result.metadata
    p.complete("geometry")
    p.settings.L_ref = p.geometry["length_ref"]
    p.complete("settings")
    p.boundaries = with_default([], p.geometry["faces"], 273.15)
    return p, result


def assign_supported(p):
    faces = p.geometry["faces"]
    for name, kind, direction, values in (
        ("inlet", "pressure_inlet", [-1, 0, 0], dict(temperature=300, pressure=120, tangential_velocity="zero")),
        ("outlet", "pressure_outlet", [1, 0, 0], dict(pressure=80)),
        ("vacuum", "vacuum", [0, 1, 0], {}),
        ("symmetry", "symmetry", [0, 0, -1], {})):
        ids = [f["id"] for f in faces if f["type"] == "plane" and np.allclose(f.get("normal"), direction)]
        assert ids
        p.boundaries = assign_boundary(p.boundaries, dict(patch=name, type=kind, faces=ids, **values), faces, 273.15)
    p.settings.update_boundary_defaults(p.boundaries)
    p.complete("boundaries")


def options(p):
    h = p.geometry["length_ref"] / 2
    return dict(name="fluid", size_max=h, size_min=h / 4, growth=1.3, patch_sizes={"inlet": h / 2})


def test_new_stages_stay_pending():
    p = Project()
    p.change("geometry")
    assert set(p.stages.values()) == {"pending"}
    p.complete("geometry")
    p.change("geometry")
    assert p.stages["geometry"] == "redo"
    assert all(p.stages[k] == "pending" for k in STAGES[1:])


@pytest.mark.parametrize("kind,values", [
    ("diffuse_wall", dict(temperature=300, velocity=[3, 4, 0])),
    ("maxwell_wall", dict(temperature=300, accommodation=0.5)),
    ("pressure_inlet", dict(temperature=300, pressure=100, tangential_velocity="extrapolate")),
    ("pressure_outlet", dict(pressure=10)), ("vacuum", {}),
    ("mass_flow_inlet", dict(temperature=300, mass_flow=1e-9, tangential_velocity="zero")), ("symmetry", {})])
def test_boundary_kinds(kind, values):
    face = dict(id=1, area=1, centroid=[0, 0, 0], normal=[1, 0, 0], type="plane")
    b = dict(patch="test", type=kind, faces=[1], **values)
    validate_boundary(b, [face])
    p = Project(boundaries=[dict(b, fingerprints=[fingerprint(face)], automatic=False)])
    text = tomlkit.dumps(case_document(p))
    assert "automatic" not in text and "fingerprints" not in text and "faces" not in text
    assert tomlkit.parse(text)["boundary"][0]["type"] == kind


@pytest.mark.parametrize("values", [dict(temperature=0), dict(temperature=float("nan")),
    dict(temperature=300, accommodation=0), dict(temperature=300, accommodation=1.01),
    dict(temperature=300, accommodation=0.5, velocity=[0, float("inf"), 0])])
def test_invalid_maxwell(values):
    face = dict(id=1, type="plane", normal=[1, 0, 0])
    with pytest.raises(ValueError):
        validate_boundary(dict(patch="wall", type="maxwell_wall", faces=[1], **values), [face])


@pytest.mark.parametrize("faces", [[dict(id=1, type="cylinder", normal=[1, 0, 0])],
    [dict(id=1, type="plane", normal=[0.8, 0.6, 0])], [dict(id=1, type="plane", normal=None)],
    [dict(id=1, type="plane", normal=[1, 0, 0]), dict(id=2, type="plane", normal=[0, 1, 0])]])
def test_symmetry_rejects_invalid_planes(faces):
    with pytest.raises(ValueError):
        validate_boundary(dict(patch="sym", type="symmetry", faces=[f["id"] for f in faces]), faces)


def test_fingerprints_ignore_ids_and_report_missing_or_ambiguous(tmp_path):
    p, _ = make_project(tmp_path)
    assign_supported(p)
    new = copy.deepcopy(p.geometry["faces"])
    for f in new:
        f["id"] += 100
    out, lost = remap_boundaries(p.boundaries, list(reversed(new)))
    assert not lost
    assert {t for b in out for t in b["faces"]} == {f["id"] for f in new}
    validate_boundaries(out, new)
    out, lost = remap_boundaries(p.boundaries, new[1:])
    assert len(lost) == 1
    ambiguous = dict(new[0], id=999)
    _, lost = remap_boundaries(p.boundaries, new + [ambiguous])
    assert len(lost) == 1
    old = p.geometry["faces"][0]
    changed = copy.deepcopy(new)
    changed[0]["normal"] = [-v for v in old["normal"]]
    _, lost = remap_boundaries(p.boundaries, changed)
    assert len(lost) == 1
    inlet = next(b for b in p.boundaries if b["patch"] == "inlet")
    with pytest.raises(ValueError, match="使われています"):
        assign_boundary(p.boundaries, dict(inlet, patch="outlet"), p.geometry["faces"], 273.15, replace="inlet")


@pytest.mark.parametrize("shape", ["box", "orifice"])
def test_boundary_mesh_case_check_case(tmp_path, shape):
    p, _ = make_project(tmp_path, shape)
    assign_supported(p)
    result = generate_mesh(p, options(p))
    assert result["cells"] > 0
    assert result["memory"]["fits"]
    assert all(abs(v["relative_difference"]) < 0.06 for v in result["patches"].values())
    assert result["non_orthogonality"]["max"] < 90
    assert set(result["patches"]) == {b["patch"] for b in p.boundaries}
    p.mesh.update(result)
    p.mesh["validated"] = True
    p.complete("mesh")
    save_project(p)
    saved = json.loads(p.asset("boundaries.json").read_text(encoding="utf-8"))
    assert all(len(b["faces"]) == len(b["fingerprints"]) for b in saved)
    assert "auto" not in tomlkit.parse(p.asset("case.toml").read_text(encoding="utf-8"))["velocity_grid"]
    assert load_project(p.directory).boundaries == p.boundaries
    solver = Path(__file__).resolve().parents[2] / "build/release/src/gsis-solver.exe"
    assert solver.is_file(), "依頼で指定された check-case 用ソルバーがありません"
    cp = subprocess.run([str(solver), "check-case", str(p.asset("case.toml"))], capture_output=True, timeout=30)
    assert cp.returncode == 0, (cp.stdout + cp.stderr).decode("utf-8", errors="replace")
    p.settings.free_vram_gib = 0.01
    assert not mesh_memory(p, result["cells"])["fits"]
    assert "VRAM" in p.blocked_reason("run")
    p.asset(p.mesh["path"]).unlink()
    q = load_project(p.directory)
    assert q.stages["mesh"] == "redo" and not q.mesh.get("validated")


def test_stop_preserves_old_mesh_and_cleans_process(tmp_path):
    p, _ = make_project(tmp_path)
    assign_supported(p)
    old = p.asset("mesh/fluid.pmesh")
    old.parent.mkdir(exist_ok=True)
    old.write_bytes(b"previous mesh")
    cancel = threading.Event()
    timer = threading.Timer(0.15, cancel.set)
    timer.start()
    try:
        with pytest.raises(MeshCancelled):
            generate_mesh(p, options(p), cancel)
    finally:
        timer.join()
    assert old.read_bytes() == b"previous mesh"
    assert not list(old.parent.glob(".generating_*"))


@pytest.mark.parametrize("changes", [dict(size_min=2), dict(size_max=0), dict(growth=1), dict(growth=4),
    dict(name="../escape"), dict(patch_sizes={"inlet": -1}), dict(patch_sizes={"missing": 0.1})])
def test_bad_mesh_options(changes):
    values = dict(name="fluid", size_min=0.01, size_max=1, growth=1.3, patch_sizes={})
    with pytest.raises(ValueError):
        validate_options(dict(values, **changes), [dict(patch="inlet")])
