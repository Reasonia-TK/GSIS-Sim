"""pmesh 形式の読み書きと検査。"""

from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import perturbed_box
from gsis_tools.pmesh import (
    ALIGNMENT,
    MAGIC,
    PMeshError,
    PolyMesh,
    check_mesh,
    read_header,
    read_pmesh,
    write_pmesh,
)


def _raw_header(path):
    buf = path.read_bytes()
    hlen = int(np.frombuffer(buf[8:16], dtype="<u8")[0])
    return buf, hlen, json.loads(buf[16 : 16 + hlen].decode("utf-8"))


def test_roundtrip(tmp_path):
    m = perturbed_box(5)
    path = write_pmesh(tmp_path / "box.pmesh", m, metadata={"note": "テスト"})
    r = read_pmesh(path)
    for name in ("points", "face_offsets", "face_nodes", "owner", "neighbour"):
        assert np.array_equal(getattr(r, name), getattr(m, name)), name
    assert r.n_cells == m.n_cells
    assert [(p.name, p.kind, p.start, p.count) for p in r.patches] == [
        (p.name, p.kind, p.start, p.count) for p in m.patches
    ]
    assert r.metadata["note"] == "テスト"
    assert r.metadata["generator"].startswith("gsis_tools")
    np.testing.assert_allclose(r.geometry().cell_volumes, m.geometry().cell_volumes, rtol=0, atol=0)


def test_file_layout(tmp_path):
    """docs/pmesh_format.md §1・§2 のとおりの配置になっていること。"""
    m = perturbed_box(3)
    path = write_pmesh(tmp_path / "box.pmesh", m)
    buf, hlen, h = _raw_header(path)
    assert buf[:8] == MAGIC == b"PMESH\x00\x00\x00"
    assert h["format"] == "pmesh" and h["version"] == 1 and h["length_unit"] == "m"
    assert (h["n_points"], h["n_faces"], h["n_internal_faces"], h["n_cells"]) == (
        m.n_points, m.n_faces, m.n_internal_faces, m.n_cells,
    )
    arrays = h["arrays"]
    assert arrays["points"]["dtype"] == "float64" and arrays["points"]["shape"] == [m.n_points, 3]
    assert arrays["face_offsets"]["dtype"] == "int64" and arrays["face_offsets"]["shape"] == [m.n_faces + 1]
    assert arrays["face_nodes"]["dtype"] == "int32" and arrays["face_nodes"]["shape"] == [int(m.face_offsets[-1])]
    assert arrays["owner"]["dtype"] == "int32" and arrays["owner"]["shape"] == [m.n_faces]
    assert arrays["neighbour"]["dtype"] == "int32" and arrays["neighbour"]["shape"] == [m.n_internal_faces]
    offs = [arrays[n]["offset"] for n in ("points", "face_offsets", "face_nodes", "owner", "neighbour")]
    assert all(o % ALIGNMENT == 0 for o in offs)
    assert offs[0] >= 16 + hlen and offs == sorted(offs)
    # 間は 0 で埋める
    assert set(buf[16 + hlen : offs[0]]) <= {0}
    # 配列の中身(リトルエンディアン)
    pts = np.frombuffer(buf, dtype="<f8", count=m.n_points * 3, offset=offs[0]).reshape(-1, 3)
    assert np.array_equal(pts, m.points)
    nb = np.frombuffer(buf, dtype="<i4", count=m.n_internal_faces, offset=offs[4])
    assert np.array_equal(nb, m.neighbour)
    # パッチは境界面を連続して覆う
    p = h["patches"]
    assert p[0]["start"] == m.n_internal_faces
    assert all(a["start"] + a["count"] == b["start"] for a, b in zip(p[:-1], p[1:]))
    assert p[-1]["start"] + p[-1]["count"] == m.n_faces
    assert all(isinstance(v, str) for v in h["metadata"].values())
    assert read_header(path)["n_cells"] == m.n_cells


def _rewrite_header(path, out, mutate):
    buf, hlen, h = _raw_header(path)
    mutate(h)
    hb = json.dumps(h).encode("utf-8")
    assert len(hb) <= hlen  # 配列のオフセットを保つため、短くなる変更だけ
    hb = hb + b" " * (hlen - len(hb))
    out.write_bytes(buf[:16] + hb + buf[16 + hlen :])
    return out


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda h: h.update(version=2), "version"),
        (lambda h: h.update(format="xmesh"), "format"),
        (lambda h: h.update(length_unit="mm"), "length_unit"),
        (lambda h: h["arrays"]["points"].update(dtype="float32"), "dtype"),
        (lambda h: h["arrays"]["owner"].update(offset=h["arrays"]["owner"]["offset"] + 4), "offset"),
        (lambda h: h["arrays"]["owner"].update(shape=[1]), "shape"),
        (lambda h: h.update(n_cells=h["n_cells"] - 1), "範囲外"),
    ],
)
def test_reader_rejects_bad_header(tmp_path, mutate, message):
    path = write_pmesh(tmp_path / "ok.pmesh", perturbed_box(2))
    bad = _rewrite_header(path, tmp_path / "bad.pmesh", mutate)
    with pytest.raises(PMeshError, match=message):
        read_pmesh(bad)


def test_reader_rejects_bad_magic_and_truncation(tmp_path):
    path = write_pmesh(tmp_path / "ok.pmesh", perturbed_box(2))
    buf = path.read_bytes()
    (tmp_path / "magic.pmesh").write_bytes(b"XMESH\x00\x00\x00" + buf[8:])
    with pytest.raises(PMeshError, match="マジック"):
        read_pmesh(tmp_path / "magic.pmesh")
    (tmp_path / "short.pmesh").write_bytes(buf[:-100])
    with pytest.raises(PMeshError, match="はみ出し"):
        read_pmesh(tmp_path / "short.pmesh")


# --- 検査 ---


def test_check_ok(box3):
    r = check_mesh(box3)
    assert r.ok, r.errors
    assert not r.warnings
    assert r.info["最大 |ΣS|/Σ|S|"] < 1e-14


def _broken(m: PolyMesh, **kw) -> PolyMesh:
    b = m.copy()
    for k, v in kw.items():
        setattr(b, k, v)
    b.__post_init__()
    return b


def test_check_detects_flipped_face(box3):
    fn = box3.face_nodes.copy()
    f = 0  # 内部面の節点順を反転
    s, e = box3.face_offsets[f], box3.face_offsets[f + 1]
    fn[s:e] = fn[s:e][::-1]
    r = check_mesh(_broken(box3, face_nodes=fn))
    assert any("閉じていない" in e for e in r.errors)


def test_check_detects_flipped_boundary_face(box3):
    fn = box3.face_nodes.copy()
    f = box3.n_internal_faces + 3
    s, e = box3.face_offsets[f], box3.face_offsets[f + 1]
    fn[s:e] = fn[s:e][::-1]
    r = check_mesh(_broken(box3, face_nodes=fn))
    assert any("閉じていない" in e for e in r.errors)


def test_check_detects_inverted_cells(box3):
    """全ての面を反転すると閉じてはいるが体積が負になる。"""
    fn = box3.face_nodes.copy()
    for f in range(box3.n_faces):
        s, e = box3.face_offsets[f], box3.face_offsets[f + 1]
        fn[s:e] = fn[s:e][::-1]
    r = check_mesh(_broken(box3, face_nodes=fn))
    assert any("体積が正でない" in e for e in r.errors)
    assert not any("閉じていない" in e for e in r.errors)
    assert r.info["内部面の向きの違反"] == box3.n_internal_faces


def test_check_detects_owner_order(box3):
    own = box3.owner.copy()
    nei = box3.neighbour.copy()
    own[0], nei[0] = nei[0], own[0]
    r = check_mesh(_broken(box3, owner=own, neighbour=nei))
    assert any("owner < neighbour" in e for e in r.errors)


def test_check_detects_unsorted_internal_faces(box3):
    ni = box3.n_internal_faces
    perm = np.arange(box3.n_faces)
    perm[[0, ni - 1]] = perm[[ni - 1, 0]]
    sizes = np.diff(box3.face_offsets)[perm]
    fo = np.concatenate([[0], np.cumsum(sizes)])
    fn = np.concatenate([box3.face(f) for f in perm])
    r = check_mesh(
        _broken(box3, face_offsets=fo, face_nodes=fn, owner=box3.owner[perm], neighbour=box3.neighbour[perm[:ni]])
    )
    assert any("辞書順" in e for e in r.errors)


def test_check_detects_patch_gap_and_kind(box3):
    from gsis_tools.pmesh import Patch

    patches = [Patch(p.name, p.kind, p.start, p.count) for p in box3.patches]
    patches[1].count -= 1
    r = check_mesh(_broken(box3, patches=patches))
    assert any("start" in e for e in r.errors)
    patches = [Patch(p.name, p.kind, p.start, p.count) for p in box3.patches]
    patches[0].kind = "farfield"
    r = check_mesh(_broken(box3, patches=patches))
    assert any("kind" in e for e in r.errors)
    patches = [Patch(p.name, p.kind, p.start, p.count) for p in box3.patches]
    patches[2].name = patches[1].name
    r = check_mesh(_broken(box3, patches=patches))
    assert any("重複" in e for e in r.errors)


def test_check_detects_duplicate_node_and_small_face(box3):
    fn = box3.face_nodes.copy()
    fn[1] = fn[0]
    r = check_mesh(_broken(box3, face_nodes=fn))
    assert any("重複して含む面" in e for e in r.errors)


def test_check_range_errors(box3):
    own = box3.owner.copy()
    own[-1] = box3.n_cells
    r = check_mesh(_broken(box3, owner=own))
    assert any("範囲外" in e for e in r.errors)


def test_unreferenced_point_is_warning(box3):
    pts = np.vstack([box3.points, [[9.0, 9.0, 9.0]]])
    r = check_mesh(_broken(box3, points=pts))
    assert r.ok
    assert any("参照されない節点" in w for w in r.warnings)


def test_write_refuses_invalid_mesh(tmp_path, box3):
    own = box3.owner.copy()
    nei = box3.neighbour.copy()
    own[0], nei[0] = nei[0], own[0]
    with pytest.raises(PMeshError):
        write_pmesh(tmp_path / "bad.pmesh", _broken(box3, owner=own, neighbour=nei))
