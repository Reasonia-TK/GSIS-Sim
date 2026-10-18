// mesh: 面・セルの幾何量(体積・重心・面積・閉性)と幾何の検査

#include <algorithm>
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <vector>

#include "core/error.hpp"
#include "core/log.hpp"
#include "mesh/geometry.hpp"
#include "mesh/mesh_quality.hpp"
#include "mesh/pmesh_io.hpp"
#include "support/box_mesh.hpp"

using namespace gsis;
using Catch::Approx;

namespace {

// 1 セルだけのメッシュ(境界面はすべてパッチ "all")
PolyMesh single_cell(std::vector<Vec3> points, const std::vector<std::vector<Index>>& faces) {
  return test::build_poly_mesh(std::move(points), {faces}, {{"all", "wall"}},
                               [](const Vec3&) { return 0; });
}

double max_abs_diff(const Vec3& a, const Vec3& b) {
  return std::max({std::abs(a.x - b.x), std::abs(a.y - b.y), std::abs(a.z - b.z)});
}

}  // namespace

TEST_CASE("geometry of a unit cube cell", "[mesh][geometry]") {
  const PolyMesh mesh = single_cell(
      {{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0}, {0, 0, 1}, {1, 0, 1}, {1, 1, 1}, {0, 1, 1}},
      {{0, 4, 7, 3}, {1, 2, 6, 5}, {0, 1, 5, 4}, {3, 7, 6, 2}, {0, 3, 2, 1}, {4, 5, 6, 7}});
  const MeshGeometry g = compute_geometry(mesh);
  CHECK(g.cell_volume[0] == Approx(1.0).epsilon(1e-15));
  CHECK(max_abs_diff(g.cell_center[0], Vec3{0.5, 0.5, 0.5}) < 1e-15);
  for (Index f = 0; f < mesh.n_faces(); ++f) {
    CHECK(g.face_area[static_cast<std::size_t>(f)] == Approx(1.0).epsilon(1e-15));
    // 外向き: 面中心 − セル中心 と法線が同じ向き
    CHECK(dot(g.face_normal[static_cast<std::size_t>(f)],
              g.face_center[static_cast<std::size_t>(f)] - g.cell_center[0]) == Approx(0.5));
  }
  const MeshCheckReport r = check_geometry(mesh, g);
  CHECK(r.ok());
  CHECK(r.max_closure_residual < 1e-15);
  CHECK(r.total_volume == Approx(1.0));
}

TEST_CASE("geometry of a tetrahedron and a square pyramid", "[mesh][geometry]") {
  SECTION("tetrahedron") {
    const Vec3 a{0.2, -0.1, 0.3}, b{1.4, 0.1, 0.2}, c{0.1, 1.1, 0.4}, d{0.3, 0.2, 1.5};
    // 外向きの面(体積が正になる向き)
    const double vol6 = dot(b - a, cross(c - a, d - a));
    REQUIRE(vol6 > 0.0);
    const PolyMesh mesh = single_cell({a, b, c, d}, {{0, 2, 1}, {0, 1, 3}, {0, 3, 2}, {1, 2, 3}});
    const MeshGeometry g = compute_geometry(mesh);
    CHECK(g.cell_volume[0] == Approx(vol6 / 6.0).epsilon(1e-14));
    CHECK(max_abs_diff(g.cell_center[0], (a + b + c + d) / 4.0) < 1e-14);
    CHECK(check_geometry(mesh, g).ok());
  }
  SECTION("square pyramid") {
    const double s = 2.0, h = 3.0;
    const PolyMesh mesh = single_cell({{0, 0, 0}, {s, 0, 0}, {s, s, 0}, {0, s, 0}, {s / 2, s / 2, h}},
                                      {{0, 3, 2, 1}, {0, 1, 4}, {1, 2, 4}, {2, 3, 4}, {3, 0, 4}});
    const MeshGeometry g = compute_geometry(mesh);
    CHECK(g.cell_volume[0] == Approx(s * s * h / 3.0).epsilon(1e-14));
    CHECK(max_abs_diff(g.cell_center[0], Vec3{s / 2, s / 2, h / 4}) < 1e-14);
    CHECK(check_geometry(mesh, g).ok());
  }
}

TEST_CASE("face geometry: non-planar and non-convex faces", "[mesh][geometry]") {
  SECTION("area vector of a non-planar quad equals the loop vector area") {
    const std::vector<Vec3> p = {{0, 0, 0}, {1, 0, 0.1}, {1.2, 1, -0.2}, {0, 0.9, 0.3}};
    const std::vector<Index> nodes = {0, 1, 2, 3};
    const FaceGeometry fg = compute_face_geometry(p, nodes);
    Vec3 loop{};
    for (std::size_t k = 0; k < p.size(); ++k) loop += 0.5 * cross(p[k], p[(k + 1) % p.size()]);
    CHECK(max_abs_diff(fg.area_vector, loop) < 1e-15);
  }
  SECTION("U-shaped face whose node average lies outside (signed fan weights)") {
    // [0,3]×[0,2] から [1,2]×[1,2] を除いた U 字(面積 5、重心 (1.5, 0.9))。
    // 節点の平均 (1.5, 1.25) は切り欠きの中(多角形の外)なので、扇形の一部は負の面積になる。
    // 重みを |n_k| にした実装ではこの重心にならない。
    const Vec3 e1 = normalized(Vec3{0.3, 1.0, -0.2});
    const Vec3 e2 = normalized(cross(Vec3{1.0, 0.1, 0.4}, e1));
    const Vec3 origin{-0.4, 0.2, 1.1};
    auto P = [&](double u, double v) { return origin + u * e1 + v * e2; };
    const std::vector<Vec3> p = {P(0, 0), P(3, 0), P(3, 2), P(2, 2), P(2, 1), P(1, 1), P(1, 2), P(0, 2)};
    const FaceGeometry fg = compute_face_geometry(p, std::vector<Index>{0, 1, 2, 3, 4, 5, 6, 7});
    CHECK(norm(fg.area_vector) == Approx(5.0).epsilon(1e-14));
    CHECK(max_abs_diff(fg.center, P(1.5, 0.9)) < 1e-14);
  }
  SECTION("non-planar face center follows the definition in pmesh_format.md §4") {
    const std::vector<Vec3> p = {{0, 0, 0}, {1.1, 0, 0.2}, {1.3, 0.6, -0.1}, {0.9, 1.2, 0.3}, {-0.1, 0.8, 0.1}};
    // 定義をそのまま書いたもの: x̄ を仮の中心とする扇形、重みは符号付きの n_k·n̂
    const std::size_t n = p.size();
    Vec3 xbar{};
    for (const Vec3& q : p) xbar += q;
    xbar /= static_cast<double>(n);
    Vec3 sum_n{};
    std::vector<Vec3> nk(n), ck(n);
    for (std::size_t k = 0; k < n; ++k) {
      nk[k] = cross(p[k] - xbar, p[(k + 1) % n] - xbar);
      ck[k] = (p[k] + p[(k + 1) % n] + xbar) / 3.0;
      sum_n += nk[k];
    }
    const Vec3 nhat = normalized(sum_n);
    double sw = 0.0;
    Vec3 swc{};
    for (std::size_t k = 0; k < n; ++k) {
      sw += dot(nk[k], nhat);
      swc += dot(nk[k], nhat) * ck[k];
    }
    const FaceGeometry fg = compute_face_geometry(p, std::vector<Index>{0, 1, 2, 3, 4});
    CHECK(max_abs_diff(fg.area_vector, 0.5 * sum_n) < 1e-15);
    CHECK(max_abs_diff(fg.center, swc / sw) < 1e-15);
    // 非平面なので、節点の平均とは違う点になる
    CHECK(norm(fg.center - xbar) > 1e-3);
  }
  SECTION("centroid of a planar L-shaped hexagon") {
    // [0,2]×[0,1] と [0,1]×[1,2] の和(面積 3、重心 (5/6, 5/6))を傾けた平面に置く
    const Vec3 e1 = normalized(Vec3{1, 0.2, 0.1});
    const Vec3 e2 = normalized(cross(Vec3{0.1, -0.3, 1}, e1));
    const Vec3 origin{0.3, -0.2, 0.7};
    auto P = [&](double u, double v) { return origin + u * e1 + v * e2; };
    const std::vector<Vec3> p = {P(0, 0), P(2, 0), P(2, 1), P(1, 1), P(1, 2), P(0, 2)};
    const FaceGeometry fg = compute_face_geometry(p, std::vector<Index>{0, 1, 2, 3, 4, 5});
    CHECK(norm(fg.area_vector) == Approx(3.0).epsilon(1e-14));
    CHECK(max_abs_diff(fg.center, P(5.0 / 6.0, 5.0 / 6.0)) < 1e-14);
  }
}

TEST_CASE("geometry of structured, graded, distorted and tetrahedral boxes", "[mesh][geometry]") {
  const Vec3 lo{-0.5, 0.0, 1.0};
  const Vec3 hi{1.5, 0.75, 2.5};
  const double box_volume = (hi.x - lo.x) * (hi.y - lo.y) * (hi.z - lo.z);

  struct Variant {
    const char* name;
    bool tets;
    double distortion;
    std::array<double, 3> grading;
  };
  const Variant variants[] = {
      {"uniform hex", false, 0.0, {1.0, 1.0, 1.0}},
      {"graded hex", false, 0.0, {1.3, 0.8, 1.1}},
      {"distorted hex", false, 0.25, {1.0, 1.0, 1.0}},
      {"tetrahedra", true, 0.0, {1.0, 1.0, 1.0}},
      {"distorted tetrahedra", true, 0.15, {1.2, 1.0, 0.9}},
  };
  for (const Variant& v : variants) {
    DYNAMIC_SECTION(v.name) {
      test::BoxMeshOptions o;
      o.n = {5, 4, 3};
      o.lo = lo;
      o.hi = hi;
      o.tetrahedra = v.tets;
      o.distortion = v.distortion;
      o.grading = v.grading;
      o.seed = 7;
      const PolyMesh mesh = test::make_box_mesh(o);
      CHECK(mesh.n_cells == 60 * (v.tets ? 6 : 1));
      const MeshGeometry g = compute_geometry(mesh);
      const MeshCheckReport r = check_geometry(mesh, g);
      CHECK(r.ok());
      CHECK(r.max_closure_residual < 1e-13);
      CHECK(r.min_volume > 0.0);
      CHECK(r.n_orientation_warnings == 0);
      CHECK(r.total_volume == Approx(box_volume).epsilon(1e-13));
      // 側面の面積と外向き法線(側面は平面のまま)
      const std::vector<PatchSummary> patches = summarize_patches(mesh, g);
      REQUIRE(patches.size() == 6);
      const double areas[6] = {(hi.y - lo.y) * (hi.z - lo.z), (hi.y - lo.y) * (hi.z - lo.z),
                               (hi.x - lo.x) * (hi.z - lo.z), (hi.x - lo.x) * (hi.z - lo.z),
                               (hi.x - lo.x) * (hi.y - lo.y), (hi.x - lo.x) * (hi.y - lo.y)};
      Vec3 total_area_vector{};
      for (int p = 0; p < 6; ++p) {
        CHECK(patches[static_cast<std::size_t>(p)].area == Approx(areas[p]).epsilon(1e-13));
        Vec3 expected{};
        expected[p / 2] = (p % 2 == 0 ? -1.0 : 1.0) * areas[p];
        CHECK(max_abs_diff(patches[static_cast<std::size_t>(p)].area_vector, expected) < 1e-13);
        total_area_vector += patches[static_cast<std::size_t>(p)].area_vector;
      }
      CHECK(norm(total_area_vector) < 1e-13);
      // 構造格子(歪みなしの六面体)では、各セルの体積と重心が直方体のものと一致する
      if (!v.tets && v.distortion == 0.0) {
        for (Index c = 0; c < mesh.n_cells; ++c) {
          const auto ci = static_cast<std::size_t>(c);
          // セル c の節点の範囲から直方体の体積と中心を求める
          Vec3 cmin{1e30, 1e30, 1e30}, cmax{-1e30, -1e30, -1e30};
          for (Index f = 0; f < mesh.n_faces(); ++f) {
            const auto fi = static_cast<std::size_t>(f);
            if (mesh.owner[fi] != c && !(mesh.is_internal(f) && mesh.neighbour[fi] == c)) continue;
            for (const Index n : mesh.face(f)) {
              for (int a = 0; a < 3; ++a) {
                cmin[a] = std::min(cmin[a], mesh.points[static_cast<std::size_t>(n)][a]);
                cmax[a] = std::max(cmax[a], mesh.points[static_cast<std::size_t>(n)][a]);
              }
            }
          }
          const Vec3 ext = cmax - cmin;
          CHECK(g.cell_volume[ci] == Approx(ext.x * ext.y * ext.z).epsilon(1e-13));
          CHECK(max_abs_diff(g.cell_center[ci], 0.5 * (cmin + cmax)) < 1e-14);
        }
      }
    }
  }
}

TEST_CASE("geometry checks detect open cells, negative volumes and orientation", "[mesh][geometry]") {
  set_log_level(LogLevel::Error);
  const std::vector<Vec3> cube = {{0, 0, 0}, {1, 0, 0}, {1, 1, 0}, {0, 1, 0},
                                  {0, 0, 1}, {1, 0, 1}, {1, 1, 1}, {0, 1, 1}};
  SECTION("open cell (one face missing)") {
    const PolyMesh mesh =
        single_cell(cube, {{0, 4, 7, 3}, {1, 2, 6, 5}, {0, 1, 5, 4}, {3, 7, 6, 2}, {0, 3, 2, 1}});
    const MeshCheckReport r = check_geometry(mesh, compute_geometry(mesh));
    CHECK_FALSE(r.ok());
    CHECK(r.n_open_cells == 1);
    CHECK_THROWS_AS(validate_mesh(mesh), Error);
  }
  SECTION("inverted cell (all faces inward)") {
    const PolyMesh mesh = single_cell(
        cube, {{0, 3, 7, 4}, {1, 5, 6, 2}, {0, 4, 5, 1}, {3, 2, 6, 7}, {0, 1, 2, 3}, {4, 7, 6, 5}});
    const MeshCheckReport r = check_geometry(mesh, compute_geometry(mesh));
    CHECK(r.n_open_cells == 0);
    CHECK(r.n_nonpositive_volume == 1);
    CHECK_FALSE(r.ok());
  }
  SECTION("orientation warning is counted but is not an error") {
    test::BoxMeshOptions o;
    o.n = {3, 1, 1};
    const PolyMesh mesh = test::make_box_mesh(o);
    MeshGeometry g = compute_geometry(mesh);
    std::swap(g.cell_center[0], g.cell_center[1]);  // 内部面 0–1 の d を逆向きにする
    const MeshCheckReport r = check_geometry(mesh, g);
    CHECK(r.ok());
    CHECK(r.n_orientation_warnings >= 1);
    CHECK_FALSE(r.warnings.empty());
  }
  set_log_level(LogLevel::Info);
}
