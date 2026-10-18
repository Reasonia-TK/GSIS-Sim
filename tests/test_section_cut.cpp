// mesh: 断面の面の集まり(フェーズ 5c、mesh/section_cut.hpp)
//   - 位置の並びと軸の読み取り
//   - 一様な箱で、断面の面が位置の前後のセルの間の面の層になる(数・面積・位置)
//   - 歪んだ六面体・四面体でも、断面の投影面積が箱の断面積に厳密に一致する(断面が領域を前後に分ける)
//   - 断面の流量 Σ sign F

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <string>
#include <vector>

#include "core/error.hpp"
#include "mesh/geometry.hpp"
#include "mesh/section_cut.hpp"
#include "support/box_mesh.hpp"

using namespace gsis;
using Catch::Approx;

TEST_CASE("section cut: parse the positions and the axis", "[mesh][section]") {
  const SectionSpec s = parse_section_spec("0, 2.5;5  7.5,10");
  CHECK(s.axis == 0);
  REQUIRE(s.positions.size() == 5);
  CHECK(s.positions[1] == 2.5);
  CHECK(s.positions[4] == 10.0);
  CHECK(parse_section_spec("", "z").empty());
  CHECK(parse_section_spec(" , ", "x").empty());
  const SectionSpec y = parse_section_spec("-1e-1", "y");
  CHECK(y.axis == 1);
  CHECK(y.positions == std::vector<double>{-0.1});
  CHECK(std::string(section_axis_name(2)) == "z");
  CHECK_THROWS_AS(parse_section_spec("1, abc"), Error);
  CHECK_THROWS_AS(parse_section_spec("1", "w"), Error);
}

TEST_CASE("section cut: the faces between the cells before and after the plane", "[mesh][section]") {
  // 一様な箱 [0, 2] × [0, 1] × [0, 0.5] m、x 方向に 4 セル。L = 0.5 m なので無次元で [0, 4] × [0, 2] × [0, 1]
  test::BoxMeshOptions o;
  o.n = {4, 3, 2};
  o.hi = {2.0, 1.0, 0.5};
  const PolyMesh mesh = test::make_box_mesh(o);
  const MeshGeometry g = compute_geometry(mesh);
  const double L = 0.5;
  SectionSpec spec;
  spec.positions = {2.0, 1.3, 9.0, -1.0};
  const std::vector<SectionCut> cuts = build_section_cuts(mesh, g, L, spec);
  REQUIRE(cuts.size() == 4);
  // X = 2: セル中心 x = 0.5, 1.5, 2.5, 3.5 の 2 列目と 3 列目の間の面(x = 2)
  CHECK(cuts[0].faces.size() == 6);
  CHECK(cuts[0].area == Approx(2.0));
  CHECK(cuts[0].projected_area == Approx(2.0));
  CHECK(cuts[0].face_position == Approx(2.0));
  for (const signed char sg : cuts[0].sign) CHECK((sg == 1 || sg == -1));
  // X = 1.3: x = 1 の面
  CHECK(cuts[1].faces.size() == 6);
  CHECK(cuts[1].face_position == Approx(1.0));
  // 領域の外は面なし(位置は指定の値)
  CHECK(cuts[2].faces.empty());
  CHECK(cuts[3].faces.empty());
  CHECK(cuts[2].face_position == 9.0);
  // 一様な流束 F_f = S_f · e_x(無次元)なら、断面の流量は投影面積
  std::vector<double> flux(static_cast<std::size_t>(mesh.n_internal_faces()));
  for (std::size_t f = 0; f < flux.size(); ++f) flux[f] = g.face_area_vector[f].x / (L * L);
  CHECK(section_flow(cuts[0], flux) == Approx(2.0));
  CHECK(section_flow(cuts[2], flux) == 0.0);
  // y の断面
  SectionSpec sy;
  sy.axis = 1;
  sy.positions = {1.0};
  const std::vector<SectionCut> cy = build_section_cuts(mesh, g, L, sy);
  CHECK(cy[0].projected_area == Approx(4.0));  // 4 × 1
}

TEST_CASE("section cut: on distorted and tetrahedral meshes the cut separates the domain", "[mesh][section]") {
  for (const bool tets : {false, true}) {
    test::BoxMeshOptions o;
    o.n = {5, 3, 3};
    o.hi = {1.0, 0.6, 0.6};
    o.distortion = 0.25;
    o.tetrahedra = tets;
    o.seed = 7;
    const PolyMesh mesh = test::make_box_mesh(o);
    const MeshGeometry g = compute_geometry(mesh);
    SectionSpec spec;
    spec.positions = {0.3, 0.5, 0.71};
    for (const SectionCut& c : build_section_cuts(mesh, g, 1.0, spec)) {
      // 側面は平らなので、断面(階段状の面)の x の向きの投影面積は厳密に箱の断面積 0.36
      CHECK(!c.faces.empty());
      CHECK(c.projected_area == Approx(0.36).epsilon(1e-12));
      CHECK(c.area >= c.projected_area * (1.0 - 1e-12));
      CHECK(std::abs(c.face_position - c.position) < 0.3);
    }
  }
}
