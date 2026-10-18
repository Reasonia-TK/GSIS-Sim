"""GUI のケース TOML の読み書き(gsis_tools.gui.casefile)。"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

pytest.importorskip("tomlkit")

from gsis_tools.gui import casefile  # noqa: E402
from gsis_tools.gui.casefile import Boundary, CaseFileError, format_float, parse_scalar  # noqa: E402
from gsis_tools.gui.paths import default_solver, repo_root  # noqa: E402
from gsis_tools.gui.solver import run_capture  # noqa: E402

ROOT = repo_root()
CASE_FILES = sorted(
    p for pat in ("cases/tube_vacuum/kinetic/*.toml", "cases/tube_vacuum/macro/*.toml", "cases/smoke/*.toml")
    for p in ROOT.glob(pat)
)


def test_case_files_found():
    assert len(CASE_FILES) >= 5


@pytest.mark.parametrize("path", CASE_FILES, ids=lambda p: p.name)
def test_roundtrip_existing_case_is_identical(path: Path):
    """変更しなければ、書き出した TOML は元のファイルと 1 文字も違わない(コメント・並びを保つ)。"""
    cd = casefile.load_case(path)
    text = casefile.render_case(cd.model, cd.doc)
    assert text == path.read_text(encoding="utf-8")


def test_roundtrip_with_changes_keeps_comments(tmp_path: Path):
    src = ROOT / "cases/tube_vacuum/kinetic/tube_delta1_cis.toml"
    cd = casefile.load_case(src)
    m = cd.model.copy()
    m.ref_value = 100.0
    m.boundaries[1] = Boundary("outlet", "pressure_outlet", pressure=0.0)
    m.boundaries.append(Boundary("sym", "symmetry"))
    m.gsis = {"n_cis": 7, "tolerance": 2e-6}
    m.kinetic = {"limiter_k": 5.0, "backend": "cpu"}
    m.macro = {"advection": "upwind2", "linear_max_iterations": 300}
    out = tmp_path / "x.toml"
    saved = casefile.save_case(out, m, cd.doc)
    text = out.read_text(encoding="utf-8")
    # 値の往復
    again = casefile.load_case(out).model
    assert again == m == saved.model
    # 元のコメントは残る
    assert "# kg (He)" in text and "# K" in text and "円管 → 真空" in text
    # 種類に要らない値は書かない
    assert "[[boundary]]" in text and text.count("[[boundary]]") == 4
    assert 'type = "pressure_outlet"' in text and "pressure = 0.0" in text
    assert "sweep_blocks" not in text
    assert "linear_max_iterations = 300" in text


def test_remove_section_and_change_reference_kind(tmp_path: Path):
    cd = casefile.load_case(ROOT / "cases/tube_vacuum/macro/tube_hex_d1000_nsf.toml")
    m = cd.model.copy()
    m.macro = {}
    m.ref_kind, m.ref_value = "Kn", 0.05
    m.boundaries[0].pressure = 150.0
    text = casefile.render_case(m, cd.doc)
    assert "[macro]" not in text and "delta_rp" not in text and "Kn = 0.05" in text
    _, back = casefile.parse_case_text(text)
    assert back == m


@pytest.mark.parametrize("delta", casefile.PRESET_DELTAS)
def test_preset_new_document_roundtrip(delta: float):
    m = casefile.tube_preset(delta)
    assert m.name == f"tube_delta{delta:g}" and m.ref_value == delta
    text = casefile.render_case(m)
    assert text.startswith("# GSIS-PBC のケース定義")
    _, back = casefile.parse_case_text(text)
    assert back == m
    assert casefile.validate(m) == []


def test_format_float_is_toml_float():
    assert format_float(1e-5) == "1.0e-5"
    assert format_float(6.65e-27) == "6.65e-27"
    assert format_float(1.916e-05) == "1.916e-5"
    assert format_float(10.0) == "10.0"
    assert format_float(273.15) == "273.15"
    assert format_float(1e22) == "1.0e22"
    with pytest.raises(ValueError):
        format_float(math.nan)


def test_parse_scalar():
    assert parse_scalar("64") == 64 and isinstance(parse_scalar("64"), int)
    assert parse_scalar("3.0") == 3.0 and isinstance(parse_scalar("3.0"), float)
    assert parse_scalar("true") is True
    assert parse_scalar('"cpu"') == "cpu" and parse_scalar("cpu") == "cpu"
    assert parse_scalar("3", "float") == 3.0 and isinstance(parse_scalar("3", "float"), float)
    with pytest.raises(ValueError):
        parse_scalar("abc", "int")


def test_derive_matches_check_case():
    """check-case の表示(smoke.toml、δ_rp = 10)と同じ導出量になる。"""
    m = casefile.load_case(ROOT / "cases/smoke/smoke.toml").model
    d = casefile.derive(m)
    # check-case は有効数字 6 桁で表示する
    assert d.R == pytest.approx(2076.16, rel=1e-5)
    assert d.most_probable_speed == pytest.approx(1064.99, rel=1e-5)
    assert d.p_ref == pytest.approx(204.053, rel=1e-5)
    assert d.Kn == pytest.approx(0.0886227, rel=1e-5)
    assert d.delta == pytest.approx(10.0, rel=1e-12)
    assert d.mass_flow_scale == pytest.approx(3.39602e-07, rel=1e-5)
    assert d.v_ref == pytest.approx(753.063, rel=1e-5)
    assert d.nd_to_Mdot == pytest.approx(math.sqrt(2.0 / math.pi), rel=1e-12)
    assert d.has_p_in


def test_validate_finds_errors():
    m = casefile.tube_preset(10.0)
    m.boundaries[2].temperature = None
    m.boundaries.append(Boundary("wall", "diffuse_wall", 300.0))
    m.vel_range[1] = [8.0, -8.0]
    errs = casefile.validate(m)
    assert any("temperature" in e for e in errs)
    assert any("重複" in e for e in errs)
    assert any("下限 < 上限" in e for e in errs)
    m2 = casefile.tube_preset(10.0)
    m2.ref_kind, m2.ref_value = "p_ref", 100.0
    assert any("省略できるのは delta_rp" in e for e in casefile.validate(m2))


def test_bad_files_raise(tmp_path: Path):
    p = tmp_path / "bad.toml"
    p.write_text("[case\nname = 1\n", encoding="utf-8")
    with pytest.raises(CaseFileError):
        casefile.load_case(p)
    p.write_text('[case]\nname="a"\nmesh="m"\n[gas]\nmolecular_mass=1e-26\nmu_ref=1e-5\nT_ref=273.0\nomega=0.7\n'
                 '[conditions]\nL_ref=1.0\nT0=273.0\ndelta_rp=1.0\nKn=0.1\n[velocity_grid]\nn=[2,2,2]\n'
                 'range=[[-1.0,1.0],[-1.0,1.0],[-1.0,1.0]]\n', encoding="utf-8")
    with pytest.raises(CaseFileError, match="どれか 1 つ"):
        casefile.load_case(p)


def test_rebase_mesh(tmp_path: Path):
    old = tmp_path / "cases" / "a"
    new = tmp_path / "cases" / "b" / "c"
    assert casefile.rebase_mesh("../mesh/m.pmesh", old, new) == "../../mesh/m.pmesh"
    absolute = (tmp_path / "mesh" / "m.pmesh").as_posix()
    assert casefile.rebase_mesh(absolute, None, tmp_path / "cases") == "../mesh/m.pmesh"
    assert casefile.rebase_mesh("m.pmesh", None, new) == "m.pmesh"
    assert casefile.resolve_mesh("../mesh/m.pmesh", old) == (tmp_path / "cases" / "mesh" / "m.pmesh").resolve()


@pytest.mark.skipif(not default_solver().is_file(), reason="gsis-solver がビルドされていない")
def test_rendered_preset_passes_check_case(tmp_path: Path):
    """プリセットから書いた TOML を gsis-solver check-case が受け付ける(メッシュは smoke の箱)。"""
    m = casefile.tube_preset(10.0)
    m.mesh = (ROOT / "cases/smoke/box.pmesh").as_posix()
    m.kinetic = {"backend": "cpu"}
    m.macro = {"advection": "upwind1"}
    path = tmp_path / "preset.toml"
    casefile.save_case(path, m)
    cp = run_capture([str(default_solver()), "check-case", str(path)], cwd=ROOT, timeout=60)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "p_in" in cp.stdout
