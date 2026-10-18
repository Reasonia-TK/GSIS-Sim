"""面の情報(種類・面積・重心・外向き法線・半径など)と、条件による面の選択。

外向き法線は、面上の点から法線の向きに少し進んだ点が体積の内か外か(``gmsh.model.isInside``)で
決める(gmsh の ``getNormal`` は面の向きによって外向きにならないことがあるため)。円柱・球の半径と
軸・中心は主曲率から求める。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .errors import ToolFailure

# gmsh の面の種類 → 短い名前
_TYPE_MAP = {
    "plane": "plane",
    "cylinder": "cylinder",
    "cone": "cone",
    "sphere": "sphere",
    "torus": "torus",
    "bsplinesurface": "bspline",
    "bspline surface": "bspline",
    "nurb": "bspline",
    "beziersurface": "bezier",
    "bezier surface": "bezier",
    "surfaceofrevolution": "revolution",
    "surface of revolution": "revolution",
    "surfaceofextrusion": "extrusion",
    "surface of extrusion": "extrusion",
}
FACE_TYPES = ("plane", "cylinder", "cone", "sphere", "torus", "bspline", "bezier", "revolution", "extrusion", "other")
_NO_COLOR = (0, 0, 255, 0)  # gmsh が色のない面に返す値


def short_type(gmsh_type: str) -> str:
    key = gmsh_type.strip().lower()
    return _TYPE_MAP.get(key, _TYPE_MAP.get(key.replace(" ", ""), "other"))


def _unit(v) -> np.ndarray | None:
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else None


@dataclass
class FaceInfo:
    tag: int
    type: str
    gmsh_type: str
    area: float
    centroid: np.ndarray
    bbox: np.ndarray
    volumes: list[int]
    point: np.ndarray | None = None  # 面上の点(法線などを求めた点)
    normal: np.ndarray | None = None  # 外向き法線(体積が 1 つのとき。平面なら面全体で同じ)
    raw_normal: np.ndarray | None = None  # 面の向きのままの法線(向きは不定)
    radius: float | None = None
    axis: np.ndarray | None = None  # 円柱の軸の向き
    center: np.ndarray | None = None  # 球の中心、円柱の軸上の点
    color: tuple[int, ...] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def boundary(self) -> bool:
        """体積 1 つだけに属する面(計算領域の境界)。"""
        return len(self.volumes) == 1


def _interior_uv(gmsh, tag: int, n: int = 7) -> list[float]:
    (u0, v0), (u1, v1) = gmsh.model.getParametrizationBounds(2, tag)
    um, vm = 0.5 * (u0 + u1), 0.5 * (v0 + v1)
    if gmsh.model.isInside(2, tag, [um, vm], parametric=True):
        return [um, vm]
    for i in range(n):
        for j in range(n):
            u = u0 + (u1 - u0) * (i + 0.5) / n
            v = v0 + (v1 - v0) * (j + 0.5) / n
            if gmsh.model.isInside(2, tag, [u, v], parametric=True):
                return [u, v]
    # 細い面など: 重心に最も近い面上の点
    c = gmsh.model.occ.getCenterOfMass(2, tag)
    _, par = gmsh.model.getClosestPoint(2, tag, list(c))
    return [float(par[0]), float(par[1])]


def _outward(gmsh, vol: int, p: np.ndarray, n: np.ndarray, eps: float) -> np.ndarray | None:
    """n を体積 vol の外向きにそろえる(判定できなければ None)。"""
    for e in (eps, eps * 1e-2, eps * 10):
        a = gmsh.model.isInside(3, vol, list(p + e * n))
        b = gmsh.model.isInside(3, vol, list(p - e * n))
        if a and not b:
            return -n
        if b and not a:
            return n
    return None


def _shell_signs(gmsh) -> dict[int, dict[int, int]]:
    """面ごとの、属する体積の殻の中での向き(+1 / -1)。"""
    out: dict[int, dict[int, int]] = {}
    for _, v in gmsh.model.getEntities(3):
        for _, st in gmsh.model.getBoundary([(3, v)], combined=False, oriented=True):
            out.setdefault(abs(int(st)), {})[int(v)] = 1 if st > 0 else -1
    return out


def _fast_outward(gmsh, tag: int, uv, sign: int) -> np.ndarray | None:
    """外向き法線 = 殻の中での向き × 曲面のパラメータの法線(∂/∂u × ∂/∂v)。

    gmsh の ``getNormal`` は、面が最初に作られたときの向き(押し出す前の断面など)を保つので外向きとは限らない。
    殻の中での向きの符号とパラメータの法線の積は外向きになる(管・押し出し・回転・鏡映・分割・ブーリアン・
    STEP の 11 種類・312 面で、点が体積の内か外かで決めた向きと一致した)。
    """
    d = np.asarray(gmsh.model.getDerivative(2, tag, list(uv)), dtype=float).reshape(-1, 3)
    if d.shape[0] < 2:
        return None
    n = _unit(np.cross(d[0], d[1]))
    return None if n is None else sign * n


VERIFY_SAMPLES = 12  # 速い方法の向きを、点の内外の判定で確かめる面の数


def compute_face_infos(gmsh, diag: float) -> dict[int, FaceInfo]:
    """現在のモデルの全ての面の情報。

    外向き法線は速い方法(:func:`_fast_outward`)で求め、いくつかの面を点の内外の判定(:func:`_outward`)で
    確かめる。1 つでも食い違えば、すべての面を点の内外の判定で求め直す。
    """
    occ = gmsh.model.occ
    eps = 1e-4 * max(diag, 1e-300)
    signs = _shell_signs(gmsh)
    out: dict[int, FaceInfo] = {}
    pending: list[tuple[FaceInfo, int, np.ndarray]] = []  # (面, 体積, 面の向きの法線)
    for _, tag in gmsh.model.getEntities(2):
        gtype = gmsh.model.getType(2, tag)
        typ = short_type(gtype)
        area = float(occ.getMass(2, tag))
        c = np.asarray(occ.getCenterOfMass(2, tag), dtype=float)
        bb = np.asarray(gmsh.model.getBoundingBox(2, tag), dtype=float)
        up, _ = gmsh.model.getAdjacencies(2, tag)
        vols = sorted(int(v) for v in up)
        info = FaceInfo(tag, typ, gtype, area, c, bb, vols)
        try:
            col = tuple(int(x) for x in gmsh.model.getColor(2, tag))
            if col != _NO_COLOR:
                info.color = col
        except Exception:  # pragma: no cover
            pass
        try:
            uv = _interior_uv(gmsh, tag)
            p = np.asarray(gmsh.model.getValue(2, tag, uv), dtype=float)
            n = _unit(gmsh.model.getNormal(tag, uv))
            info.point = p
            info.raw_normal = n
            if n is not None and len(vols) == 1:
                sg = signs.get(tag, {}).get(vols[0])
                fast = _fast_outward(gmsh, tag, uv, sg) if sg is not None else None
                if fast is None:
                    info.normal = _outward(gmsh, vols[0], p, n, eps)
                else:
                    info.normal = fast
                    pending.append((info, vols[0], n))
            elif n is not None and not vols:
                info.normal = n  # 体積に属さない面: 面の向きのまま
            if typ in ("cylinder", "sphere") and n is not None:
                k1, k2, d1, d2 = gmsh.model.getPrincipalCurvatures(tag, uv)
                k1, k2 = float(np.ravel(k1)[0]), float(np.ravel(k2)[0])
                d1, d2 = np.ravel(d1)[:3], np.ravel(d2)[:3]
                if typ == "sphere":
                    k = 0.5 * (k1 + k2)
                    if abs(k) > 0:
                        info.radius = 1.0 / abs(k)
                        info.center = p + n / k
                else:
                    # 曲率が 0 の向きが軸、もう一方の曲率の逆数が半径
                    if abs(k1) < abs(k2):
                        dz, kc = d1, k2
                    else:
                        dz, kc = d2, k1
                    if abs(kc) > 0:
                        info.radius = 1.0 / abs(kc)
                        info.axis = _unit(dz)
                        info.center = p + n / kc
        except Exception:  # pragma: no cover - 退化した面など
            pass
        out[tag] = info
    # 速い方法の向きを、いくつかの面で確かめる(食い違えば、すべて点の内外の判定で求め直す)
    if pending:
        idx = np.unique(np.linspace(0, len(pending) - 1, min(VERIFY_SAMPLES, len(pending))).round().astype(int))
        mismatch = False
        for i in idx:
            info, v, n = pending[int(i)]
            ref = _outward(gmsh, v, info.point, n, eps)
            if ref is not None and float(np.dot(ref, info.normal)) < 0.5:
                mismatch = True
                break
        if mismatch:
            for info, v, n in pending:
                info.normal = _outward(gmsh, v, info.point, n, eps)
    return out


# ---------------------------------------------------------------------------
# 選択
# ---------------------------------------------------------------------------


def _angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    a = _unit(a)
    b = _unit(b)
    if a is None or b is None:
        return 180.0
    return math.degrees(math.atan2(float(np.linalg.norm(np.cross(a, b))), float(np.dot(a, b))))


def _point_on_face(gmsh, tag: int, pt: np.ndarray, tol: float) -> bool:
    cp, par = gmsh.model.getClosestPoint(2, tag, list(pt))
    if float(np.linalg.norm(np.asarray(cp) - pt)) > tol:
        return False
    return bool(gmsh.model.isInside(2, tag, [float(par[0]), float(par[1])], parametric=True))


SELECTOR_KEYS = (
    "faces", "volumes", "type", "on_plane", "normal", "in_box", "contains_point", "radius", "axis",
    "patch", "unassigned", "color", "area_min", "area_max", "tol", "angle_tol_deg", "include_internal",
)


def select_faces(
    gmsh,
    infos: dict[int, FaceInfo],
    sel: dict[str, Any] | None,
    *,
    patch_of: dict[int, str],
    diag: float,
    resolve_volumes=None,
) -> tuple[list[int], dict[str, int]]:
    """条件(すべて AND)に合う面の番号と、条件ごとの残りの数。

    既定では計算領域の境界の面(体積 1 つに属する面)だけを対象にする。
    """
    sel = {k: v for k, v in (sel or {}).items() if v is not None}
    unknown = set(sel) - set(SELECTOR_KEYS)
    if unknown:
        raise ToolFailure(f"選択の条件に未知の項目があります: {sorted(unknown)}", hint=f"使える項目: {', '.join(SELECTOR_KEYS)}")
    tol = float(sel.get("tol") or 1e-6 * max(diag, 1e-300))
    atol = float(sel.get("angle_tol_deg") or 1.0)
    include_internal = bool(sel.get("include_internal", False))
    cand = [t for t, f in infos.items() if (f.boundary or include_internal and len(f.volumes) > 1)]
    counts: dict[str, int] = {"候補": len(cand)}

    def keep(name: str, pred) -> None:
        nonlocal cand
        cand = [t for t in cand if pred(infos[t])]
        counts[name] = len(cand)

    if "faces" in sel:
        want = [int(t) for t in sel["faces"]]
        missing = [t for t in want if t not in infos]
        if missing:
            raise ToolFailure(f"面 {missing} はありません", hint="list_faces で番号を確かめてください")
        not_bnd = [t for t in want if t not in cand]
        if not_bnd:
            raise ToolFailure(
                f"面 {not_bnd} は計算領域の境界の面ではありません(体積 {[infos[t].volumes for t in not_bnd]} に属する)",
                hint="2 つの体積に挟まれた面は内部の面です。境界パッチにはできません",
            )
        ws = set(want)
        keep("faces", lambda f: f.tag in ws)
    if "volumes" in sel:
        vols = set(resolve_volumes(sel["volumes"])) if resolve_volumes else {int(v) for v in sel["volumes"]}
        keep("volumes", lambda f: bool(vols.intersection(f.volumes)))
    if "type" in sel:
        t = str(sel["type"]).lower()
        if t not in FACE_TYPES:
            raise ToolFailure(f"面の種類 {t!r} は未知です", hint=f"使える種類: {', '.join(FACE_TYPES)}")
        keep("type", lambda f: f.type == t)
    if "on_plane" in sel:
        p0 = np.asarray(sel["on_plane"]["point"], dtype=float)
        n0 = _unit(sel["on_plane"]["normal"])
        if n0 is None:
            raise ToolFailure("on_plane の normal が 0 ベクトルです")

        def on_plane(f: FaceInfo) -> bool:
            if f.type != "plane":
                return False
            if abs(float(np.dot(n0, f.centroid - p0))) > tol:
                return False
            n = f.normal if f.normal is not None else f.raw_normal
            if n is None:
                return True
            ang = _angle_deg(n, n0)
            return min(ang, 180.0 - ang) <= atol

        keep("on_plane", on_plane)
    if "normal" in sel:
        d = np.asarray(sel["normal"], dtype=float)
        if _unit(d) is None:
            raise ToolFailure("normal が 0 ベクトルです")
        keep("normal", lambda f: f.type == "plane" and f.normal is not None and _angle_deg(f.normal, d) <= atol)
    if "in_box" in sel:
        b = np.asarray(sel["in_box"], dtype=float)
        lo, hi = b[:3] - tol, b[3:] + tol
        keep("in_box", lambda f: bool(np.all(f.bbox[:3] >= lo) and np.all(f.bbox[3:] <= hi)))
    if "radius" in sel:
        r = float(sel["radius"])
        keep("radius", lambda f: f.radius is not None and abs(f.radius - r) <= max(tol, 1e-6 * abs(r)))
    if "axis" in sel:
        a = np.asarray(sel["axis"], dtype=float)

        def ax_ok(f: FaceInfo) -> bool:
            if f.axis is None:
                return False
            ang = _angle_deg(f.axis, a)
            return min(ang, 180.0 - ang) <= atol

        keep("axis", ax_ok)
    if "patch" in sel:
        pn = str(sel["patch"])
        keep("patch", lambda f: patch_of.get(f.tag) == pn)
    if sel.get("unassigned"):
        keep("unassigned", lambda f: f.tag not in patch_of)
    if "color" in sel:
        col = tuple(int(c) for c in sel["color"])
        keep("color", lambda f: f.color is not None and f.color[: len(col)] == col)
    if "area_min" in sel:
        keep("area_min", lambda f: f.area >= float(sel["area_min"]))
    if "area_max" in sel:
        keep("area_max", lambda f: f.area <= float(sel["area_max"]))
    if "contains_point" in sel:
        pt = np.asarray(sel["contains_point"], dtype=float)
        bb_tol = tol
        keep(
            "contains_point",
            lambda f: bool(np.all(pt >= f.bbox[:3] - bb_tol) and np.all(pt <= f.bbox[3:] + bb_tol))
            and _point_on_face(gmsh, f.tag, pt, tol),
        )
    return sorted(cand), counts


def face_row(f: FaceInfo, patch: str | None, rnd) -> dict[str, Any]:
    """list_faces の 1 行(小さく保つ)。"""
    row: dict[str, Any] = {"id": f.tag, "type": f.type, "area": rnd(f.area, 8), "c": rnd(f.centroid)}
    if f.normal is not None and f.type == "plane":
        row["n"] = rnd(f.normal, 4)
    if f.radius is not None:
        row["r"] = rnd(f.radius)
    if f.axis is not None:
        row["axis"] = rnd(f.axis, 4)
    if f.type == "sphere" and f.center is not None:
        row["center"] = rnd(f.center)
    row["bbox"] = rnd(f.bbox)
    if not f.boundary:
        row["volumes"] = f.volumes
    if f.color is not None:
        row["color"] = list(f.color)
    if patch:
        row["patch"] = patch
    return row
