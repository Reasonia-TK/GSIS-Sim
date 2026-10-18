#pragma once
// 運動論ソルバーの実行部分(CPU 参照実装と CUDA 版)の共通の口。CUDA のヘッダを含まない。
// ホストとのやり取りはすべて double・内部の番号付け(kinetic_setup.hpp)。
//
// 1 回の CIS 反復での呼び出しの順(kinetic_solver.cpp):
//   compute_moments → (閉じた領域なら scale_distribution) → update_cell_params →
//   set_inflow(圧力境界の W_b、壁は前回の ρ_Γ)→ boundary_sums → set_inflow(新しい ρ_Γ)→ sweep

#include <cstddef>
#include <memory>
#include <span>

#include "core/vec3.hpp"
#include "kinetic/kinetic_setup.hpp"

namespace gsis::kinetic {

// 処理ごとの時間 [s]
struct StepTimings {
  double moments = 0.0;
  double boundary = 0.0;
  double gradient = 0.0;
  double residual = 0.0;
  double sweep_forward = 0.0;
  double sweep_backward = 0.0;
  double other = 0.0;  // 係数の更新・規格化・転送など

  double total() const {
    return moments + boundary + gradient + residual + sweep_forward + sweep_backward + other;
  }
  StepTimings& operator+=(const StepTimings& o) {
    moments += o.moments;
    boundary += o.boundary;
    gradient += o.gradient;
    residual += o.residual;
    sweep_forward += o.sweep_forward;
    sweep_backward += o.sweep_backward;
    other += o.other;
    return *this;
  }
};

// 境界面の流入分布 scale · f^eq(rho, u, T)(無次元)。拡散壁は rho = 1・壁の u, T・scale = ρ_Γ、
// 圧力境界は W_b と scale = 1、真空は scale = 0
struct InflowState {
  double scale = 0.0;
  double rho = 0.0;
  Vec3 u{};
  double T = 1.0;
};

// 保存的な離散平衡分布の補正の記録(直前の update_cell_params の分)
struct DeqCounts {
  long long corrected = 0;  // ニュートン法で補正したセル
  long long failed = 0;     // ニュートン法が収束せず、連続の Maxwell 分布にしたセル
  int max_iterations = 0;   // ニュートン法の反復数の最大
};

class KineticBackend {
 public:
  virtual ~KineticBackend() = default;

  virtual const char* name() const = 0;
  virtual int chunk_width() const = 0;             // 速度チャンクの点数
  virtual std::size_t memory_bytes() const = 0;    // 確保した作業領域を含むメモリ量(GPU なら VRAM)

  // 分布関数。states は 1 セル 5 個(ρ, u_x, u_y, u_z, T)、f は [セル][保存位置]
  virtual void set_equilibrium(std::span<const double> states) = 0;
  virtual void set_distribution(std::span<const double> f) = 0;
  virtual void get_distribution(std::span<double> f) = 0;

  // モーメント(1 セル 15 個)。結果は内部にも残し、update_cell_params が使う
  virtual void compute_moments(std::span<double> out) = 0;
  // f と内部のモーメントを s 倍する(閉じた領域の全質量の規格化)
  virtual void scale_distribution(double s) = 0;
  // 内部のモーメントからセルの係数(Shakhov の目標分布、V/τ、対角)を作る
  virtual void update_cell_params() = 0;
  // 境界面ごとの流入分布
  virtual void set_inflow(std::span<const InflowState> inflow) = 0;
  // 境界面ごとの集計(1 面 20 個、kinetic_types.hpp の kBoundarySumsPerFace)
  virtual void boundary_sums(std::span<double> out) = 0;
  // 全チャンクの勾配・残差・LU-SGS・更新。residual_cell にセルごとの Σ_k R² を返す
  virtual void sweep(std::span<double> residual_cell) = 0;
  // GSIS の補正(式 (14))と負値の置換。w は 1 セル 5 個(ρ, u_x, u_y, u_z, T)。置き換えたセル数を返す。
  // replaced が空でなければ(長さはセル数)、置き換えたセルに 1、そのほかに 0 を書く
  virtual long long gsis_correction(std::span<const double> w_half, std::span<const double> w_new,
                                    std::span<int> replaced) = 0;

  // 内部面の質量流束 Σ_k ϖ (ξ_k·S_out) f_face,k(フェーズ 5c、断面の質量流量の集計用)。面 i はセル cells[i] の
  // CSR の要素 entries[i](内部面)で、向きはそのセルから見て外向き。面の値は残差と同じ風上側の制限付き線形再構成
  // (勾配・リミッタの境界の値には現在の流入分布を使うので、set_inflow の後に呼ぶ)。out は面ごとの値(無次元)
  virtual void internal_face_mass_flux(std::span<const std::int32_t> cells, std::span<const std::int32_t> entries,
                                       std::span<double> out) = 0;

  // 前回の呼び出しからの時間を返して 0 に戻す
  virtual StepTimings take_timings() = 0;

  // 直前の update_cell_params の離散平衡の補正の数([kinetic].equilibrium = "discrete" のとき)
  virtual DeqCounts deq_counts() = 0;
};

// CPU 参照実装(OpenMP。精度は setup.params.precision)
std::unique_ptr<KineticBackend> make_cpu_backend(const KineticSetup& setup);
// CUDA 版(GSIS_WITH_CUDA のときだけ使える。そうでなければ gsis::Error)
std::unique_ptr<KineticBackend> make_cuda_backend(const KineticSetup& setup);

}  // namespace gsis::kinetic
