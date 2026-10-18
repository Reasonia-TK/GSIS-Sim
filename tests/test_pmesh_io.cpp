// mesh: .pmesh の書き込み → 読み込み、形式と規約の検査

#include <algorithm>
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <format>
#include <string>
#include <vector>

#include "core/error.hpp"
#include "core/json.hpp"
#include "core/log.hpp"
#include "core/paths.hpp"
#include "mesh/pmesh_io.hpp"
#include "support/box_mesh.hpp"
#include "support/test_paths.hpp"

using namespace gsis;
using Catch::Matchers::ContainsSubstring;

namespace {

PolyMesh sample_mesh() {
  test::BoxMeshOptions o = test::tube_like_box({4, 3, 2}, Vec3{0.0, -1e-3, -1e-3}, Vec3{4e-3, 1e-3, 1e-3});
  o.distortion = 0.2;
  o.seed = 3;
  PolyMesh mesh = test::make_box_mesh(o);
  mesh.metadata = {{"generator", "test"}, {"created", "2026-10-15T12:00:00Z"}, {"source", "日本語のメモ"}};
  return mesh;
}

void check_equal(const PolyMesh& a, const PolyMesh& b) {
  CHECK(a.n_cells == b.n_cells);
  CHECK(a.length_unit == b.length_unit);
  REQUIRE(a.points.size() == b.points.size());
  for (std::size_t i = 0; i < a.points.size(); ++i) CHECK(a.points[i] == b.points[i]);  // ビット単位
  CHECK(a.face_offsets == b.face_offsets);
  CHECK(a.face_nodes == b.face_nodes);
  CHECK(a.owner == b.owner);
  CHECK(a.neighbour == b.neighbour);
  REQUIRE(a.patches.size() == b.patches.size());
  for (std::size_t p = 0; p < a.patches.size(); ++p) {
    CHECK(a.patches[p].name == b.patches[p].name);
    CHECK(a.patches[p].kind == b.patches[p].kind);
    CHECK(a.patches[p].start == b.patches[p].start);
    CHECK(a.patches[p].count == b.patches[p].count);
  }
  CHECK(a.metadata == b.metadata);
}

// ファイルのヘッダ JSON を取り出す
json::Value read_header(const std::string& bytes) {
  std::uint64_t h = 0;
  std::memcpy(&h, bytes.data() + 8, 8);
  return json::parse(std::string_view(bytes).substr(16, static_cast<std::size_t>(h)));
}

// 同じ長さの文字列で置き換える(ヘッダの長さとオフセットを保つ)
std::string patch_text(std::string bytes, const std::string& from, const std::string& to) {
  REQUIRE(from.size() == to.size());
  const std::size_t pos = bytes.find(from);
  REQUIRE(pos != std::string::npos);
  bytes.replace(pos, from.size(), to);
  return bytes;
}

void expect_read_error(const std::filesystem::path& path, const std::string& bytes, const std::string& fragment) {
  write_file(path, bytes);
  try {
    read_pmesh(path);
    FAIL("例外が出ませんでした: " + fragment);
  } catch (const Error& e) {
    CHECK_THAT(e.what(), ContainsSubstring(fragment));
  }
}

}  // namespace

TEST_CASE("pmesh write then read gives the same mesh", "[mesh][pmesh]") {
  const PolyMesh mesh = sample_mesh();
  const std::filesystem::path path = test::output_dir() / "roundtrip.pmesh";
  write_pmesh(path, mesh);
  const PolyMesh back = read_pmesh(path);
  check_equal(mesh, back);

  // 形式: マジック、ヘッダ、64 の倍数のオフセット、dtype
  const std::string bytes = read_file(path);
  CHECK(std::memcmp(bytes.data(), "PMESH\0\0\0", 8) == 0);
  const json::Value h = read_header(bytes);
  std::uint64_t header_bytes = 0;
  std::memcpy(&header_bytes, bytes.data() + 8, 8);
  CHECK(h.at("format").as_string() == "pmesh");
  CHECK(h.at("version").as_int() == 1);
  CHECK(h.at("n_cells").as_int() == mesh.n_cells);
  CHECK(h.at("n_internal_faces").as_int() == mesh.n_internal_faces());
  for (const char* name : {"points", "face_offsets", "face_nodes", "owner", "neighbour"}) {
    const json::Value& a = h.at("arrays").at(name);
    CHECK(a.at("offset").as_int() % 64 == 0);
    CHECK(a.at("offset").as_int() >= static_cast<std::int64_t>(16 + header_bytes));
  }
  CHECK(h.at("arrays").at("points").at("dtype").as_string() == "float64");
  CHECK(h.at("arrays").at("face_offsets").at("dtype").as_string() == "int64");
  CHECK(h.at("arrays").at("owner").at("dtype").as_string() == "int32");

  // 読み込み + 検査
  const LoadedMesh lm = load_pmesh(path);
  CHECK(lm.report.ok());
  CHECK(lm.report.n_unused_points == 0);
}

namespace {

// テスト用の .pmesh の組み立て方(検査を通らないファイルも作れるように、PolyMesh を検査しない)
struct RawOptions {
  bool index_int64 = false;          // face_nodes・owner・neighbour を int64 にする
  bool offsets_int32 = false;        // face_offsets を int32 にする
  bool overlap_owner = false;        // owner を face_nodes と同じ offset に置く(重なり)
  bool owner_shape_short = false;    // owner の shape を 1 つ短くする(配列も短くする)
  bool neighbour_at_owner = false;   // neighbour を owner と同じ offset に置く(長さ 0 のとき用)
  std::int64_t points_rows = -1;     // 0 以上ならヘッダの points の shape[0] をこの値にする(配列はそのまま)
  json::Value metadata;              // null なら書かない
};

// 整数配列の dtype や配置を選んで .pmesh を組み立てる(書き手が Python のときの別の dtype も模す)
std::string make_raw_pmesh(const PolyMesh& m, const RawOptions& opt) {
  const bool index_int64 = opt.index_int64;
  const bool offsets_int32 = opt.offsets_int32;
  struct Blob {
    std::string name;
    std::string dtype;
    std::int64_t length;
    int columns;
    std::string data;
  };
  auto ints = [](const auto& v, bool wide) {
    std::string s;
    for (const auto x : v) {
      if (wide) {
        const std::int64_t y = x;
        s.append(reinterpret_cast<const char*>(&y), 8);
      } else {
        const std::int32_t y = static_cast<std::int32_t>(x);
        s.append(reinterpret_cast<const char*>(&y), 4);
      }
    }
    return s;
  };
  std::string pts;
  for (const Vec3& p : m.points) {
    for (int a = 0; a < 3; ++a) {
      const double v = p[a];
      pts.append(reinterpret_cast<const char*>(&v), 8);
    }
  }
  const std::string itype = index_int64 ? "int64" : "int32";
  std::vector<Index> owner = m.owner;
  if (opt.owner_shape_short && !owner.empty()) owner.pop_back();
  const std::vector<Blob> blobs = {
      {"points", "float64", m.n_points(), 3, pts},
      {"face_offsets", offsets_int32 ? "int32" : "int64", static_cast<std::int64_t>(m.face_offsets.size()), 1,
       ints(m.face_offsets, !offsets_int32)},
      {"face_nodes", itype, static_cast<std::int64_t>(m.face_nodes.size()), 1, ints(m.face_nodes, index_int64)},
      {"owner", itype, static_cast<std::int64_t>(owner.size()), 1, ints(owner, index_int64)},
      {"neighbour", itype, m.n_internal_faces(), 1, ints(m.neighbour, index_int64)},
  };
  std::int64_t base = 2048;
  std::string header;
  std::vector<std::int64_t> offsets;
  for (int iter = 0; iter < 8; ++iter) {
    json::Value h;
    h.set("format", "pmesh");
    h.set("version", 1);
    h.set("length_unit", "m");
    h.set("n_points", m.n_points());
    h.set("n_faces", m.n_faces());
    h.set("n_internal_faces", m.n_internal_faces());
    h.set("n_cells", m.n_cells);
    json::Value patches = json::Array{};
    for (const Patch& p : m.patches) {
      json::Value jp;
      jp.set("name", p.name);
      jp.set("kind", p.kind);
      jp.set("start", p.start);
      jp.set("count", p.count);
      patches.push_back(jp);
    }
    h.set("patches", patches);
    json::Value arrays = json::Object{};
    offsets.clear();
    std::int64_t pos = base;
    for (const Blob& b : blobs) {
      std::int64_t off = pos;
      if (opt.overlap_owner && b.name == "owner") off = offsets[2];          // face_nodes と同じ位置
      if (opt.neighbour_at_owner && b.name == "neighbour") off = offsets[3];  // owner と同じ位置
      json::Value a;
      a.set("dtype", b.dtype);
      json::Value shape = json::Array{};
      shape.push_back(b.name == "points" && opt.points_rows >= 0 ? opt.points_rows : b.length);
      if (b.columns > 1) shape.push_back(b.columns);
      a.set("shape", shape);
      a.set("offset", off);
      arrays.set(b.name, a);
      offsets.push_back(off);
      pos = std::max(pos, (off + static_cast<std::int64_t>(b.data.size()) + 63) / 64 * 64);
    }
    h.set("arrays", arrays);
    if (!opt.metadata.is_null()) h.set("metadata", opt.metadata);
    header = json::dump(h);  // 1 行
    if (16 + static_cast<std::int64_t>(header.size()) <= base) break;
    base = (16 + static_cast<std::int64_t>(header.size()) + 63) / 64 * 64;
  }
  // 全体を 0 で埋めた領域に、ヘッダと各配列をその offset に書く(重なりがあれば後の配列で上書き)
  std::size_t total = 16 + header.size();
  for (std::size_t i = 0; i < blobs.size(); ++i) {
    total = std::max(total, static_cast<std::size_t>(offsets[i]) + blobs[i].data.size());
  }
  std::string out(total, '\0');
  std::memcpy(out.data(), "PMESH\0\0\0", 8);
  const std::uint64_t hl = header.size();
  std::memcpy(out.data() + 8, &hl, 8);
  std::memcpy(out.data() + 16, header.data(), header.size());
  for (std::size_t i = 0; i < blobs.size(); ++i) {
    if (!blobs[i].data.empty()) {
      std::memcpy(out.data() + offsets[i], blobs[i].data.data(), blobs[i].data.size());
    }
  }
  return out;
}

// 2 つの単位立方体を x = 1 でつなぎ、その間の面を 2 枚に分けたメッシュ(同じセルの組を結ぶ内部面が 2 つ)
PolyMesh two_cells_with_split_face() {
  const std::vector<Vec3> pts = {{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}, {0, 0, 1}, {1, 0, 1}, {1, 1, 1},
                                 {0, 1, 1}, {2, 0, 0}, {2, 1, 0}, {2, 0, 1}, {2, 1, 1}, {1, 0.5, 0}, {1, 0.5, 1}};
  const std::vector<std::vector<std::vector<Index>>> cells = {
      // セル 0: x+ の面を y = 0.5 で 2 枚に分ける(z− と z+ の面は 5 角形になる)
      {{0, 4, 7, 3}, {1, 12, 13, 5}, {12, 2, 6, 13}, {0, 1, 5, 4}, {3, 7, 6, 2}, {0, 3, 2, 12, 1}, {4, 5, 13, 6, 7}},
      // セル 1: x− の 2 枚はセル 0 の面の逆向き
      {{5, 13, 12, 1}, {13, 6, 2, 12}, {8, 9, 11, 10}, {1, 8, 10, 5}, {2, 6, 11, 9}, {1, 12, 2, 9, 8},
       {5, 10, 11, 6, 13}},
  };
  return test::build_poly_mesh(pts, cells, {{"walls", "wall"}}, [](const Vec3&) { return 0; });
}

}  // namespace

TEST_CASE("pmesh: several internal faces between the same pair of cells", "[mesh][pmesh]") {
  const PolyMesh mesh = two_cells_with_split_face();
  REQUIRE(mesh.n_internal_faces() == 2);
  CHECK(mesh.owner[0] == 0);
  CHECK(mesh.neighbour[0] == 1);
  CHECK(mesh.owner[1] == 0);
  CHECK(mesh.neighbour[1] == 1);
  const LoadedMesh lm = validate_mesh(mesh);
  CHECK(lm.report.ok());
  CHECK(lm.geometry.cell_volume[0] == Catch::Approx(1.0).epsilon(1e-15));
  CHECK(lm.geometry.cell_volume[1] == Catch::Approx(1.0).epsilon(1e-15));
  CHECK(lm.report.n_orientation_warnings == 0);
  // 書いて読める
  const std::filesystem::path path = test::output_dir() / "split_face.pmesh";
  write_pmesh(path, mesh);
  check_equal(mesh, read_pmesh(path));
}

TEST_CASE("pmesh reader: failures that only the reader checks", "[mesh][pmesh]") {
  const PolyMesh mesh = sample_mesh();
  const std::filesystem::path bad = test::output_dir() / "bad_raw.pmesh";
  SECTION("decreasing face_offsets") {
    PolyMesh m = mesh;
    m.face_offsets[1] = m.face_offsets[2] + 1;  // 面 0 が面 1 の区間を越える
    expect_read_error(bad, make_raw_pmesh(m, {}), "face_offsets が減少");
  }
  SECTION("overlapping arrays") {
    RawOptions o;
    o.overlap_owner = true;
    expect_read_error(bad, make_raw_pmesh(mesh, o), "重なっています");
  }
  SECTION("shape mismatch") {
    RawOptions o;
    o.owner_shape_short = true;
    expect_read_error(bad, make_raw_pmesh(mesh, o), "shape が期待と違います");
  }
  SECTION("int64 value outside the int32 range") {
    RawOptions o;
    o.index_int64 = true;
    std::string bytes = make_raw_pmesh(mesh, o);
    const json::Value h = read_header(bytes);
    const auto off = static_cast<std::size_t>(h.at("arrays").at("owner").at("offset").as_int());
    const std::int64_t huge = std::int64_t{1} << 40;
    std::memcpy(bytes.data() + off, &huge, 8);
    expect_read_error(bad, bytes, "int32 の範囲");
  }
  SECTION("huge shape does not overflow") {
    // 要素数 × 8 バイトが 2^64 を超える shape(桁あふれで検査をすり抜けないこと)
    RawOptions o;
    o.points_rows = std::int64_t{1} << 62;
    expect_read_error(bad, make_raw_pmesh(mesh, o), "大きすぎます");
  }
  SECTION("metadata with a non-string value is skipped with a warning") {
    RawOptions o;
    json::Value md;
    md.set("generator", "test");
    md.set("count", 5);
    o.metadata = md;
    write_file(bad, make_raw_pmesh(mesh, o));
    std::vector<std::string> warnings;
    set_log_sink([&](LogLevel level, std::string_view msg) {
      if (level == LogLevel::Warn) warnings.emplace_back(msg);
    });
    const PolyMesh back = read_pmesh(bad);
    set_log_sink({});
    REQUIRE(back.metadata.size() == 1);
    CHECK(back.metadata[0].first == "generator");
    REQUIRE(warnings.size() == 1);
    CHECK_THAT(warnings[0], ContainsSubstring("count"));
  }
  SECTION("empty array placed at the offset of another array is not an overlap") {
    // 内部面のない 1 セルのメッシュで、長さ 0 の neighbour を owner と同じ位置に置く
    const std::vector<Vec3> cube = {{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}, {0, 0, 1}, {1, 0, 1}, {1, 1, 1}, {0, 1, 1}};
    const PolyMesh one = test::build_poly_mesh(
        cube, {{{0, 4, 7, 3}, {1, 2, 6, 5}, {0, 1, 5, 4}, {3, 7, 6, 2}, {0, 3, 2, 1}, {4, 5, 6, 7}}},
        {{"all", "wall"}}, [](const Vec3&) { return 0; });
    RawOptions o;
    o.neighbour_at_owner = true;
    write_file(bad, make_raw_pmesh(one, o));
    const PolyMesh back = read_pmesh(bad);
    CHECK(back.n_internal_faces() == 0);
    CHECK(back.n_cells == 1);
  }
  SECTION("empty patch is allowed with a warning") {
    PolyMesh m = mesh;
    Patch empty;
    empty.name = "unused";
    empty.kind = "patch";
    empty.start = m.n_faces();
    empty.count = 0;
    m.patches.push_back(empty);
    write_file(bad, make_raw_pmesh(m, {}));
    std::vector<std::string> warnings;
    set_log_sink([&](LogLevel level, std::string_view msg) {
      if (level == LogLevel::Warn) warnings.emplace_back(msg);
    });
    const PolyMesh back = read_pmesh(bad);
    set_log_sink({});
    CHECK(back.patches.size() == m.patches.size());
    REQUIRE(warnings.size() == 1);
    CHECK_THAT(warnings[0], ContainsSubstring("unused"));
  }
}

TEST_CASE("pmesh with a Japanese path and other integer dtypes", "[mesh][pmesh]") {
  const PolyMesh mesh = sample_mesh();
  const std::filesystem::path path = test::output_dir() / path_from_utf8("日本語のフォルダ/箱 メッシュ.pmesh");
  ensure_parent_directory(path);
  write_pmesh(path, mesh);
  check_equal(mesh, read_pmesh(path));

  // 整数配列が int64(face_offsets は int32)でも読める。metadata が無くてもよい。
  PolyMesh no_meta = mesh;
  no_meta.metadata.clear();
  const std::filesystem::path path64 = test::output_dir() / "int64_indices.pmesh";
  RawOptions wide;
  wide.index_int64 = true;
  wide.offsets_int32 = true;
  write_file(path64, make_raw_pmesh(no_meta, wide));
  check_equal(no_meta, read_pmesh(path64));
  write_file(path64, make_raw_pmesh(no_meta, {}));
  check_equal(no_meta, read_pmesh(path64));
}

TEST_CASE("pmesh reader rejects broken files", "[mesh][pmesh]") {
  const PolyMesh mesh = sample_mesh();
  const std::filesystem::path good = test::output_dir() / "good.pmesh";
  write_pmesh(good, mesh);
  const std::string bytes = read_file(good);
  const std::filesystem::path bad = test::output_dir() / "bad.pmesh";

  SECTION("magic") {
    std::string b = bytes;
    b[0] = 'X';
    expect_read_error(bad, b, "マジック");
  }
  SECTION("header length") {
    std::string b = bytes;
    const std::uint64_t huge = std::uint64_t{1} << 40;
    std::memcpy(b.data() + 8, &huge, 8);
    expect_read_error(bad, b, "ヘッダの長さ");
  }
  SECTION("unknown version") {
    expect_read_error(bad, patch_text(bytes, "\"version\": 1", "\"version\": 2"), "version 2");
  }
  SECTION("unknown dtype") {
    expect_read_error(bad, patch_text(bytes, "\"float64\"", "\"float32\""), "未知");
  }
  SECTION("length unit") {
    expect_read_error(bad, patch_text(bytes, "\"length_unit\": \"m\"", "\"length_unit\": \"k\""), "length_unit");
  }
  SECTION("misaligned offset") {
    const json::Value h = read_header(bytes);
    const std::int64_t off = h.at("arrays").at("owner").at("offset").as_int();
    const std::string from = std::format("\"offset\": {}", off);
    const std::string to = std::format("\"offset\": {}", off + 8);
    if (from.size() == to.size()) {
      expect_read_error(bad, patch_text(bytes, from, to), "64 の倍数");
    }
  }
  SECTION("truncated file") {
    expect_read_error(bad, bytes.substr(0, bytes.size() - 100), "ファイルの外");
  }
  SECTION("broken JSON") {
    expect_read_error(bad, patch_text(bytes, "\"format\":", "\"format\" "), "JSON");
  }
  SECTION("topology error inside the arrays: owner > neighbour") {
    const json::Value h = read_header(bytes);
    std::string b = bytes;
    const auto off = static_cast<std::size_t>(h.at("arrays").at("owner").at("offset").as_int());
    const std::int32_t big = mesh.n_cells - 1;
    std::memcpy(b.data() + off, &big, 4);  // 面 0 の owner を最後のセルにする
    expect_read_error(bad, b, "owner");
  }
}

TEST_CASE("validate_topology detects convention violations", "[mesh][pmesh]") {
  set_log_level(LogLevel::Error);
  const PolyMesh base = sample_mesh();
  auto expect_error = [](const PolyMesh& m, const std::string& fragment) {
    try {
      validate_topology(m);
      FAIL("例外が出ませんでした: " + fragment);
    } catch (const Error& e) {
      CHECK_THAT(e.what(), ContainsSubstring(fragment));
    }
  };
  CHECK_NOTHROW(validate_topology(base));
  {
    PolyMesh m = base;
    std::swap(m.owner[0], m.neighbour[0]);
    expect_error(m, "owner");
  }
  {
    PolyMesh m = base;
    // 内部面 0 と 1 の (owner, neighbour) を入れ替えて順序を崩す
    REQUIRE(m.n_internal_faces() >= 2);
    const std::size_t last = static_cast<std::size_t>(m.n_internal_faces()) - 1;
    std::swap(m.owner[0], m.owner[last]);
    std::swap(m.neighbour[0], m.neighbour[last]);
    expect_error(m, "並んでいません");
  }
  {
    PolyMesh m = base;
    m.face_nodes[1] = m.face_nodes[0];
    expect_error(m, "重複");
  }
  {
    // face_offsets が途中で減る(面 0 の区間が面 1 を越える)。節点を読む前に止まること
    PolyMesh m = base;
    m.face_offsets[1] = m.face_offsets[2] + 1;
    expect_error(m, "3 個未満");
  }
  {
    PolyMesh m = base;
    m.face_nodes[2] = m.n_points();
    expect_error(m, "範囲");
  }
  {
    PolyMesh m = base;
    m.patches[1].start += 1;
    expect_error(m, "start");
  }
  {
    PolyMesh m = base;
    m.patches.back().count -= 1;
    expect_error(m, "過不足なく");
  }
  {
    PolyMesh m = base;
    m.patches[0].kind = "inflow";
    expect_error(m, "知らない種類");
  }
  {
    PolyMesh m = base;
    m.patches[1].name = m.patches[0].name;
    expect_error(m, "重複");
  }
  {
    PolyMesh m = base;
    m.length_unit = "mm";
    expect_error(m, "長さの単位");
  }
  {
    // 面の節点が 3 未満
    PolyMesh m;
    m.points = {{0, 0, 0}, {1, 0, 0}, {0, 1, 0}};
    m.n_cells = 1;
    m.face_offsets = {0, 2};
    m.face_nodes = {0, 1};
    m.owner = {0};
    m.patches = {{"all", "wall", 0, 1}};
    expect_error(m, "3 個未満");
  }
  {
    // 参照されない節点は警告だけ
    PolyMesh m = base;
    m.points.push_back({9, 9, 9});
    CHECK_NOTHROW(validate_topology(m));
    const LoadedMesh lm = validate_mesh(m);
    CHECK(lm.report.n_unused_points == 1);
    CHECK(lm.report.ok());
  }
  set_log_level(LogLevel::Info);
}
