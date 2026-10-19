"""汎用計算の設定と見積もり。Qt に依存しない(S2 の check-case との照合は G3)。"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .casefile import HELIUM, K_BOLTZMANN

# Bird (1994) 表 A1、273 K。統括役の提供値(一次資料との照合は統括役が担当)。
GASES = {"He": dict(HELIUM), **{
    name: dict(molecular_mass=mass, mu_ref=mu, T_ref=273.0, omega=omega)
    for name, mass, mu, omega in (("Ne", 33.5e-27, 2.975e-5, 0.66), ("Ar", 66.3e-27, 2.117e-5, 0.81),
                                 ("Kr", 139.1e-27, 2.328e-5, 0.80), ("Xe", 218.0e-27, 2.107e-5, 0.85))}}


@dataclass
class GeneralSettings:
    mode: str = "gsis"
    gas: str = "He"
    molecular_mass: float = HELIUM["molecular_mass"]
    mu_ref: float = HELIUM["mu_ref"]
    T_ref: float = HELIUM["T_ref"]
    omega: float = HELIUM["omega"]
    L_ref: float = 1e-3
    length_auto: bool = True
    T0: float = 273.15
    wall_temperature: float = 273.15
    p_ref: float = 100.0
    T_min: float = 273.15
    T_max: float = 273.15
    u_max: float = 0.0
    velocity_auto: bool = True
    vel_n: list[int] = field(default_factory=lambda: [16, 16, 16])
    vel_range: list[list[float]] = field(default_factory=lambda: [[-5.0, 5.0] for _ in range(3)])
    n_cis: int = 5
    max_pbc: int = 10
    cfl_pbc: float = 500.0
    tolerance: float = 1e-5
    velocity_chunk: int = 0
    estimated_cells: int = 100000
    free_vram_gib: float = 14.7
    kinetic: dict[str, Any] = field(default_factory=dict)
    macro: dict[str, Any] = field(default_factory=dict)
    gsis_extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if self.mode not in ("gsis", "cis", "nsf"):
            raise ValueError("計算モードは GSIS-PBC / CIS / NSF です")
        for key in ("molecular_mass", "mu_ref", "T_ref", "omega", "L_ref", "T0", "wall_temperature", "p_ref", "T_min", "T_max",
                    "cfl_pbc", "tolerance", "free_vram_gib"):
            value = getattr(self, key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} は有限の正の数にしてください")
        if not math.isfinite(self.u_max) or self.u_max < 0 or self.T_min > self.T_max:
            raise ValueError("速度は 0 以上、温度は T_min ≤ T_max にしてください")
        for key in ("n_cis", "max_pbc", "velocity_chunk", "estimated_cells"):
            value = getattr(self, key)
            if type(value) is not int or value < (0 if key == "velocity_chunk" else 1):
                raise ValueError(f"{key} の整数が範囲外です")
        if not self.velocity_auto:
            if len(self.vel_n) != 3 or any(type(n) is not int or n < 2 for n in self.vel_n):
                raise ValueError("速度格子は各軸 2 点以上にしてください")
            if len(self.vel_range) != 3 or any(len(r) != 2 or not all(math.isfinite(v) for v in r)
                                             or r[0] >= r[1] for r in self.vel_range):
                raise ValueError("速度格子は有限の下限 < 上限にしてください")

    def update_boundary_defaults(self, boundaries: list[dict[str, Any]]) -> None:
        """G2 から温度・圧力・速度の既定を更新する窓口(値は SI)。"""
        temps = [float(b["temperature"]) for b in boundaries if b.get("temperature") is not None]
        pressures = [float(b["pressure"]) for b in boundaries if b.get("pressure") is not None and b["pressure"] > 0]
        vectors = [b.get("velocity") or b.get("tangential_velocity") for b in boundaries]
        speeds = [math.sqrt(sum(float(v) ** 2 for v in u)) for u in vectors if isinstance(u, (list, tuple))]
        if temps:
            self.T_min, self.T_max = min(temps), max(temps)
            self.T0 = self.T_max
        if pressures:
            self.p_ref = max(pressures)
        self.u_max = max(speeds, default=0.0)


def velocity_grid(s: GeneralSettings) -> tuple[list[int], list[list[float]]]:
    """範囲は sqrt(R*T0) 単位。中点則の点数を偶数にし、幅 / n ≤ 0.7 sqrt(R*T_min)。"""
    s.validate()
    if not s.velocity_auto:
        return list(s.vel_n), [list(r) for r in s.vel_range]
    R = K_BOLTZMANN / s.molecular_mass
    limit = s.u_max + 5 * math.sqrt(R * s.T_max)
    step = 0.7 * math.sqrt(R * s.T_min)
    n = max(2, 2 * math.ceil(limit / step))
    limit_nd = limit / math.sqrt(R * s.T0)
    return [n] * 3, [[-limit_nd, limit_nd] for _ in range(3)]


def estimate_vram(n_cells: int, n_velocity: int, *, chunk: int = 0, free_bytes: int = int(14.7 * 2**30),
                  mode: str = "gsis") -> dict[str, Any]:
    """FP32 の f + 4 本のチャンク作業領域 + 暫定固定費。空きから 1 GB を残す。

    固定費はメッシュ未生成時の保守的な 0.1 GB。S2 と照合するまでは概算として扱う。
    """
    if n_cells < 1 or n_velocity < 1 or chunk < 0 or free_bytes <= 0:
        raise ValueError("メモリ見積もりの入力が範囲外です")
    overhead = 100_000_000
    reserve = 1_000_000_000
    budget = max(0, free_bytes - reserve)
    f = 0 if mode == "nsf" else n_cells * n_velocity * 4
    per_chunk = n_cells * 16
    automatic = max(0, (budget - f - overhead) // per_chunk)
    width = 0 if mode == "nsf" else min(n_velocity, chunk if chunk else automatic)
    work = width * per_chunk
    total = f + work + overhead
    fits = total <= budget and (mode == "nsf" or width >= 1)
    return dict(distribution=f, workspace=work, overhead=overhead, reserve=reserve, budget=budget,
                chunk=width, total=total, fits=fits)


def derived(s: GeneralSettings) -> dict[str, Any]:
    n, ranges = velocity_grid(s)
    R = K_BOLTZMANN / s.molecular_mass
    mu = s.mu_ref * (s.T0 / s.T_ref) ** s.omega
    mean_free_path = mu / s.p_ref * math.sqrt(math.pi * R * s.T0 / 2)
    memory = estimate_vram(s.estimated_cells, math.prod(n), chunk=s.velocity_chunk,
                           free_bytes=int(s.free_vram_gib * 2**30), mode=s.mode)
    return dict(R=R, mu=mu, mean_free_path=mean_free_path, Kn=mean_free_path / s.L_ref,
                n=n, ranges=ranges, n_velocity=math.prod(n), memory=memory)
