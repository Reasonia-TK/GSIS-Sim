"""GUI の history.csv の解析と追いかけ読み(gsis_tools.gui.history)。"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import numpy as np
import pytest

from gsis_tools.gui.history import (
    KIND_KINETIC,
    KIND_MACRO,
    HistoryTail,
    mass_balance,
    mass_flow_series,
    patch_role,
    read_history,
)
from gsis_tools.gui.paths import repo_root

ROOT = repo_root()
SQ = math.sqrt(2.0 / math.pi)

KIN_HEADER = ("iteration,epsilon,eps_rho,eps_u,eps_T,residual,residual_rel,total_mass,mass_scale,"
              "mdot_inlet_kg_s,mdot_outlet_kg_s,mdot_wall_kg_s,mdot_inlet_nd,mdot_outlet_nd,mdot_wall_nd,"
              "time_iter_s,time_total_s")
MAC_HEADER = ("iteration,cfl,residual_mass,residual_momentum,residual_energy,update_p,update_u,update_T,"
              "mdot_inlet,mdot_outlet,mdot_wall,seconds,elapsed_seconds")


def kin_row(i: int, eps: float, m_in: float, m_out: float) -> str:
    return f"{i},{eps},{eps / 3},{eps},{eps / 5},0.1,0.5,1800,1,{m_in * 1e-8},{m_out * 1e-8},0,{m_in},{m_out},0,0.6,{0.6 * (i + 1)}"


def test_kinetic_history(tmp_path: Path):
    p = tmp_path / "history.csv"
    rows = [f"0,nan,nan,nan,nan,0.02,1,1838,1,0,0,0,0,20.4,0,0.5,1.0"]
    rows += [kin_row(i, 10.0 ** (-i), -0.25, 0.2501) for i in range(1, 6)]
    p.write_text(KIN_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    h = read_history(p)
    assert h.n == 6 and h.kind == KIND_KINETIC
    assert h.convergence_columns() == ["epsilon"]
    assert "eps_u" in h.convergence_candidates()
    mf = h.mass_flow_columns()
    assert [(c.patch, c.column, c.unit) for c in mf] == [
        ("inlet", "mdot_inlet_nd", "nd"), ("outlet", "mdot_outlet_nd", "nd"), ("wall", "mdot_wall_nd", "nd")]
    assert math.isnan(h.column("epsilon")[0]) and h.last("epsilon") == pytest.approx(1e-5)
    series, ylabel = mass_flow_series(h, nd_to_Mdot=SQ, mass_flow_scale=3.4e-8)
    assert ylabel == r"無次元質量流量 $\dot{M}$"
    assert [s.role for s in series] == ["in", "out"]  # 壁は既定では描かない
    assert series[0].y[-1] == pytest.approx(0.25 * SQ)  # 入口は流入を正にする
    assert series[1].y[-1] == pytest.approx(0.2501 * SQ)
    assert mass_balance(series) == pytest.approx(abs(0.25 - 0.2501) / (0.5 * (0.25 + 0.2501)))
    series2, ylabel2 = mass_flow_series(h)
    assert "無次元" in ylabel2 and series2[1].y[-1] == pytest.approx(0.2501)
    assert h.elapsed_seconds()[-1] == pytest.approx(3.6)


def test_macro_history_and_roles(tmp_path: Path):
    p = tmp_path / "history.csv"
    lines = [MAC_HEADER] + [f"{i},5,{0.9 / i},{1 / i},{1 / i},{0.1 / i},{0.01 / i},{0.05 / i},{-1.12},{1.12},0,0.8,{0.8 * i}"
                            for i in range(1, 4)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    h = read_history(p)
    assert h.kind == KIND_MACRO
    assert h.convergence_columns() == ["update_p", "update_u", "update_T", "residual_mass"]
    assert [(c.patch, c.unit) for c in h.mass_flow_columns()] == [("inlet", "nd"), ("outlet", "nd"), ("wall", "nd")]
    series, _ = mass_flow_series(h, nd_to_Mdot=SQ, mass_flow_scale=1.0,
                                 patch_types={"inlet": "pressure_inlet", "outlet": "vacuum", "wall": "diffuse_wall"})
    assert series[0].y[-1] == pytest.approx(1.12 * SQ)
    assert patch_role("inlet_left") == "in" and patch_role("exhaust", {"exhaust": "pressure_outlet"}) == "out"
    assert patch_role("wall") == "other" and patch_role("sym_y") == "other"


GSIS_HEADER = ("iteration,cis_iterations,epsilon,eps_rho,eps_u,eps_T,eps_half,pbc_change,residual,total_mass,"
               "pbc_iterations,mdot_inlet_kg_s,mdot_outlet_kg_s,mdot_wall_kg_s,mdot_inlet_nd,mdot_outlet_nd,mdot_wall_nd,"
               "mdot_pbc_inlet_nd,mdot_pbc_outlet_nd,mdot_pbc_wall_nd,M_dot_inlet,M_dot_outlet,time_iter_s,time_total_s")


def test_gsis_run_history(tmp_path: Path):
    """フェーズ 4 の run の列(build/phase4 の出力の見出しと同じ)。PBC 側の流量は別の系列で、差の計算に入れない。"""
    p = tmp_path / "history.csv"
    rows = [f"{i},5,{10.0 ** -i},0,0,0,0,0,0,1800,10,0,0,0,-0.30,0.31,0,-0.32,0.33,0,0.24,0.25,12.0,{12.0 * i}"
            for i in range(1, 4)]
    p.write_text(GSIS_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    h = read_history(p)
    assert h.kind == "gsis" and h.convergence_columns() == ["epsilon"]
    cols = h.mass_flow_columns()
    assert [(c.patch, c.column, c.source) for c in cols] == [
        ("inlet", "mdot_inlet_nd", "main"), ("outlet", "mdot_outlet_nd", "main"), ("wall", "mdot_wall_nd", "main"),
        ("inlet", "mdot_pbc_inlet_nd", "pbc"), ("outlet", "mdot_pbc_outlet_nd", "pbc"), ("wall", "mdot_pbc_wall_nd", "pbc")]
    series, _ = mass_flow_series(h, nd_to_Mdot=1.0, mass_flow_scale=1.0)
    assert [(s.patch, s.source) for s in series] == [("inlet", "main"), ("outlet", "main"), ("inlet", "pbc"),
                                                    ("outlet", "pbc")]
    assert series[2].label == "inlet(PBC 側、流入)" and series[2].y[-1] == pytest.approx(0.32)
    assert mass_balance(series) == pytest.approx(0.01 / 0.305)  # 運動論側だけで計算する
    only_main, _ = mass_flow_series(h, include_pbc=False)
    assert all(s.source == "main" for s in only_main)


def test_si_only_mass_flow(tmp_path: Path):
    p = tmp_path / "h.csv"
    p.write_text("iteration,epsilon,mdot_inlet_kg_s,mdot_outlet_kg_s\n1,0.1,-2e-8,2e-8\n", encoding="utf-8")
    h = read_history(p)
    assert [c.unit for c in h.mass_flow_columns()] == ["kg_s", "kg_s"]
    series, _ = mass_flow_series(h, nd_to_Mdot=SQ, mass_flow_scale=4e-8)
    assert series[0].y[-1] == pytest.approx(0.5) and series[1].y[-1] == pytest.approx(0.5)


def test_tail_reads_incrementally_and_detects_restart(tmp_path: Path):
    p = tmp_path / "history.csv"
    tail = HistoryTail(p)
    assert tail.poll() == 0  # まだファイルがない
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(KIN_HEADER + "\n" + kin_row(1, 0.5, -0.2, 0.3) + "\n" + kin_row(2, 0.1, -0.2, 0.3) + "\n")
        fh.write(kin_row(3, 0.05, -0.2, 0.3)[:10])  # 行の途中まで
    assert tail.poll() == 2
    assert tail.poll() == 0
    with open(p, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(kin_row(3, 0.05, -0.2, 0.3)[10:] + "\n")
    assert tail.poll() == 1
    h = tail.history()
    assert h.n == 3 and list(h.iterations()) == [1.0, 2.0, 3.0]
    gen = tail.generation
    # 同じ見出しで作り直された(新しい計算)
    p.write_text(KIN_HEADER + "\n" + kin_row(1, 0.9, -0.1, 0.1) + "\n", encoding="utf-8")
    assert tail.poll() == 1
    assert tail.generation > gen and tail.history().n == 1
    assert tail.history().column("epsilon")[0] == pytest.approx(0.9)


def test_tail_ignores_old_file(tmp_path: Path):
    p = tmp_path / "history.csv"
    p.write_text(KIN_HEADER + "\n" + kin_row(1, 0.5, -0.2, 0.3) + "\n", encoding="utf-8")
    old = time.time() - 100
    os.utime(p, (old, old))
    tail = HistoryTail(p, min_mtime=time.time() - 10)
    assert tail.poll() == 0 and tail.history().n == 0
    p.write_text(KIN_HEADER + "\n" + kin_row(1, 0.5, -0.2, 0.3) + "\n", encoding="utf-8")
    assert tail.poll() == 1


RUN = ROOT / "runs/kinetic/tube_delta1_cis"


@pytest.mark.skipif(not (RUN / "history.csv").is_file(), reason="既存の出力がない")
def test_real_kinetic_history_matches_summary():
    h = read_history(RUN / "history.csv")
    s = json.loads((RUN / "summary.json").read_text(encoding="utf-8"))
    assert h.kind == KIND_KINETIC
    assert int(h.last("iteration")) == s["iterations"]
    assert h.last("epsilon") == pytest.approx(s["epsilon"], rel=1e-12)
    series, _ = mass_flow_series(h, nd_to_Mdot=SQ, mass_flow_scale=s["mass_flow"]["scale_kg_s"])
    by = {x.patch: x.y[-1] for x in series}
    assert by["inlet"] == pytest.approx(s["mass_flow"]["M_dot_inlet"], rel=1e-6)
    assert by["outlet"] == pytest.approx(s["mass_flow"]["M_dot_outlet"], rel=1e-6)
    assert np.all(np.isfinite(h.iterations()))
