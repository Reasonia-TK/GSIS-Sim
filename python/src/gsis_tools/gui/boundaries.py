"""面の境界割り当て、入力検査と幾何の指紋による引き継ぎ(Qt なし)。"""

from __future__ import annotations

import copy
import math
import re

import numpy as np

KINDS = {"diffuse_wall": "拡散壁", "maxwell_wall": "Maxwell 壁", "pressure_inlet": "圧力入口",
         "pressure_outlet": "圧力出口", "vacuum": "真空", "mass_flow_inlet": "質量流量入口", "symmetry": "対称面"}
FIELDS = {"diffuse_wall": ("temperature", "velocity"),
          "maxwell_wall": ("temperature", "accommodation", "velocity"),
          "pressure_inlet": ("pressure", "temperature", "tangential_velocity"),
          "pressure_outlet": ("pressure",), "vacuum": (),
          "mass_flow_inlet": ("mass_flow", "temperature", "tangential_velocity"), "symmetry": ()}
DEFAULT_PATCH = "default_wall"
NSF_MAXWELL_WARNING = ("NSF の α < 1 の壁は半空間 Maxwell 混合の PBC 境界です。"
                       "連続体のすべり・温度跳躍の定量精度は保証されません。"
                       "完全拡散壁の既定 NSF すべり係数を α < 1 に流用しないでください")


def boundary_notes(settings, boundaries):
    """境界の組み合わせによる注意。基準圧力は入口の指定圧力とは区別する。"""
    notes = []
    if any(b.get("type") == "mass_flow_inlet" for b in boundaries):
        notes.append(f"質量流量入口の初期 p_b = p_ref = {settings.p_ref:.6g} Pa。"
                     "パッチ全体の目標流量に合わせて反復中に圧力を更新します")
        if not any(b.get("type") in ("pressure_inlet", "pressure_outlet") for b in boundaries):
            notes.append("圧力を指定する境界がないため、設定画面の基準圧力 p_ref を初期値として保持します。"
                         "質量流量からの自動推定はしません。真空は指定圧力境界に数えません")
    if settings.mode == "nsf" and any(b.get("type") == "maxwell_wall" and b.get("accommodation", 1) < 1
                                      for b in boundaries):
        notes.append(NSF_MAXWELL_WARNING)
    return notes


def fingerprint(face):
    return {k: copy.deepcopy(face.get(k)) for k in ("centroid", "area", "normal", "type")}


def same_face(a, b, length):
    """SI の相対許容差。番号は照合に使わず、法線の欠測同士だけを一致とする。"""
    try:
        if a.get("type") != b.get("type"):
            return False
        if not math.isclose(a["area"], b["area"], rel_tol=1e-6, abs_tol=length**2 * 1e-10):
            return False
        if np.linalg.norm(np.asarray(a["centroid"]) - b["centroid"]) > length * 1e-6:
            return False
        na, nb = a.get("normal"), b.get("normal")
        return (na is None and nb is None) if na is None or nb is None else np.linalg.norm(np.asarray(na) - nb) < 1e-6
    except (KeyError, TypeError, ValueError):
        return False


def remap_boundaries(boundaries, faces):
    """一意に照合できた面だけを引き継ぐ。不明・重複は勝手に割り当てない。"""
    if not faces:
        return [], [f"{b['patch']}: 面 {f}" for b in boundaries for f in b.get("faces", [])]
    centres = np.asarray([f["centroid"] for f in faces])
    length = max(float(np.linalg.norm(np.ptp(centres, axis=0))), math.sqrt(sum(f["area"] for f in faces)), 1e-30)
    result, lost, used = [], [], set()
    for old in boundaries:
        b = copy.deepcopy(old)
        b["faces"], b["fingerprints"] = [], []
        fps = old.get("fingerprints", [])
        for i, tag in enumerate(old.get("faces", [])):
            candidates = [f for f in faces if i < len(fps) and same_face(fps[i], f, length)]
            if len(candidates) == 1 and candidates[0]["id"] not in used:
                f = candidates[0]
                used.add(f["id"])
                b["faces"].append(f["id"])
                b["fingerprints"].append(fingerprint(f))
            else:
                lost.append(f"{old['patch']}: 面 {tag}")
        if b["faces"] or not old.get("automatic"):
            result.append(b)
    return result, lost


def with_default(boundaries, faces, temperature):
    out = copy.deepcopy([b for b in boundaries if not b.get("automatic")])
    assigned = {t for b in out for t in b.get("faces", [])}
    missing = [f for f in faces if f["id"] not in assigned]
    if missing:
        out.append(dict(patch=DEFAULT_PATCH, type="diffuse_wall", temperature=temperature, automatic=True,
                        faces=[f["id"] for f in missing], fingerprints=[fingerprint(f) for f in missing]))
    return out


def validate_boundary(b, faces):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", b.get("patch", "")):
        raise ValueError("パッチ名は英字で始まる英数字・_・- にしてください")
    kind = b.get("type")
    if kind not in KINDS:
        raise ValueError("境界の種類を選んでください")
    ids = b.get("faces", [])
    by_id = {f["id"]: f for f in faces}
    if not ids or len(set(ids)) != len(ids) or any(i not in by_id for i in ids):
        raise ValueError("割り当てる面を 1 つ以上選んでください")
    for key in FIELDS[kind]:
        value = b.get(key)
        if key == "velocity":
            if value is not None and (not isinstance(value, (list, tuple)) or len(value) != 3
                                      or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in value)):
                raise ValueError("壁速度は有限の x, y, z 成分にしてください")
        elif key == "tangential_velocity":
            if value is not None and value not in ("extrapolate", "zero"):
                raise ValueError("接線速度は extrapolate または zero を選んでください")
        elif key == "pressure" and kind == "pressure_outlet":
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("出口圧力は有限の 0 以上の数にしてください")
        elif not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} は有限の正の数にしてください")
        elif key == "accommodation" and value > 1:
            raise ValueError("適応係数 α は 0 < α ≤ 1 にしてください")
    if kind == "symmetry":
        axes = set()
        for tag in ids:
            f = by_id[tag]
            n = np.asarray(f.get("normal"), float)
            if f.get("type") != "plane" or n.shape != (3,) or not np.isfinite(n).all():
                raise ValueError(f"対称面 {tag} は法線が分かる平面にしてください")
            axis = int(np.argmax(np.abs(n)))
            target = np.zeros(3)
            target[axis] = np.sign(n[axis])
            if np.linalg.norm(n - target) > 1e-8:
                raise ValueError(f"対称面 {tag} の法線が座標軸にそろっていません")
            axes.add(axis)
        if len(axes) > 1:
            raise ValueError("法線の軸が異なる対称面は別のパッチにしてください")


def validate_boundaries(boundaries, faces):
    names, assigned = set(), set()
    for b in boundaries:
        validate_boundary(b, faces)
        if b["patch"] in names or assigned.intersection(b["faces"]):
            raise ValueError("パッチ名または面の割り当てが重複しています")
        names.add(b["patch"])
        assigned.update(b["faces"])
    if assigned != {f["id"] for f in faces}:
        raise ValueError("すべての面をパッチに割り当ててください")


def assign_boundary(boundaries, b, faces, temperature, *, replace=None):
    validate_boundary(b, faces)
    if b["patch"] == DEFAULT_PATCH:
        raise ValueError("default_wall は未割り当て面用の予約名です")
    if replace and replace != b["patch"] and any(old["patch"] == b["patch"] for old in boundaries):
        raise ValueError("変更先のパッチ名はすでに使われています")
    out = []
    for old in boundaries:
        if old.get("automatic") or old["patch"] in (b["patch"], replace):
            continue
        keep = [f for f in faces if f["id"] in old["faces"] and f["id"] not in b["faces"]]
        if keep:
            out.append(dict(old, faces=[f["id"] for f in keep], fingerprints=[fingerprint(f) for f in keep]))
    selected = [f for f in faces if f["id"] in b["faces"]]
    out.append(dict(b, faces=[f["id"] for f in selected], fingerprints=[fingerprint(f) for f in selected]))
    return with_default(out, faces, temperature)
