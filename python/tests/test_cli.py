"""CLI(gsis-tools)。"""

from __future__ import annotations

import pytest

from gsis_tools.cli import main
from gsis_tools.pmesh import read_pmesh


def test_mesh_slab_and_info(tmp_path, capsys):
    out = tmp_path / "slab.pmesh"
    rc = main(["mesh", "slab", "--length", "1e-3", "--nx", "20", "--first-cell", "1e-5",
               "--out", str(out), "--vtu", str(tmp_path / "slab.vtu"),
               "--boundary-vtu", str(tmp_path / "slab_b.vtu"), "--report", str(tmp_path / "slab.md")])
    assert rc == 0
    m = read_pmesh(out)
    assert m.n_cells == 20 and m.patch_names() == ["wall_left", "wall_right", "sym_y", "sym_z"]
    for f in ("slab.vtu", "slab_b.vtu", "slab.md"):
        assert (tmp_path / f).stat().st_size > 0
    rc = main(["mesh-info", str(out)])
    assert rc == 0
    assert "検査: OK" in capsys.readouterr().out


def test_mesh_tube_hex_small(tmp_path):
    out = tmp_path / "tube.pmesh"
    rc = main(["mesh", "tube", "--kind", "hex", "--n-theta", "6", "--ds-wall", "0.1", "--ds-mouth", "0.1",
               "--h-tube", "0.6", "--max-size", "1.0", "--out", str(out), "--report", str(tmp_path / "q.md")])
    assert rc == 0
    m = read_pmesh(out)
    assert m.patch_names() == ["inlet", "outlet", "wall"]
    text = (tmp_path / "q.md").read_text(encoding="utf-8")
    assert "上流槽" in text and "inlet" in text


def test_convert_msh(tmp_path):
    gmsh = pytest.importorskip("gmsh")
    from gsis_tools.gmsh_convert import gmsh_session

    msh = tmp_path / "box.msh"
    with gmsh_session("box", verbosity=0):
        gmsh.model.occ.addBox(0, 0, 0, 1, 1, 1)
        gmsh.model.occ.synchronize()
        tags = [t for _, t in gmsh.model.getEntities(2)]
        gmsh.model.addPhysicalGroup(2, tags[:1], name="lid")
        gmsh.model.addPhysicalGroup(2, tags[1:], name="walls")
        gmsh.model.addPhysicalGroup(3, [1], name="fluid")
        gmsh.option.setNumber("Mesh.MeshSizeMax", 0.5)
        gmsh.model.mesh.generate(3)
        gmsh.write(str(msh))
    out = tmp_path / "box.pmesh"
    rc = main(["convert", str(msh), "--out", str(out), "--patch-kind", "walls=wall", "--patch-order", "walls,lid"])
    assert rc == 0
    m = read_pmesh(out)
    assert m.patch_names() == ["walls", "lid"]
    assert m.patches[0].kind == "wall" and m.patches[1].kind == "patch"


def test_cli_error_message(tmp_path, capsys):
    rc = main(["mesh-info", str(tmp_path / "missing.pmesh")])
    assert rc == 1
    assert "エラー" in capsys.readouterr().err
