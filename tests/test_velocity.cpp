// velocity: 一様デカルト速度格子(中点配置)、番号の変換、重み、鏡映の対応表

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "core/error.hpp"
#include "velocity/velocity_grid.hpp"

using namespace gsis;
using Catch::Approx;

TEST_CASE("tube velocity grid (30x24x24, midpoint placement)", "[velocity]") {
  const VelocityGrid g({30, 24, 24}, {-10.0, -8.0, -8.0}, {10.0, 8.0, 8.0});
  CHECK(g.size() == 17280);
  for (int a = 0; a < 3; ++a) CHECK(g.delta(a) == Approx(2.0 / 3.0).epsilon(1e-15));  // spec §3.3
  CHECK(g.weight() == Approx(8.0 / 27.0).epsilon(1e-15));
  CHECK(g.weight() * g.size() == Approx(20.0 * 16.0 * 16.0).epsilon(1e-14));
  CHECK(g.coordinate(0, 0) == Approx(-10.0 + 1.0 / 3.0).epsilon(1e-15));
  CHECK(g.coordinate(0, 29) == Approx(10.0 - 1.0 / 3.0).epsilon(1e-15));
  CHECK(g.coordinate(1, 12) == Approx(1.0 / 3.0).epsilon(1e-15));
  // 中点配置なので 0 の点はない
  for (int k = 0; k < g.size(); ++k) {
    const Vec3 xi = g.xi(k);
    REQUIRE(xi.x != 0.0);
    REQUIRE(xi.y != 0.0);
    REQUIRE(xi.z != 0.0);
  }
  // FP32 の写し
  CHECK(g.xs_f32().size() == 17280);
  CHECK(g.xs_f32()[5] == static_cast<float>(g.xs()[5]));
}

TEST_CASE("velocity index conversion", "[velocity]") {
  const VelocityGrid g({5, 4, 3}, {-1.0, -2.0, 0.0}, {1.0, 2.0, 3.0});
  CHECK(g.size() == 60);
  for (int k = 0; k < g.size(); ++k) {
    const auto ijk = g.multi_index(k);
    CHECK(g.index(ijk[0], ijk[1], ijk[2]) == k);
    const Vec3 xi = g.xi(k);
    CHECK(xi.x == g.coordinate(0, ijk[0]));
    CHECK(xi.y == g.coordinate(1, ijk[1]));
    CHECK(xi.z == g.coordinate(2, ijk[2]));
  }
  CHECK(g.index(1, 0, 0) == 1);  // x が最も速く変わる
  CHECK(g.index(0, 1, 0) == 5);
  CHECK(g.index(0, 0, 1) == 20);
}

TEST_CASE("velocity mirror tables", "[velocity]") {
  const VelocityGrid g({6, 5, 4}, {-3.0, -2.5, -2.0}, {3.0, 2.5, 2.0});
  for (int a = 0; a < 3; ++a) {
    REQUIRE(g.symmetric(a));
    const std::vector<int>& m = g.mirror(a);
    REQUIRE(m.size() == static_cast<std::size_t>(g.size()));
    for (int k = 0; k < g.size(); ++k) {
      const Vec3 xi = g.xi(k);
      const Vec3 xm = g.xi(m[static_cast<std::size_t>(k)]);
      for (int b = 0; b < 3; ++b) {
        if (b == a) {
          CHECK(xm[b] == -xi[b]);  // 対称な範囲ではビット単位で一致する
        } else {
          CHECK(xm[b] == xi[b]);
        }
      }
      CHECK(m[static_cast<std::size_t>(m[static_cast<std::size_t>(k)])] == k);  // 2 回で元に戻る
    }
  }
  // 範囲が対称でない軸には鏡映がない
  const VelocityGrid h({4, 4, 4}, {-1.0, -1.0, 0.0}, {1.0, 1.0, 2.0});
  CHECK(h.symmetric(0));
  CHECK_FALSE(h.symmetric(2));
  CHECK_THROWS_AS(h.mirror(2), Error);
}

TEST_CASE("velocity grid rejects invalid input", "[velocity]") {
  CHECK_THROWS_AS(VelocityGrid({0, 4, 4}, {-1, -1, -1}, {1, 1, 1}), Error);
  CHECK_THROWS_AS(VelocityGrid({4, 4, 4}, {1, -1, -1}, {-1, 1, 1}), Error);
}
