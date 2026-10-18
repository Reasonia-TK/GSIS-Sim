#pragma once
// 一様デカルト速度格子(spec §3.3、仮定 A5)。
// - 各軸の区間 [lo, hi] を n 等分し、点は各小区間の中点に置く: ξ_i = lo + (i + 1/2) Δ、Δ = (hi − lo)/n
// - 積分は中点則。重みは全点で ΔξxΔξyΔξz
// - 1 次元の通し番号 k = i + nx (j + ny l)(x が最も速く変わる)
// - 鏡映の対応表: 軸 a の鏡映 ξ_a → −ξ_a に対応する点の番号(範囲が 0 に対して対称な軸だけ)
// 値の単位は呼び出し側で決める(ソルバー内部では sqrt(R T0) を単位とする無次元量)。

#include <array>
#include <span>
#include <vector>

#include "core/vec3.hpp"

namespace gsis {

class VelocityGrid {
 public:
  VelocityGrid() = default;
  VelocityGrid(std::array<int, 3> n, std::array<double, 3> lo, std::array<double, 3> hi);

  int size() const { return size_; }
  int n(int axis) const { return n_[static_cast<std::size_t>(axis)]; }
  double lo(int axis) const { return lo_[static_cast<std::size_t>(axis)]; }
  double hi(int axis) const { return hi_[static_cast<std::size_t>(axis)]; }
  double delta(int axis) const { return delta_[static_cast<std::size_t>(axis)]; }
  double weight() const { return weight_; }  // ΔξxΔξyΔξz

  // 軸 axis の i 番目の座標 = lo + (i + 1/2) Δ。
  // 桁落ちを避けるため、範囲の中心 c からの対称な形 c + (2i + 1 − n) Δ/2 で計算する
  // (範囲が対称なら c = 0 なので、鏡映した点の座標はビット単位で −ξ になる)。
  double coordinate(int axis, int i) const {
    const auto a = static_cast<std::size_t>(axis);
    return center_[a] + static_cast<double>(2 * i + 1 - n_[a]) * half_delta_[a];
  }
  int index(int i, int j, int l) const { return i + n_[0] * (j + n_[1] * l); }
  std::array<int, 3> multi_index(int k) const {
    return {k % n_[0], (k / n_[0]) % n_[1], k / (n_[0] * n_[1])};
  }
  Vec3 xi(int k) const {
    return {xs_[static_cast<std::size_t>(k)], ys_[static_cast<std::size_t>(k)],
            zs_[static_cast<std::size_t>(k)]};
  }

  // 全点の座標(量ごとの配列。GPU への転送用)
  const std::vector<double>& xs() const { return xs_; }
  const std::vector<double>& ys() const { return ys_; }
  const std::vector<double>& zs() const { return zs_; }
  std::vector<float> xs_f32() const { return to_f32(xs_); }
  std::vector<float> ys_f32() const { return to_f32(ys_); }
  std::vector<float> zs_f32() const { return to_f32(zs_); }

  // 範囲が 0 に対して対称(lo = −hi)な軸か
  bool symmetric(int axis) const { return symmetric_[static_cast<std::size_t>(axis)]; }
  // 軸 axis の鏡映の対応表(長さ size())。対称でない軸なら gsis::Error。
  const std::vector<int>& mirror(int axis) const;

 private:
  std::array<int, 3> n_{};
  std::array<double, 3> lo_{};
  std::array<double, 3> hi_{};
  std::array<double, 3> delta_{};
  std::array<double, 3> center_{};      // (lo + hi) / 2
  std::array<double, 3> half_delta_{};  // Δ / 2
  double weight_ = 0.0;
  int size_ = 0;
  std::vector<double> xs_, ys_, zs_;
  std::array<bool, 3> symmetric_{};
  std::array<std::vector<int>, 3> mirror_;

  static std::vector<float> to_f32(const std::vector<double>& v);
};

}  // namespace gsis
