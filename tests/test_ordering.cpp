// mesh: RCM の並べ替え、番号の付け替え、連続区間のブロック分割

#include <algorithm>
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <numeric>
#include <random>
#include <vector>

#include "core/error.hpp"
#include "mesh/geometry.hpp"
#include "mesh/ordering.hpp"
#include "support/box_mesh.hpp"

using namespace gsis;
using Catch::Approx;

namespace {

PolyMesh shuffled_box(std::array<int, 3> n, bool tets, unsigned seed) {
  test::BoxMeshOptions o;
  o.n = n;
  o.tetrahedra = tets;
  const PolyMesh mesh = test::make_box_mesh(o);
  std::vector<Index> perm(static_cast<std::size_t>(mesh.n_cells));
  std::iota(perm.begin(), perm.end(), 0);
  std::mt19937 rng(seed);
  std::shuffle(perm.begin(), perm.end(), rng);
  return renumber_cells(mesh, perm);
}

}  // namespace

TEST_CASE("renumber_cells keeps the conventions and the geometry", "[mesh][ordering]") {
  test::BoxMeshOptions o;
  o.n = {4, 3, 3};
  o.distortion = 0.2;
  const PolyMesh mesh = test::make_box_mesh(o);
  std::vector<Index> perm(static_cast<std::size_t>(mesh.n_cells));
  std::iota(perm.begin(), perm.end(), 0);
  std::mt19937 rng(5);
  std::shuffle(perm.begin(), perm.end(), rng);

  const PolyMesh out = renumber_cells(mesh, perm);
  CHECK_NOTHROW(validate_topology(out));
  CHECK(out.n_faces() == mesh.n_faces());
  CHECK(out.n_internal_faces() == mesh.n_internal_faces());
  const MeshGeometry g0 = compute_geometry(mesh);
  const MeshGeometry g1 = compute_geometry(out);
  CHECK(check_geometry(out, g1).ok());
  for (Index i = 0; i < out.n_cells; ++i) {
    const auto old = static_cast<std::size_t>(perm[static_cast<std::size_t>(i)]);
    CHECK(g1.cell_volume[static_cast<std::size_t>(i)] == Approx(g0.cell_volume[old]).epsilon(1e-13));
    CHECK(norm(g1.cell_center[static_cast<std::size_t>(i)] - g0.cell_center[old]) < 1e-14);
  }
  // パッチの区間と面積は変わらない
  for (std::size_t p = 0; p < mesh.patches.size(); ++p) {
    CHECK(out.patches[p].start == mesh.patches[p].start);
    CHECK(out.patches[p].count == mesh.patches[p].count);
  }
}

TEST_CASE("invert_permutation", "[mesh][ordering]") {
  const std::vector<Index> p = {2, 0, 3, 1};
  const std::vector<Index> inv = invert_permutation(p);
  CHECK(inv == std::vector<Index>{1, 3, 0, 2});
  CHECK_THROWS_AS(invert_permutation(std::vector<Index>{0, 0, 1}), Error);
  CHECK_THROWS_AS(invert_permutation(std::vector<Index>{0, 3, 1}), Error);
}

TEST_CASE("RCM reduces the bandwidth of a shuffled mesh", "[mesh][ordering]") {
  for (const bool tets : {false, true}) {
    DYNAMIC_SECTION((tets ? "tetrahedra" : "hexahedra")) {
      const std::array<int, 3> n = {8, 5, 3};
      const PolyMesh natural = test::make_box_mesh([&] {
        test::BoxMeshOptions o;
        o.n = n;
        o.tetrahedra = tets;
        return o;
      }());
      const PolyMesh shuffled = shuffled_box(n, tets, 9);
      const std::vector<Index> order = rcm_ordering(shuffled);
      // 置換になっている
      std::vector<Index> sorted = order;
      std::sort(sorted.begin(), sorted.end());
      for (std::size_t i = 0; i < sorted.size(); ++i) REQUIRE(sorted[i] == static_cast<Index>(i));
      const PolyMesh rcm = renumber_cells(shuffled, order);
      CHECK_NOTHROW(validate_topology(rcm));
      const Index bw_shuffled = matrix_bandwidth(shuffled);
      const Index bw_natural = matrix_bandwidth(natural);
      const Index bw_rcm = matrix_bandwidth(rcm);
      INFO("bandwidth: shuffled " << bw_shuffled << ", natural " << bw_natural << ", RCM " << bw_rcm);
      CHECK(bw_rcm < bw_shuffled / 2);
      CHECK(bw_rcm <= bw_natural);
      // 決定的(同じ入力なら同じ順序)
      CHECK(rcm_ordering(shuffled) == order);
    }
  }
}

TEST_CASE("RCM handles several connected components", "[mesh][ordering]") {
  // 離れた 2 つの箱を 1 つのメッシュにする
  test::BoxMeshOptions o;
  o.n = {3, 2, 2};
  PolyMesh a = test::make_box_mesh(o);
  o.lo = {5.0, 0.0, 0.0};
  o.hi = {6.0, 1.0, 1.0};
  const PolyMesh b = test::make_box_mesh(o);
  // セルの一覧を作り直して結合する
  std::vector<Vec3> points = a.points;
  points.insert(points.end(), b.points.begin(), b.points.end());
  std::vector<std::vector<std::vector<Index>>> cells(static_cast<std::size_t>(a.n_cells + b.n_cells));
  auto add_cells = [&](const PolyMesh& m, Index cell_offset, Index point_offset) {
    for (Index f = 0; f < m.n_faces(); ++f) {
      std::vector<Index> nodes;
      for (const Index p : m.face(f)) nodes.push_back(p + point_offset);
      cells[static_cast<std::size_t>(m.owner[static_cast<std::size_t>(f)] + cell_offset)].push_back(nodes);
      if (m.is_internal(f)) {
        std::reverse(nodes.begin(), nodes.end());
        cells[static_cast<std::size_t>(m.neighbour[static_cast<std::size_t>(f)] + cell_offset)].push_back(nodes);
      }
    }
  };
  add_cells(a, 0, 0);
  add_cells(b, a.n_cells, a.n_points());
  const PolyMesh two = test::build_poly_mesh(points, cells, {{"walls", "wall"}}, [](const Vec3&) { return 0; });
  const std::vector<Index> order = rcm_ordering(two);
  CHECK(invert_permutation(order).size() == static_cast<std::size_t>(two.n_cells));
  const PolyMesh rcm = renumber_cells(two, order);
  CHECK(check_geometry(rcm, compute_geometry(rcm)).ok());
}

TEST_CASE("contiguous block partition", "[mesh][ordering]") {
  const std::vector<Index> p = partition_contiguous(10, 3);
  CHECK(p == std::vector<Index>{0, 4, 7, 10});
  CHECK_THROWS_AS(partition_contiguous(2, 3), Error);
  CHECK_THROWS_AS(partition_contiguous(2, 0), Error);

  // 重み付き: 前半のセルが重いと、前半のブロックが小さくなる
  std::vector<double> w(100, 1.0);
  for (int i = 0; i < 20; ++i) w[static_cast<std::size_t>(i)] = 5.0;
  const std::vector<Index> pw = partition_contiguous_weighted(w, 4);
  REQUIRE(pw.size() == 5);
  CHECK(pw.front() == 0);
  CHECK(pw.back() == 100);
  for (std::size_t b = 0; b + 1 < pw.size(); ++b) CHECK(pw[b] < pw[b + 1]);
  CHECK(pw[1] == 9);  // 重みの合計 180 の 1/4 = 45 → 5 × 9 = 45

  // 1 列のセル(n×1×1)を P 個に分けると、ブロック間の面は P − 1
  test::BoxMeshOptions o;
  o.n = {12, 1, 1};
  const PolyMesh chain = test::make_box_mesh(o);
  const std::vector<Index> blocks = partition_contiguous(chain.n_cells, 4);
  const BlockPartitionStats s = block_partition_stats(chain, blocks);
  CHECK(s.n_blocks == 4);
  CHECK(s.min_cells == 3);
  CHECK(s.max_cells == 3);
  CHECK(s.n_cut_faces == 3);
  const std::vector<int> ids = cell_block_ids(blocks);
  CHECK(ids[0] == 0);
  CHECK(ids[11] == 3);
}
