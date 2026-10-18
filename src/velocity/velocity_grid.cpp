#include "velocity/velocity_grid.hpp"

#include <cmath>
#include <format>

#include "core/error.hpp"

namespace gsis {

VelocityGrid::VelocityGrid(std::array<int, 3> n, std::array<double, 3> lo, std::array<double, 3> hi)
    : n_(n), lo_(lo), hi_(hi) {
  long long total = 1;
  for (std::size_t a = 0; a < 3; ++a) {
    GSIS_CHECK(n_[a] >= 1, std::format("速度格子の軸 {} の点数 {} は 1 以上にしてください", a, n_[a]));
    GSIS_CHECK(std::isfinite(lo_[a]) && std::isfinite(hi_[a]) && lo_[a] < hi_[a],
               std::format("速度格子の軸 {} の範囲 [{}, {}] が不正です", a, lo_[a], hi_[a]));
    delta_[a] = (hi_[a] - lo_[a]) / n_[a];
    half_delta_[a] = (hi_[a] - lo_[a]) / (2.0 * n_[a]);
    center_[a] = 0.5 * (lo_[a] + hi_[a]);
    total *= n_[a];
  }
  GSIS_CHECK(total <= 100'000'000, std::format("速度点が多すぎます({} 点)", total));
  size_ = static_cast<int>(total);
  weight_ = delta_[0] * delta_[1] * delta_[2];

  xs_.resize(static_cast<std::size_t>(size_));
  ys_.resize(static_cast<std::size_t>(size_));
  zs_.resize(static_cast<std::size_t>(size_));
  for (int l = 0; l < n_[2]; ++l) {
    for (int j = 0; j < n_[1]; ++j) {
      for (int i = 0; i < n_[0]; ++i) {
        const auto k = static_cast<std::size_t>(index(i, j, l));
        xs_[k] = coordinate(0, i);
        ys_[k] = coordinate(1, j);
        zs_[k] = coordinate(2, l);
      }
    }
  }

  // 鏡映: 範囲が対称なら、中点配置では i → n − 1 − i がちょうど −ξ に対応する
  for (int a = 0; a < 3; ++a) {
    const auto as = static_cast<std::size_t>(a);
    const double scale = hi_[as] - lo_[as];
    symmetric_[as] = std::abs(lo_[as] + hi_[as]) <= 1e-12 * scale;
    if (!symmetric_[as]) continue;
    std::vector<int>& m = mirror_[as];
    m.resize(static_cast<std::size_t>(size_));
    for (int k = 0; k < size_; ++k) {
      std::array<int, 3> ijk = multi_index(k);
      ijk[as] = n_[as] - 1 - ijk[as];
      m[static_cast<std::size_t>(k)] = index(ijk[0], ijk[1], ijk[2]);
    }
  }
}

const std::vector<int>& VelocityGrid::mirror(int axis) const {
  GSIS_CHECK(axis >= 0 && axis < 3, std::format("軸の番号 {} が不正です", axis));
  GSIS_CHECK(symmetric(axis),
             std::format("速度格子の軸 {} の範囲 [{}, {}] が 0 に対して対称でないので、鏡映の対応表が"
                         "ありません",
                         axis, lo(axis), hi(axis)));
  return mirror_[static_cast<std::size_t>(axis)];
}

std::vector<float> VelocityGrid::to_f32(const std::vector<double>& v) {
  std::vector<float> out(v.size());
  for (std::size_t i = 0; i < v.size(); ++i) out[i] = static_cast<float>(v[i]);
  return out;
}

}  // namespace gsis
