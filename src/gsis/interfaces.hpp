#pragma once
// 運動論ソルバー(GPU、フェーズ 2)と PBC マクロソルバー(CPU、フェーズ 3)の間で受け渡すデータ。
// フェーズ 4 の GSIS の反復は、この型で両者をつなぐ(docs/design_cis_gpu.md §8、docs/design_pbc.md §1)。
//
// - すべてホスト側・FP64・無次元量(spec §3.2。無次元の気体定数は 1 なので p = ρ T)
// - セルの並びはメッシュのセル番号、境界面の並びは境界面の番号(面番号 − n_internal_faces)
// - σ の成分の順は xx, yy, zz, xy, yz, xz(VTK と同じ)
// - 半空間フラックスの成分は [質量, 運動量 x, y, z, エネルギー](全体座標、面積を掛けた値)

#include <array>
#include <cstddef>
#include <vector>

namespace gsis {

// セルごとのマクロ量(SoA)
struct CellMacroState {
  std::vector<double> rho, ux, uy, uz, T, p;

  std::size_t size() const { return rho.size(); }
  void resize(std::size_t n) {
    rho.resize(n);
    ux.resize(n);
    uy.resize(n);
    uz.resize(n);
    T.resize(n);
    p.resize(n);
  }
};

// CIS の後の運動論のモーメント(式 (5)(6))
struct KineticMoments {
  CellMacroState state;
  std::vector<std::array<double, 6>> sigma;  // 偏差応力 σ
  std::vector<std::array<double, 3>> q;      // 熱流束 q
};

// 境界面ごとの離散半空間フラックス(式 (35))と、境界面の分布関数のモーメント
struct BoundaryKineticFluxes {
  std::vector<std::array<double, 5>> F_plus;   // 流出側 Ξ⁺ の和(再構成値)
  std::vector<std::array<double, 5>> F_minus;  // 流入側 Ξ⁻ の和(境界条件の値)
  // 境界面の分布関数(流出側 + 流入側)のモーメント [ρ, ux, uy, uz, T]。
  // 境界セルの最小二乗勾配の境界値に使う(仮定 A7)
  std::vector<std::array<double, 5>> face_state;
};

// 高次項 HoT(式 (12))。セルごと
struct HigherOrderTerms {
  std::vector<std::array<double, 6>> sigma;
  std::vector<std::array<double, 3>> q;
};

}  // namespace gsis
