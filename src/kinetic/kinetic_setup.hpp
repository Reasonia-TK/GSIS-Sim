#pragma once
// 運動論ソルバーの前処理(ホスト側): セルの並べ替え、無次元の幾何量、速度点の保存順、
// LU-SGS のブロック分割、境界条件の表。CPU 参照実装と GPU 版の両方がこれを使う。
//
// 番号付け:
//   - 「元の番号」: 入力メッシュのセル・境界面の番号(出力はすべて元の番号に戻す)
//   - 「内部の番号」: cell_ordering = "rcm" なら RCM で並べ替えた番号(LU-SGS のスイープの順)
// 速度点の保存順(VelocityLayout):
//   対称面で使う軸の鏡映で移り合う点(軌道、大きさ 2^軸数)を連続に並べる。速度チャンクの境界を
//   軌道の大きさの倍数にすると、鏡映した点が常に同じチャンクに入る。保存位置 p の鏡映は p ^ axis_xor[a]

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "config/case_config.hpp"
#include "core/types.hpp"
#include "core/vec3.hpp"
#include "kinetic/kinetic_params.hpp"
#include "kinetic/kinetic_types.hpp"
#include "mesh/fv_geometry.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"
#include "velocity/velocity_grid.hpp"

namespace gsis::kinetic {

struct VelocityLayout {
  int nv = 0;
  int orbit_size = 1;                      // 2^(鏡映を使う軸の数)
  std::array<int, 3> axis_xor{0, 0, 0};    // 軸 a の鏡映: p → p ^ axis_xor[a](使わない軸は 0)
  std::vector<int> storage_to_grid;        // 保存位置 → 速度格子の番号
  std::vector<int> grid_to_storage;
  std::vector<double> xi_x, xi_y, xi_z;    // 保存位置の順(無次元、単位 √(R T0))
  double weight = 0.0;                     // ϖ = ΔξxΔξyΔξz
  double xi_max = 0.0;                     // max |ξ|
};

// 境界パッチの条件(無次元)
struct PatchCondition {
  std::string name;
  BoundaryType type = BoundaryType::DiffuseWall;
  BoundaryKind kind = kBcDiffuseWall;
  Index first_bface = 0;  // 境界面の番号(0 始まり)の範囲 [first_bface, first_bface + n_faces)
  Index n_faces = 0;
  double accommodation = 1.0;
  double target_mass_flow = 0.0;  // 無次元、流入正
  double T = 1.0;         // 壁温(拡散壁)・入口温度(圧力入口)
  double p = 0.0;         // 圧力(圧力入口・出口)
  Vec3 u_wall{};          // 壁速度(拡散壁)
  int mirror_axis = -1;   // 対称面の法線の軸
  bool zero_tangential = false;  // 圧力入口の流入の速度の接線成分を 0 にする(BoundarySpec::inlet_zero_tangential)
};

struct KineticSetup {
  KineticParams params;
  LimiterMode limiter_mode = kLimiterVenkatAbsolute;
  DerivedQuantities derived;  // 次元のある量との換算(derived.ref が無次元化の基準)
  double length_scale = 1.0;  // L_ref [m]

  // 番号の対応(new = 内部の番号、old = 元の番号)。境界面は 0 始まりの境界面の番号
  std::vector<Index> cell_new_to_old;
  std::vector<Index> cell_old_to_new;
  std::vector<Index> bface_new_to_old;
  std::vector<Index> bface_old_to_new;

  // 内部の番号付けのメッシュと幾何(長さは m のまま)
  PolyMesh mesh;
  MeshGeometry geometry;
  CellFaceCsr csr;
  LsqGradient lsq;

  // 無次元の量(内部の番号)
  std::vector<double> volume;  // V
  std::vector<double> vdt;     // V/Δt(Δt = ∞ または pseudo_time = "velocity" なら 0)
  double diag_scale = 1.0;     // LU-SGS の対角の Σa⁺ に掛ける係数(pseudo_time = "velocity" で 1 + 1/CFL)

  VelocityLayout velocity;
  std::vector<Index> block_offsets;  // LU-SGS の空間ブロック(内部の番号の連続区間)

  // LU-SGS のスイープの順序(フェーズ 5a、[kinetic].sweep_order。"block" なら n_sweep_dirs = 0 で使わない)。
  // 向き d_g(g < n_sweep_dirs、符号は区別しない)ごとに、各ブロックの中のセルを x·d_g の小さい順に並べる。
  //   sweep_order[g × n_cells + k]: k 番目のセル(ブロック b の区間 [block_offsets[b], block_offsets[b+1]) の中の並べ替え)
  //   sweep_pos[g × n_cells + c]:   セル c の位置 k
  //   sweep_dir_of[p]:              速度点(保存位置 p)が使う向き g(|ξ·d_g|/|d_g| が最大のもの)
  // 速度点 ξ の風上のセルは、ξ·d_g > 0 なら x·d_g の小さい側(前進スイープで解く)、< 0 なら大きい側(後退)
  // 保存的な離散平衡分布([kinetic].equilibrium = "discrete")の速度格子の情報と打ち切り
  bool discrete_eq = false;
  DeqGrid deq_grid;

  int n_sweep_dirs = 0;
  std::vector<Vec3> sweep_dirs;
  std::vector<std::int32_t> sweep_order;
  std::vector<std::int32_t> sweep_pos;
  std::vector<std::uint8_t> sweep_dir_of;

  std::vector<PatchCondition> patches;  // メッシュのパッチの順
  std::vector<int> bface_patch;         // 境界面(内部の番号)→ パッチ番号
  std::vector<MaxwellFace> maxwell;  // 部分適応壁があるときだけ境界面数を確保
  int n_maxwell_faces = 0;
  std::array<int, 3> grid_n{};
  bool closed_domain = false;           // 圧力境界(入口・出口・真空)がない

  // 気体(無次元の粘性 μ = mu0 T^ω)
  double mu0 = 0.0;
  double omega = 0.0;
  double Pr = 2.0 / 3.0;
  double gamma = 5.0 / 3.0;

  int n_cells() const { return mesh.n_cells; }
  int n_bfaces() const { return mesh.n_boundary_faces(); }
  int nv() const { return velocity.nv; }
  int n_blocks() const { return static_cast<int>(block_offsets.size()) - 1; }
};

// ケースとメッシュから前処理をする。ケースとメッシュが合わなければ gsis::Error。
KineticSetup build_kinetic_setup(const CaseConfig& config, const PolyMesh& mesh, const MeshGeometry& geometry,
                                 const KineticParams& params);

// 速度点の保存順を作る(mirror_axes は鏡映を使う軸。範囲が対称で点数が偶数であること)。
// sweep_dirs が空でなく鏡映を使わないなら、スイープの向き(sweep_direction_of)ごとに速度点をまとめて並べる
// (同じ warp の速度点が同じセルの順序をたどるようにする。向きの中は速度格子の順)
VelocityLayout make_velocity_layout(const VelocityGrid& grid, const std::vector<int>& mirror_axes,
                                    const std::vector<Vec3>& sweep_dirs = {});

// sweep_order の向きの組("octant": 体対角 4、"dir13": 軸 3 + 面対角 6 + 体対角 4。単位ベクトル。"block" は空)
std::vector<Vec3> make_sweep_directions(const std::string& sweep_order);
// 速度 ξ が使う向き(|ξ·d_g| が最大の g。同じ値なら番号の小さい方)
int sweep_direction_of(const std::vector<Vec3>& dirs, const Vec3& xi);

// 実行側に渡す表(精度ごと)
std::vector<std::int32_t> make_csr_offsets(const KineticSetup& s);
template <class F>
std::vector<CsrEntry<F>> make_csr_entries(const KineticSetup& s);
template <class F>
std::vector<BoundaryFaceInfo<F>> make_bface_infos(const KineticSetup& s);
template <class F>
std::vector<F> make_eps2(const KineticSetup& s);  // K³ V

}  // namespace gsis::kinetic
