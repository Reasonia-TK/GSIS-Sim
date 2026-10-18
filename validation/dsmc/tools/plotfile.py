"""単一レベルの AMReX plotfile を NumPy で読む(numpy のみ)。

DSMC 側の scripts/amrex_plotfile.py(C:\\Works\\20261003_DSMC)と同じ形式を読む。
大きな plotfile でも必要な変数だけを読めるよう、FAB 内の成分ごとにシークして読む。

    pf = PlotFile("runs/smoke/plt01000")
    rho = pf.get("rho")                      # (nx, ny, nz)
    data = pf.get_many(["rho", "u", "p"])    # {name: ndarray}
    x, y, z = pf.cell_centers()
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np

_BOX_RE = re.compile(r"\(\(\s*([-\d,\s]+)\)\s*\(\s*([-\d,\s]+)\)\s*\(\s*([-\d,\s]+)\)\)")
_FAB_RE = re.compile(r"\(\(\s*(\d+)\s*,\s*\(([\d\s]+)\)\s*\)\s*,\s*\(\s*(\d+)\s*,\s*\(([\d\s]+)\)\s*\)\s*\)")


def _parse_box(text: str):
    m = _BOX_RE.search(text)
    if m is None:
        raise ValueError("box を解釈できません: " + text)
    lo = np.array([int(v) for v in m.group(1).split(",")])
    hi = np.array([int(v) for v in m.group(2).split(",")])
    return lo, hi


class PlotFile:
    def __init__(self, path):
        self.path = Path(path)
        m = re.search(r"(\d+)$", self.path.name)
        self.step = int(m.group(1)) if m else -1
        self._read_header()
        self._read_level_header()
        self._cache: dict[str, np.ndarray] = {}

    def _read_header(self):
        lines = (self.path / "Header").read_text().splitlines()
        it = iter(lines)
        self.version = next(it)
        nvar = int(next(it))
        self.varnames = [next(it).strip() for _ in range(nvar)]
        self.ndim = int(next(it))
        self.time = float(next(it))
        if int(next(it)) != 0:
            raise NotImplementedError("複数レベルの plotfile には対応していません")
        self.prob_lo = np.array([float(v) for v in next(it).split()])
        self.prob_hi = np.array([float(v) for v in next(it).split()])
        next(it)
        self.domain_lo, self.domain_hi = _parse_box(next(it))
        next(it)
        self.dx = np.array([float(v) for v in next(it).split()])
        self.shape = tuple(int(v) for v in (self.domain_hi - self.domain_lo + 1))

    def _read_level_header(self):
        level = self.path / "Level_0"
        lines = (level / "Cell_H").read_text().splitlines()
        self.ncomp = int(lines[2])
        idx = 4
        nbox = int(re.match(r"\((\d+)\s+\d+", lines[idx]).group(1))
        self.boxes = [_parse_box(lines[idx + 1 + i]) for i in range(nbox)]
        idx += 1 + nbox + 1
        nfab = int(lines[idx])
        idx += 1
        self.fabs = []
        for i in range(nfab):
            parts = lines[idx + i].split()
            self.fabs.append((level / parts[1], int(parts[2])))

    def _read_components(self, comps: list[int]) -> np.ndarray:
        out = np.full((len(comps),) + self.shape, np.nan)
        for (lo, hi), (fname, offset) in zip(self.boxes, self.fabs):
            with open(fname, "rb") as f:
                f.seek(offset)
                header = f.readline().decode("ascii")
                m = _FAB_RE.search(header)
                nbytes = int(m.group(1))
                order = [int(v) for v in m.group(4).split()]
                dtype = np.dtype(("<" if order[0] == nbytes else ">") + ("f8" if nbytes == 8 else "f4"))
                flo, fhi = _parse_box(header[m.end():])
                n = fhi - flo + 1
                npts = int(np.prod(n))
                start = f.tell()
                sl = tuple(slice(flo[d] - self.domain_lo[d], fhi[d] - self.domain_lo[d] + 1) for d in range(3))
                for i, c in enumerate(comps):
                    f.seek(start + c * npts * nbytes)
                    arr = np.fromfile(f, dtype=dtype, count=npts)
                    out[(i,) + sl] = arr.reshape((n[2], n[1], n[0])).transpose(2, 1, 0)
        return out

    def get_many(self, names) -> dict[str, np.ndarray]:
        need = [n for n in names if n not in self._cache]
        if need:
            comps = [self.varnames.index(n) for n in need]
            data = self._read_components(comps)
            for i, n in enumerate(need):
                self._cache[n] = data[i]
        return {n: self._cache[n] for n in names}

    def get(self, name: str) -> np.ndarray:
        return self.get_many([name])[name]

    def cell_centers(self):
        return tuple(self.prob_lo[d] + (np.arange(self.shape[d]) + 0.5) * self.dx[d] for d in range(3))


def list_plotfiles(run_dir, prefix: str = "plt"):
    """run_dir 直下の plotfile(prefix + 数字)をステップ順に返す。"""
    run_dir = Path(run_dir)
    items = []
    for p in run_dir.iterdir():
        if p.is_dir() and re.fullmatch(re.escape(prefix) + r"\d+", p.name) and (p / "Header").exists():
            items.append((int(p.name[len(prefix):]), p))
    return [p for _, p in sorted(items)]
