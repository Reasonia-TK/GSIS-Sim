"""ケース定義(TOML)の読み書き・プリセット・導出量(docs/spec.md §4.6)。

- 読み書きは tomlkit で行い、元のファイルのコメント・並び・知らない節を保つ。値を変えたキーだけを
  書き換える(:func:`update_document`)。
- 値の正しさの最終判断は ``gsis-solver check-case`` に任せる。ここの :func:`validate` は画面で
  すぐ分かる誤り(空欄、負の値、境界条件に要る値の不足)だけを見る。
- :func:`derive` は C++ の ``derive_quantities``(``src/config/case_config.cpp``)と同じ式で、
  p_ref・Kn・無次元化の基準量・質量流量の基準を計算する(画面の表示と結果の無次元化に使う)。

Qt には依存しない。
"""

from __future__ import annotations

import copy
import datetime as _dt
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import AoT, Float, Table, Trivia

from .paths import repo_root, to_posix_relative, tube_mesh_dir

K_BOLTZMANN = 1.380649e-23  # J/K

BOUNDARY_TYPES = ("diffuse_wall", "pressure_inlet", "pressure_outlet", "vacuum", "symmetry", "periodic_slab")
BOUNDARY_TYPE_LABELS = {
    "diffuse_wall": "拡散壁",
    "pressure_inlet": "圧力入口",
    "pressure_outlet": "圧力出口",
    "vacuum": "真空",
    "symmetry": "対称面",
    "periodic_slab": "並進周期",
}
# 境界条件の種類ごとの値: 必須 / 任意(C++ の parse_boundary_array と同じ)
BOUNDARY_REQUIRED = {"diffuse_wall": ("temperature",), "pressure_inlet": ("temperature",), "pressure_outlet": ("pressure",)}
BOUNDARY_OPTIONAL = {"diffuse_wall": ("velocity",), "pressure_inlet": ("pressure",)}

REFERENCE_KINDS = ("delta_rp", "Kn", "p_ref")
MODES = ("gsis", "cis")
MODULE_SECTIONS = ("kinetic", "macro", "output")

# [gsis] の既定値(C++ の GsisSection と同じ)
GSIS_DEFAULTS: dict[str, float | int] = {"n_cis": 5, "max_pbc": 10, "cfl_pbc": 500.0, "tolerance": 1e-5}
GSIS_TYPES = {"n_cis": int, "max_pbc": int, "cfl_pbc": float, "tolerance": float}

# [kinetic]・[macro] の既知のキー: 型・既定値・説明(src/kinetic/kinetic_params.hpp、src/macro/macro_params.hpp)
KNOWN_MODULE_KEYS: dict[str, dict[str, tuple[str, Any, str]]] = {
    "kinetic": {
        "pseudo_cfl": ("float", 0.0, "擬似時間刻みの CFL 数(0 で Δt = ∞)"),
        "pseudo_time": ("str", "global", "擬似時間刻みの形(global / velocity)"),
        "limiter": ("str", "venkatakrishnan", "リミッタ(venkatakrishnan / none)"),
        "limiter_k": ("float", 3.0, "Venkatakrishnan の定数 K"),
        "limiter_eps": ("str", "relative", "ε² の決め方(relative / absolute)"),
        "lsq_weight_power": ("float", 0.0, "最小二乗の重み |r|^-p の p(0 で重みなし)"),
        "velocity_chunk": ("int", 0, "速度チャンクの点数(0 で空き VRAM から自動)"),
        "sweep_blocks": ("int", 0, "LU-SGS の空間ブロック数 P(0 で自動)"),
        "cell_ordering": ("str", "rcb", "セルの並べ替え(rcb / rcm / none)"),
        "precision": ("str", "fp32", "分布関数の精度(fp32 / fp64)"),
        "backend": ("str", "cuda", "実行先(cuda / cpu)"),
        "max_iterations": ("int", 100000, "CIS 単独モードの反復の上限"),
        "initial_pressure": ("float", 1.0, "一様な初期状態の圧力(p_ref に対する比)"),
        "initial_temperature": ("float", 1.0, "一様な初期状態の温度(T0 に対する比)"),
        "convergence_u_floor": ("float", 0.01, "収束判定 (59) の u の分母の下限"),
        "wall_relaxation": ("float", 1.0, "拡散壁の流入密度の緩和(1 で緩和なし)"),
        "interface_damping": ("float", 0.5, "LU-SGS のブロック境界の安定化の係数"),
        "launch_cells": ("int", 16384, "1 回のカーネル起動で扱うセル数の上限"),
        "sweep_cells_per_launch": ("int", 4096, "LU-SGS の 1 回の起動で各ブロックが進むセル数の上限"),
    },
    "macro": {
        "advection": ("str", "upwind1", "移流項(upwind1 / upwind2)"),
        "linear_solver": ("str", "gmres", "線形ソルバー(gmres / bicgstab)"),
        "preconditioner": ("str", "ilu0", "前処理(ilu0 / amg / cpr)"),
        "linear_rtol": ("float", 1e-3, "線形ソルバーの相対許容誤差"),
        "linear_max_iterations": ("int", 200, "線形ソルバーの反復の上限"),
        "gmres_restart": ("int", 30, "GMRES の再始動までの反復数"),
        "pbc_tolerance": ("float", 1e-6, "PBC 内側の収束判定 ‖ΔW‖/‖W‖"),
        "relaxation": ("float", 1.0, "更新の緩和係数"),
        "init_iterations": ("int", 50, "NSF のみの初期化での PBC 反復数"),
        "limiter_k": ("float", 5.0, "再構成の Venkatakrishnan の定数 K"),
        "lsq_weight_power": ("float", 0.0, "最小二乗勾配の重み |r|^-p の p"),
        "max_relative_decrease": ("float", 0.5, "1 回の更新で p・T を下げてよい割合の上限"),
        "pressure_floor": ("float", 0.0, "p の下限(p_ref 単位、0 で使わない)"),
        "temperature_floor": ("float", 0.0, "T の下限(T0 単位、0 で使わない)"),
        "reject_linear_residual": ("float", 0.1, "線形ソルバーの相対残差がこれを超えたら更新を捨てる"),
        "standalone_face_state": ("str", "cell", "単独モードの境界面の値(cell / moments / wall_moments)"),
        "boundary_reconstruction": ("str", "limited", "境界面の状態の作り方(limited / walls / cell)"),
        "standalone_boundary_reconstruction": ("str", "cell", "単独モードの境界面の状態(limited / walls / cell)"),
        "boundary_jacobian": ("str", "cell", "境界ヤコビアンの評価(cell / reconstruction)"),
    },
    "output": {},
}

# He(論文 §4.1)
HELIUM = {"molecular_mass": 6.65e-27, "mu_ref": 1.916e-5, "T_ref": 273.15, "omega": 0.66}
PRESET_DELTAS = (1.0, 10.0, 100.0, 1000.0)


class CaseFileError(ValueError):
    """ケース TOML が読めない・形が違う。"""


# ---------------------------------------------------------------------------
# データ
# ---------------------------------------------------------------------------


@dataclass
class Boundary:
    """``[[boundary]]`` の 1 つ。値は SI(K、Pa、m/s)。"""

    patch: str
    type: str
    temperature: float | None = None
    pressure: float | None = None
    velocity: tuple[float, float, float] | None = None

    def copy(self) -> "Boundary":
        return Boundary(self.patch, self.type, self.temperature, self.pressure,
                        None if self.velocity is None else tuple(self.velocity))


def _default_boundaries() -> list[Boundary]:
    return [
        Boundary("inlet", "pressure_inlet", temperature=273.15),
        Boundary("outlet", "vacuum"),
        Boundary("wall", "diffuse_wall", temperature=273.15),
    ]


@dataclass
class CaseModel:
    """ケース定義の中身(画面のフォームと 1 対 1)。

    ``mesh`` は TOML に書く文字列のまま(相対パスなら TOML のディレクトリが基準)。
    ``gsis`` は書くキーだけを持つ(書かないキーはソルバーの既定値 :data:`GSIS_DEFAULTS`)。
    """

    name: str = "tube_delta10"
    mesh: str = "../mesh/tube_hex.pmesh"
    mode: str = "gsis"
    molecular_mass: float = HELIUM["molecular_mass"]
    mu_ref: float = HELIUM["mu_ref"]
    T_ref: float = HELIUM["T_ref"]
    omega: float = HELIUM["omega"]
    L_ref: float = 1e-3
    T0: float = 273.15
    ref_kind: str = "delta_rp"
    ref_value: float = 10.0
    boundaries: list[Boundary] = field(default_factory=_default_boundaries)
    vel_n: list[int] = field(default_factory=lambda: [30, 24, 24])
    vel_range: list[list[float]] = field(default_factory=lambda: [[-10.0, 10.0], [-8.0, 8.0], [-8.0, 8.0]])
    gsis: dict[str, float | int] = field(default_factory=dict)
    kinetic: dict[str, Any] = field(default_factory=dict)
    macro: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "CaseModel":
        return copy.deepcopy(self)

    def gsis_value(self, key: str) -> float | int:
        """[gsis] の実際の値(書いていなければ既定値)。"""
        return self.gsis.get(key, GSIS_DEFAULTS[key])

    def module(self, name: str) -> dict[str, Any]:
        return getattr(self, name)

    @property
    def delta_rp(self) -> float | None:
        return self.ref_value if self.ref_kind == "delta_rp" else None


@dataclass
class CaseDocument:
    """読み込んだケース: ファイルの場所・tomlkit の文書(コメントを含む)・中身。"""

    path: Path | None
    doc: tomlkit.TOMLDocument
    model: CaseModel

    @property
    def directory(self) -> Path | None:
        return None if self.path is None else self.path.parent


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------


def _num(v: Any, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise CaseFileError(f"{where} は数値でなければなりません(値: {v!r})")
    return float(v)


def _opt_num(t: dict, key: str, where: str) -> float | None:
    return None if key not in t else _num(t[key], f"{where}.{key}")


def _table(root: dict, name: str, required: bool = True) -> dict:
    t = root.get(name)
    if t is None:
        if required:
            raise CaseFileError(f"[{name}] がありません")
        return {}
    if not isinstance(t, dict):
        raise CaseFileError(f"{name} は表でなければなりません")
    return t


def model_from_dict(root: dict) -> CaseModel:
    """``tomllib`` / ``tomlkit.unwrap()`` 形式の辞書からモデルを作る。"""
    case = _table(root, "case")
    gas = _table(root, "gas")
    cond = _table(root, "conditions")
    vg = _table(root, "velocity_grid")
    m = CaseModel()
    m.name = str(case.get("name", ""))
    m.mesh = str(case.get("mesh", ""))
    m.mode = str(case.get("mode", "gsis"))
    for k in ("molecular_mass", "mu_ref", "T_ref", "omega"):
        if k not in gas:
            raise CaseFileError(f"gas.{k} がありません")
        setattr(m, k, _num(gas[k], f"gas.{k}"))
    for k in ("L_ref", "T0"):
        if k not in cond:
            raise CaseFileError(f"conditions.{k} がありません")
        setattr(m, k, _num(cond[k], f"conditions.{k}"))
    given = [k for k in REFERENCE_KINDS if k in cond]
    if len(given) != 1:
        raise CaseFileError("conditions には delta_rp・Kn・p_ref のどれか 1 つだけを書いてください")
    m.ref_kind = given[0]
    m.ref_value = _num(cond[given[0]], f"conditions.{given[0]}")

    bl = root.get("boundary", [])
    if not isinstance(bl, list):
        raise CaseFileError("boundary は表の配列 [[boundary]] でなければなりません")
    m.boundaries = []
    for i, b in enumerate(bl):
        if not isinstance(b, dict):
            raise CaseFileError(f"boundary[{i}] が表ではありません")
        vel = b.get("velocity")
        if vel is not None:
            if not isinstance(vel, list) or len(vel) != 3:
                raise CaseFileError(f"boundary[{i}].velocity は 3 成分の配列です")
            vel = tuple(_num(v, f"boundary[{i}].velocity") for v in vel)
        m.boundaries.append(
            Boundary(
                patch=str(b.get("patch", "")),
                type=str(b.get("type", "")),
                temperature=_opt_num(b, "temperature", f"boundary[{i}]"),
                pressure=_opt_num(b, "pressure", f"boundary[{i}]"),
                velocity=vel,
            )
        )
    n = vg.get("n")
    rng = vg.get("range")
    if not (isinstance(n, list) and len(n) == 3):
        raise CaseFileError("velocity_grid.n は 3 つの整数の配列です")
    if not (isinstance(rng, list) and len(rng) == 3 and all(isinstance(r, list) and len(r) == 2 for r in rng)):
        raise CaseFileError("velocity_grid.range は [[下限, 上限] × 3] の配列です")
    m.vel_n = [int(v) for v in n]
    m.vel_range = [[_num(r[0], "velocity_grid.range"), _num(r[1], "velocity_grid.range")] for r in rng]

    g = _table(root, "gsis", required=False)
    m.gsis = {}
    for k, typ in GSIS_TYPES.items():
        if k in g:
            m.gsis[k] = int(g[k]) if typ is int else _num(g[k], f"gsis.{k}")
    for sec in MODULE_SECTIONS:
        t = _table(root, sec, required=False)
        setattr(m, sec, {str(k): v for k, v in t.items() if not isinstance(v, (dict, list))})
    return m


def parse_case_text(text: str) -> tuple[tomlkit.TOMLDocument, CaseModel]:
    try:
        doc = tomlkit.parse(text)
    except Exception as exc:  # tomlkit の解析エラー
        raise CaseFileError(f"TOML として読めません: {exc}") from exc
    return doc, model_from_dict(doc.unwrap())


def load_case(path: str | Path) -> CaseDocument:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    doc, model = parse_case_text(text)
    return CaseDocument(path=path, doc=doc, model=model)


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------


def format_float(v: float) -> str:
    """TOML の浮動小数点の表記。``repr`` の桁を保ち、指数は ``1.0e-5`` の形にする。"""
    v = float(v)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"TOML に書けない値です: {v}")
    s = repr(v)
    if "e" in s or "E" in s:
        mant, exp = s.lower().split("e")
        if "." not in mant:
            mant += ".0"
        sign = "-" if exp.startswith("-") else ""
        exp = exp.lstrip("+-").lstrip("0") or "0"
        return f"{mant}e{sign}{exp}"
    if "." not in s:
        s += ".0"
    return s


def _float_item(v: float) -> Float:
    return Float(float(v), Trivia(), format_float(v))


def _toml_value(v: Any):
    """モデルの値を tomlkit の項目にする(浮動小数点は :func:`format_float` の表記)。"""
    if isinstance(v, bool):
        return tomlkit.item(v)
    if isinstance(v, float):
        return _float_item(v)
    if isinstance(v, (list, tuple)):
        arr = tomlkit.array()
        for x in v:
            arr.append(_toml_value(x))
        return arr
    return tomlkit.item(v)


def _same(old: Any, new: Any) -> bool:
    """値として同じか(整数と浮動小数点は区別する。型が違えば書き直す)。"""
    if isinstance(old, bool) or isinstance(new, bool):
        return type(old) is type(new) and old == new
    if isinstance(old, (list, tuple)) and isinstance(new, (list, tuple)):
        return len(old) == len(new) and all(_same(a, b) for a, b in zip(old, new))
    if isinstance(old, float) and isinstance(new, float):
        return old == new
    if isinstance(old, int) and isinstance(new, int):
        return old == new
    if isinstance(old, str) and isinstance(new, str):
        return old == new
    return False


def _plain(item: Any) -> Any:
    return item.unwrap() if hasattr(item, "unwrap") else item


def _set(table, key: str, value: Any, comment: str | None = None) -> None:
    """値が違うときだけ書き換える(行末のコメントは tomlkit が引き継ぐ)。"""
    if key in table and _same(_plain(table[key]), value):
        return
    item = _toml_value(value)
    if comment and key not in table:
        item.comment(comment)
    table[key] = item


def _del(table, key: str) -> None:
    if key in table:
        del table[key]


def _ensure_table(doc, name: str) -> Table:
    if name not in doc:
        doc.add(name, tomlkit.table())
    return doc[name]


def _sync_scalars(table, values: dict[str, Any]) -> None:
    for k in [k for k in table.keys() if k not in values]:
        del table[k]
    for k, v in values.items():
        _set(table, k, v)


def _boundary_table(t, b: Boundary) -> None:
    _set(t, "patch", b.patch)
    _set(t, "type", b.type)
    allowed = set(BOUNDARY_REQUIRED.get(b.type, ())) | set(BOUNDARY_OPTIONAL.get(b.type, ()))
    for key in ("temperature", "pressure", "velocity"):
        v = getattr(b, key)
        if key in allowed and v is not None:
            _set(t, key, [float(x) for x in v] if key == "velocity" else float(v))
        else:
            _del(t, key)


# 新しく作る文書の行末のコメント(既存の文書では元のコメントを保つ)
_UNIT_COMMENTS = {
    ("gas", "molecular_mass"): "kg",
    ("gas", "mu_ref"): "Pa s(T_ref での粘性)",
    ("gas", "T_ref"): "K",
    ("gas", "omega"): "粘性のべき指数",
    ("conditions", "L_ref"): "m(管半径)",
    ("conditions", "T0"): "K",
    ("conditions", "delta_rp"): "p_in = δ μ0 √(2RT0) / L_ref",
    ("conditions", "Kn"): "式 (8) を基準状態で評価した値",
    ("conditions", "p_ref"): "Pa",
    ("velocity_grid", "range"): "単位 sqrt(R*T0)",
}


def update_document(doc: tomlkit.TOMLDocument, m: CaseModel) -> tomlkit.TOMLDocument:
    """``doc`` をモデルの値に合わせて書き換える(その場で変更し、同じ文書を返す)。

    値が同じキーには触れないので、元のコメントと書き方が残る。知らない節・キーもそのまま残す
    (C++ 側が知らないキーをエラーにするので、画面では [kinetic]・[macro]・[output] の表で消せる)。
    """
    case = _ensure_table(doc, "case")
    _set(case, "name", m.name)
    _set(case, "mesh", m.mesh)
    _set(case, "mode", m.mode)

    gas = _ensure_table(doc, "gas")
    for k in ("molecular_mass", "mu_ref", "T_ref", "omega"):
        _set(gas, k, float(getattr(m, k)), _UNIT_COMMENTS.get(("gas", k)))

    cond = _ensure_table(doc, "conditions")
    _set(cond, "L_ref", float(m.L_ref), _UNIT_COMMENTS.get(("conditions", "L_ref")))
    _set(cond, "T0", float(m.T0), _UNIT_COMMENTS.get(("conditions", "T0")))
    for k in REFERENCE_KINDS:
        if k != m.ref_kind:
            _del(cond, k)
    _set(cond, m.ref_kind, float(m.ref_value), _UNIT_COMMENTS.get(("conditions", m.ref_kind)))

    if "boundary" in doc and isinstance(doc["boundary"], AoT):
        aot = doc["boundary"]
        for i, b in enumerate(m.boundaries):
            if i < len(aot):
                _boundary_table(aot[i], b)
            else:
                t = tomlkit.table()
                _boundary_table(t, b)
                aot.append(t)
        while len(aot) > len(m.boundaries):
            del aot[len(aot) - 1]
    else:
        if "boundary" in doc:
            del doc["boundary"]
        aot = tomlkit.aot()
        for b in m.boundaries:
            t = tomlkit.table()
            _boundary_table(t, b)
            aot.append(t)
        doc.add("boundary", aot)

    vg = _ensure_table(doc, "velocity_grid")
    _set(vg, "n", [int(v) for v in m.vel_n])
    _set(vg, "range", [[float(a), float(b)] for a, b in m.vel_range], _UNIT_COMMENTS.get(("velocity_grid", "range")))

    gsis_vals = {k: (int(v) if GSIS_TYPES[k] is int else float(v)) for k, v in m.gsis.items()}
    if gsis_vals or "gsis" in doc:
        _sync_scalars(_ensure_table(doc, "gsis"), gsis_vals)

    for sec in MODULE_SECTIONS:
        vals = m.module(sec)
        if vals:
            _sync_scalars(_ensure_table(doc, sec), vals)
        elif sec in doc:
            del doc[sec]
    return doc


def new_document(m: CaseModel, header: str | None = None) -> tomlkit.TOMLDocument:
    """新しい文書(見出しのコメント付き)。"""
    doc = tomlkit.document()
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    for line in (header or f"GSIS-PBC のケース定義(gsis-gui で作成、{stamp})").splitlines():
        doc.add(tomlkit.comment(line))
    doc.add(tomlkit.nl())
    return update_document(doc, m)


def render_case(m: CaseModel, base: tomlkit.TOMLDocument | None = None) -> str:
    """モデルを TOML の文字列にする。``base`` があればそのコピーを書き換える(元の文書は変えない)。"""
    doc = new_document(m) if base is None else update_document(copy.deepcopy(base), m)
    text = tomlkit.dumps(doc)
    return text if text.endswith("\n") else text + "\n"


def save_case(path: str | Path, m: CaseModel, base: tomlkit.TOMLDocument | None = None) -> CaseDocument:
    """TOML に保存し、保存した内容の :class:`CaseDocument` を返す(UTF-8、改行は LF)。"""
    path = Path(path)
    text = render_case(m, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    doc, model = parse_case_text(text)
    return CaseDocument(path=path, doc=doc, model=model)


# ---------------------------------------------------------------------------
# メッシュのパス
# ---------------------------------------------------------------------------


def resolve_mesh(mesh: str, case_dir: Path | None) -> Path:
    """TOML の ``case.mesh`` を絶対パスにする(相対パスは TOML のディレクトリが基準。C++ と同じ)。"""
    p = Path(mesh)
    if p.is_absolute() or case_dir is None:
        return p
    return (Path(case_dir) / p).resolve()


def rebase_mesh(mesh: str, old_dir: Path | None, new_dir: Path) -> str:
    """TOML の置き場所が変わるとき、``case.mesh`` を新しい場所からの相対パスに直す。

    相対パスで元の場所(``old_dir``)が分からなければ、そのまま返す。
    """
    if not mesh:
        return mesh
    p = Path(mesh)
    if not p.is_absolute():
        if old_dir is None:
            return mesh
        p = (Path(old_dir) / p).resolve()
    return to_posix_relative(p, Path(new_dir))


def is_tube_case(m: CaseModel, case_dir: Path | None) -> bool:
    """論文 §4.1 の管のメッシュか(gsis_tools.geometry.tube で作ったメッシュか、ファイル名が tube で始まる)。

    論文の値(Table 1・Fig.3)を重ねてよいかの判断に使う。メッシュが読めなければファイル名だけで決める。
    (smoke の箱メッシュは metadata に "tube=true" とあるが管ではないので、source の文字列は厳密に見る)
    """
    try:
        from ..pmesh import read_header

        p = resolve_mesh(m.mesh, case_dir)
        md = read_header(p).get("metadata", {}) or {}
        return "geometry.tube" in str(md.get("source", "")) or p.name.lower().startswith("tube")
    except Exception:
        return Path(m.mesh).name.lower().startswith("tube")


# ---------------------------------------------------------------------------
# 検査と導出量
# ---------------------------------------------------------------------------


def validate(m: CaseModel) -> list[str]:
    """画面ですぐ分かる誤りの一覧(空なら問題なし)。最終的な検査は ``check-case`` が行う。"""
    errs: list[str] = []
    if not m.name.strip():
        errs.append("case.name が空です")
    if not m.mesh.strip():
        errs.append("case.mesh が空です")
    if m.mode not in MODES:
        errs.append(f"case.mode は {' / '.join(MODES)} です")
    for k in ("molecular_mass", "mu_ref", "T_ref", "omega", "L_ref", "T0", "ref_value"):
        v = getattr(m, k)
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            errs.append(f"{k} は正の数にしてください(値: {v})")
    if m.ref_kind not in REFERENCE_KINDS:
        errs.append(f"基準の与え方は {' / '.join(REFERENCE_KINDS)} です")
    if not m.boundaries:
        errs.append("境界条件が 1 つもありません")
    seen: set[str] = set()
    for i, b in enumerate(m.boundaries):
        where = f"境界条件 {i + 1}({b.patch or '名前なし'})"
        if not b.patch.strip():
            errs.append(f"{where}: パッチ名が空です")
        if b.patch in seen:
            errs.append(f"{where}: パッチ {b.patch} が重複しています")
        seen.add(b.patch)
        if b.type not in BOUNDARY_TYPES:
            errs.append(f"{where}: 種類 {b.type!r} は知りません")
            continue
        for key in BOUNDARY_REQUIRED.get(b.type, ()):
            if getattr(b, key) is None:
                errs.append(f"{where}: {key} が要ります")
        if b.temperature is not None and not b.temperature > 0:
            errs.append(f"{where}: temperature は正にしてください")
        if b.pressure is not None:
            if b.type == "pressure_outlet" and b.pressure < 0:
                errs.append(f"{where}: pressure は 0 以上にしてください")
            elif b.type != "pressure_outlet" and not b.pressure > 0:
                errs.append(f"{where}: pressure は正にしてください")
        if b.type == "pressure_inlet" and b.pressure is None and m.ref_kind != "delta_rp":
            errs.append(f"{where}: pressure を省略できるのは delta_rp を与えたときだけです")
    if len(m.vel_n) != 3 or any(int(n) < 1 for n in m.vel_n):
        errs.append("速度格子の点数は 1 以上にしてください")
    for a, (lo, hi) in enumerate(m.vel_range):
        if not lo < hi:
            errs.append(f"速度格子の範囲(軸 {a})で下限 < 上限 になっていません")
    for k, v in m.gsis.items():
        if not v > 0:
            errs.append(f"gsis.{k} は正にしてください")
    return errs


@dataclass(frozen=True)
class Derived:
    """導出量(C++ の DerivedQuantities と同じ定義。SI)。"""

    R: float
    mu0: float
    most_probable_speed: float  # √(2 R T0)
    p_ref: float  # = p_in
    rho_ref: float
    v_ref: float  # √(R T0)
    Kn: float
    delta: float  # √π / (2 Kn)
    mean_free_path: float
    has_p_in: bool  # delta_rp で与えた(管ケース)
    mass_flow_scale: float  # √π L² p_in / √(2 R T0) [kg/s]
    mass_flux_ref: float  # ρ_ref √(R T0) [kg/(m² s)]
    heat_flux_ref: float  # p_ref √(R T0) [W/m²]
    L_ref: float
    T0: float

    @property
    def nd_to_Mdot(self) -> float:
        """ソルバーの無次元の質量流量(ρ_ref √(RT0) L_ref² 単位)→ Ṁ の係数(= √(2/π))。"""
        return self.mass_flux_ref * self.L_ref**2 / self.mass_flow_scale


def derive(m: CaseModel) -> Derived:
    R = K_BOLTZMANN / m.molecular_mass
    mu0 = m.mu_ref * (m.T0 / m.T_ref) ** m.omega
    c = math.sqrt(2.0 * R * m.T0)
    kn_factor = mu0 / m.L_ref * math.sqrt(math.pi * R * m.T0 / 2.0)
    if m.ref_kind == "delta_rp":
        p_ref = m.ref_value * mu0 * c / m.L_ref
    elif m.ref_kind == "Kn":
        p_ref = kn_factor / m.ref_value
    else:
        p_ref = m.ref_value
    Kn = kn_factor / p_ref
    v_ref = math.sqrt(R * m.T0)
    rho_ref = p_ref / (R * m.T0)
    return Derived(
        R=R, mu0=mu0, most_probable_speed=c, p_ref=p_ref, rho_ref=rho_ref, v_ref=v_ref, Kn=Kn,
        delta=math.sqrt(math.pi) / (2.0 * Kn), mean_free_path=Kn * m.L_ref, has_p_in=m.ref_kind == "delta_rp",
        mass_flow_scale=math.sqrt(math.pi) * m.L_ref**2 * p_ref / c, mass_flux_ref=rho_ref * v_ref,
        heat_flux_ref=p_ref * v_ref, L_ref=m.L_ref, T0=m.T0,
    )


def velocity_points(m: CaseModel) -> int:
    return int(m.vel_n[0]) * int(m.vel_n[1]) * int(m.vel_n[2])


def velocity_spacing(m: CaseModel) -> list[float]:
    return [(hi - lo) / max(int(n), 1) for (lo, hi), n in zip(m.vel_range, m.vel_n)]


def distribution_bytes(m: CaseModel, n_cells: int, precision: str = "fp32") -> int:
    """分布関数 f を 1 本持つのに要るバイト数(セル × 速度点 × 4 または 8)。"""
    return int(n_cells) * velocity_points(m) * (8 if precision == "fp64" else 4)


# ---------------------------------------------------------------------------
# プリセット
# ---------------------------------------------------------------------------


def preset_label(delta: float) -> str:
    return f"He 管 δ_rp = {delta:g}"


def tube_preset(delta: float, *, mode: str = "gsis", mesh: str | None = None) -> CaseModel:
    """論文 §4.1 の管ケース(He、δ_rp、管半径 1 mm、30×24×24 の速度格子、GSIS の設定は論文どおり)。

    ``mesh`` を省略すると ``cases/tube_vacuum/mesh/tube_hex.pmesh`` の絶対パス
    (保存するときに TOML の場所からの相対パスに直す)。
    """
    m = CaseModel()
    m.name = f"tube_delta{delta:g}" + ("_cis" if mode == "cis" else "")
    m.mesh = mesh if mesh is not None else (tube_mesh_dir(repo_root()) / "tube_hex.pmesh").as_posix()
    m.mode = mode
    for k, v in HELIUM.items():
        setattr(m, k, v)
    m.L_ref = 1e-3
    m.T0 = 273.15
    m.ref_kind = "delta_rp"
    m.ref_value = float(delta)
    m.boundaries = _default_boundaries()
    m.vel_n = [30, 24, 24]
    m.vel_range = [[-10.0, 10.0], [-8.0, 8.0], [-8.0, 8.0]]
    m.gsis = dict(GSIS_DEFAULTS)
    return m


def parse_scalar(text: str, expected: str | None = None) -> Any:
    """[kinetic] などの値の文字列を解釈する。``expected`` は "int" / "float" / "bool" / "str"。

    型が分からなければ C++ の ``--set`` と同じ順(整数 → 実数 → 真偽値 → 文字列)で解釈する。
    """
    s = text.strip()
    if expected == "str":
        return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s
    if expected == "bool":
        if s.lower() in ("true", "1", "yes"):
            return True
        if s.lower() in ("false", "0", "no"):
            return False
        raise ValueError(f"真偽値ではありません: {text}")
    if expected == "int":
        return int(s)
    if expected == "float":
        return float(s)
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if s in ("true", "false"):
        return s == "true"
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s


def scalar_to_text(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return format_float(v)
    return str(v)


def scalar_type(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return "str"
