// io: VTU(多面体・境界面)と CSV の書き出し

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <format>
#include <limits>
#include <map>
#include <numeric>
#include <regex>
#include <span>
#include <string>
#include <vector>

#include "core/error.hpp"
#include "core/paths.hpp"
#include "io/csv_writer.hpp"
#include "io/vtu_writer.hpp"
#include "mesh/geometry.hpp"
#include "support/box_mesh.hpp"
#include "support/test_paths.hpp"

using namespace gsis;
using Catch::Matchers::ContainsSubstring;

namespace {

// appended 形式の VTU を読み、DataArray の名前ごとに生のバイト列を取り出す(自己整合の検査用)
std::map<std::string, std::string> read_appended_arrays(const std::string& text) {
  std::map<std::string, std::string> out;
  const std::size_t marker = text.find("<AppendedData encoding=\"raw\">");
  REQUIRE(marker != std::string::npos);
  const std::size_t base = text.find('_', marker) + 1;
  const std::regex re("<DataArray[^>]*Name=\"([^\"]+)\"[^>]*offset=\"([0-9]+)\"");
  const std::string head = text.substr(0, marker);
  for (std::sregex_iterator it(head.begin(), head.end(), re), end; it != end; ++it) {
    const std::size_t off = base + std::stoull((*it)[2].str());
    std::uint64_t n = 0;
    std::memcpy(&n, text.data() + off, 8);
    REQUIRE(off + 8 + n <= text.size());
    out[(*it)[1].str()] = text.substr(off + 8, static_cast<std::size_t>(n));
  }
  return out;
}

template <class T>
std::vector<T> as_vector(const std::string& bytes) {
  std::vector<T> v(bytes.size() / sizeof(T));
  if (!v.empty()) std::memcpy(v.data(), bytes.data(), v.size() * sizeof(T));
  return v;
}

}  // namespace

TEST_CASE("VTU polyhedra: structure of the appended file", "[io][vtu]") {
  test::BoxMeshOptions o;
  o.n = {3, 2, 2};
  o.tetrahedra = true;
  o.distortion = 0.1;
  const PolyMesh mesh = test::make_box_mesh(o);
  const MeshGeometry g = compute_geometry(mesh);

  VtuWriter w = VtuWriter::polyhedra(mesh);
  CHECK(w.n_cells() == mesh.n_cells);
  std::vector<std::int32_t> ids(static_cast<std::size_t>(mesh.n_cells));
  std::iota(ids.begin(), ids.end(), 0);
  w.add_cell_scalar("cell_id", std::span<const std::int32_t>(ids));
  w.add_cell_scalar("volume", std::span<const double>(g.cell_volume));
  w.add_cell_vector("center", std::span<const Vec3>(g.cell_center));
  std::vector<double> tensor(6 * static_cast<std::size_t>(mesh.n_cells), 1.0);
  w.add_cell_field("sigma", 6, tensor);
  CHECK_THROWS_AS(w.add_cell_scalar("volume", std::span<const double>(g.cell_volume)), Error);  // 名前の重複
  CHECK_THROWS_AS(w.add_cell_field("bad", 3, tensor), Error);                                   // 長さ違い

  const std::filesystem::path path = test::output_dir() / path_from_utf8("vtu/四面体.vtu");
  w.write(path);
  const std::string text = read_file(path);
  CHECK_THAT(text, ContainsSubstring("type=\"UnstructuredGrid\""));
  CHECK_THAT(text, ContainsSubstring(std::format("NumberOfPoints=\"{}\" NumberOfCells=\"{}\"", mesh.n_points(), mesh.n_cells)));
  CHECK(text.ends_with("</VTKFile>\n"));

  const auto arrays = read_appended_arrays(text);
  const auto types = as_vector<std::uint8_t>(arrays.at("types"));
  REQUIRE(types.size() == static_cast<std::size_t>(mesh.n_cells));
  for (const auto t : types) CHECK(t == 42);  // VTK_POLYHEDRON
  const auto faceoffsets = as_vector<std::int64_t>(arrays.at("faceoffsets"));
  const auto faces = as_vector<std::int64_t>(arrays.at("faces"));
  REQUIRE(faceoffsets.size() == static_cast<std::size_t>(mesh.n_cells));
  CHECK(faceoffsets.back() == static_cast<std::int64_t>(faces.size()));
  // 各セルの面の並び: [面の数 = 4, 3, a, b, c, ...]。面は外向き(面積ベクトル·(面中心 − セル中心) > 0)
  std::int64_t pos = 0;
  for (Index c = 0; c < mesh.n_cells; ++c) {
    const std::int64_t nf = faces[static_cast<std::size_t>(pos++)];
    CHECK(nf == 4);
    for (std::int64_t f = 0; f < nf; ++f) {
      const std::int64_t nn = faces[static_cast<std::size_t>(pos++)];
      std::vector<Index> nodes;
      for (std::int64_t k = 0; k < nn; ++k) nodes.push_back(static_cast<Index>(faces[static_cast<std::size_t>(pos++)]));
      const FaceGeometry fg = compute_face_geometry(mesh.points, nodes);
      CHECK(dot(fg.area_vector, fg.center - g.cell_center[static_cast<std::size_t>(c)]) > 0.0);
    }
    CHECK(pos == faceoffsets[static_cast<std::size_t>(c)]);
  }
  CHECK(as_vector<double>(arrays.at("volume")) == g.cell_volume);
  CHECK(as_vector<std::int32_t>(arrays.at("cell_id")) == ids);
  const auto points = as_vector<double>(arrays.at("Points"));
  CHECK(points.size() == 3 * mesh.points.size());

  // ASCII 形式
  const std::filesystem::path ascii = test::output_dir() / "vtu/tets_ascii.vtu";
  w.write(ascii, VtuEncoding::Ascii);
  const std::string atext = read_file(ascii);
  CHECK_THAT(atext, ContainsSubstring("Name=\"faceoffsets\" format=\"ascii\""));
  CHECK_THAT(atext, ContainsSubstring("NumberOfComponents=\"6\""));
}

TEST_CASE("VTU boundary polygons", "[io][vtu]") {
  const PolyMesh mesh = test::make_box_mesh(test::tube_like_box({4, 2, 2}, Vec3{0, 0, 0}, Vec3{4, 1, 1}));
  VtuWriter all = VtuWriter::boundary_polygons(mesh);
  CHECK(all.n_cells() == mesh.n_boundary_faces());
  const int inlet = mesh.find_patch("inlet");
  REQUIRE(inlet >= 0);
  const std::vector<int> only_inlet = {inlet};
  VtuWriter w = VtuWriter::boundary_polygons(mesh, only_inlet);
  CHECK(w.n_cells() == mesh.patches[static_cast<std::size_t>(inlet)].count);
  const std::filesystem::path path = test::output_dir() / "vtu/inlet.vtu";
  w.write(path);
  const auto arrays = read_appended_arrays(read_file(path));
  for (const auto t : as_vector<std::uint8_t>(arrays.at("types"))) CHECK(t == 7);  // VTK_POLYGON
  for (const auto p : as_vector<std::int32_t>(arrays.at("patch_id"))) CHECK(p == inlet);
  CHECK(arrays.count("faces") == 0);
}

TEST_CASE("CSV writer", "[io][csv]") {
  const std::filesystem::path path = test::output_dir() / "csv/history.csv";
  {
    CsvWriter csv(path, {"iteration", "epsilon", "note"});
    csv.set_autoflush(true);
    csv.write_row({std::int64_t{1}, 0.1, std::string("plain")});
    csv.write_row({std::int64_t{2}, 1e-5, std::string("a, \"quoted\" note")});
    const std::vector<double> row = {3.0, 2.5e-300, -0.0};
    csv.write_row(std::span<const double>(row));
    CHECK_THROWS_AS(csv.write_row({1.0, 2.0}), Error);
  }
  const std::string text = read_file(path);
  CHECK(text ==
        "iteration,epsilon,note\n"
        "1,0.1,plain\n"
        "2,1e-05,\"a, \"\"quoted\"\" note\"\n"
        "3,2.5e-300,-0\n");
  CHECK(csv_field(std::numeric_limits<double>::quiet_NaN()) == "nan");
  CHECK(csv_escape("日本語") == "日本語");
}
