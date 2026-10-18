#include "macro/pbc_solver.hpp"

#include <algorithm>
#include <cmath>
#include <format>
#include <limits>

#include "core/error.hpp"
#include "core/log.hpp"
#include "physics/limiter.hpp"

namespace gsis {

using namespace macro;

namespace {

constexpr int kNumVars = 5;  // p, ux, uy, uz, T

inline std::size_t zu(Index i) { return static_cast<std::size_t>(i); }
inline std::size_t zu(Offset i) { return static_cast<std::size_t>(i); }

MacroBoundaryKind to_macro_kind(BoundaryType t) {
  switch (t) {
    case BoundaryType::DiffuseWall:
      return MacroBoundaryKind::DiffuseWall;
    case BoundaryType::PressureInlet:
      return MacroBoundaryKind::PressureInlet;
    case BoundaryType::PressureOutlet:
      return MacroBoundaryKind::PressureOutlet;
    case BoundaryType::Vacuum:
      return MacroBoundaryKind::Vacuum;
    case BoundaryType::Symmetry:
      return MacroBoundaryKind::Symmetry;
    case BoundaryType::PeriodicSlab:
      return MacroBoundaryKind::PeriodicSlab;
  }
  return MacroBoundaryKind::DiffuseWall;
}

// 幾何量を長さ L で割って無次元にする
MeshGeometry scale_geometry(const MeshGeometry& g, double L) {
  GSIS_CHECK(L > 0.0, "長さの基準量は正にしてください");
  MeshGeometry s = g;
  const double L2 = L * L;
  const double L3 = L2 * L;
  for (Vec3& v : s.face_area_vector) v /= L2;
  for (double& a : s.face_area) a /= L2;
  for (Vec3& x : s.face_center) x /= L;
  for (double& v : s.cell_volume) v /= L3;
  for (Vec3& x : s.cell_center) x /= L;
  return s;
}

// 体積重みの 2 ノルムの比 sqrt(Σ a_i V_i / Σ b_i V_i)(有限でなければ NaN を返す)
double ratio_norm(std::span<const double> num, std::span<const double> den) {
  const double d = deterministic_sum(den);
  const double n = deterministic_sum(num);
  if (!std::isfinite(n) || !std::isfinite(d)) return std::numeric_limits<double>::quiet_NaN();
  if (!(d > 0.0)) return n > 0.0 ? std::numeric_limits<double>::infinity() : 0.0;
  return std::sqrt(n / d);
}

}  // namespace

// ---------------------------------------------------------------------------
// 構築
// ---------------------------------------------------------------------------

PbcSolver::PbcSolver(const PolyMesh& mesh, const MeshGeometry& geometry, double length_scale, const MacroGas& gas,
                     std::vector<MacroBoundaryCondition> patch_bcs, const MacroParams& params)
    : mesh_(mesh), geo_(scale_geometry(geometry, length_scale)), gas_(gas), patch_bc_(std::move(patch_bcs)) {
  GSIS_CHECK(patch_bc_.size() == mesh_.patches.size(),
             std::format("境界条件の数 {} がパッチの数 {} と違います", patch_bc_.size(), mesh_.patches.size()));
  GSIS_CHECK(gas_.mu_ref > 0.0 && gas_.T_ref > 0.0, "気体の粘性の基準値は正にしてください");
  validate_macro_params(params);
  params_ = params;
  fv_ = compute_fv_geometry(mesh_, geo_);
  GSIS_CHECK(fv_.n_nonpositive_dn == 0,
             std::format("d_n <= 0 の内部面が {} 面あります(PBC の面勾配が計算できません)", fv_.n_nonpositive_dn));
  csr_ = build_cell_faces(mesh_);
  lsq_ = build_lsq_gradient(mesh_, geo_, csr_, LsqOptions{params_.lsq_weight_power});
  bface_patch_ = mesh_.boundary_face_patches();
  far_field_faces_.resize(patch_bc_.size());

  closed_domain_ = true;
  for (std::size_t p = 0; p < patch_bc_.size(); ++p) {
    if (mesh_.patches[p].count == 0) continue;
    const MacroBoundaryKind k = patch_bc_[p].kind;
    if (k == MacroBoundaryKind::PressureInlet || k == MacroBoundaryKind::PressureOutlet ||
        k == MacroBoundaryKind::Vacuum || k == MacroBoundaryKind::FarField) {
      closed_domain_ = false;
    }
    if (k == MacroBoundaryKind::DiffuseWall) {
      GSIS_CHECK(patch_bc_[p].temperature > 0.0, "拡散壁の温度は正にしてください");
    }
    if (k == MacroBoundaryKind::PressureInlet) {
      GSIS_CHECK(patch_bc_[p].temperature > 0.0 && patch_bc_[p].pressure > 0.0,
                 "圧力入口の温度と圧力は正にしてください");
    }
    if (k == MacroBoundaryKind::PressureOutlet) {
      GSIS_CHECK(patch_bc_[p].pressure >= 0.0, "圧力出口の圧力は 0 以上にしてください");
    }
  }
  // 閉じた領域では、体積が最大のセル(同じなら番号の小さいセル)の Δp を 0 に固定する
  pin_cell_ = 0;
  for (Index c = 1; c < mesh_.n_cells; ++c) {
    if (geo_.cell_volume[zu(c)] > geo_.cell_volume[zu(pin_cell_)]) pin_cell_ = c;
  }

  build_pattern();

  const auto nc = zu(mesh_.n_cells);
  const auto nif = zu(mesh_.n_internal_faces());
  const auto nbf = zu(mesh_.n_boundary_faces());
  for (int v = 0; v < kNumVars; ++v) {
    W_[v].assign(nc, v == 0 || v == 4 ? 1.0 : 0.0);
    grad_[v].assign(nc, Vec3{});
    lim_[v].assign(nc, 1.0);
  }
  rho_.assign(nc, 1.0);
  mu_.assign(nc, 0.0);
  kappa_.assign(nc, 0.0);
  cs_.assign(nc, 0.0);
  a_u_.assign(nc, 0.0);
  D_.assign(nc, 0.0);
  R_.assign(nc, {});
  defect_.assign(nc, 0.0);
  lambda_.assign(nc, 0.0);
  dt_.assign(nc, 0.0);
  hot_sigma_.assign(nc, {});
  hot_q_.assign(nc, Vec3{});
  face_res_.resize(nif);
  face_jac_.resize(nif);
  face_ckappa_.assign(nif, 0.0);
  face_lam_o_.assign(nif, 0.0);
  face_lam_n_.assign(nif, 0.0);
  mdot_hat_.assign(nif, 0.0);
  face_state_.assign(nbf, vec5_zero());
  next_face_state_.assign(nbf, vec5_zero());
  C_plus_.assign(nbf, vec5_zero());
  C_minus_.assign(nbf, vec5_zero());
  bW_.assign(nbf, vec5_zero());
  bF_.assign(nbf, vec5_zero());
  bJ_.assign(nbf, mat5_zero());
  bLam_.assign(nbf, 0.0);
  bBnorm_.assign(nbf, 0.0);
  set_params(params);
}

void PbcSolver::set_params(const MacroParams& params) {
  validate_macro_params(params);
  if (params.lsq_weight_power != params_.lsq_weight_power) {
    lsq_ = build_lsq_gradient(mesh_, geo_, csr_, LsqOptions{params.lsq_weight_power});
  }
  params_ = params;
  linear_.solver = params_.linear_solver;
  linear_.preconditioner = params_.preconditioner;
  linear_.rtol = params_.linear_rtol;
  linear_.max_iterations = params_.linear_max_iterations;
  linear_.gmres_restart = static_cast<int>(params_.gmres_restart);
  eps2_.resize(zu(mesh_.n_cells));
  for (Index c = 0; c < mesh_.n_cells; ++c) {
    eps2_[zu(c)] = venkatakrishnan_eps2(params_.limiter_k, geo_.cell_volume[zu(c)]);
  }
}

PbcSolver PbcSolver::from_case(const CaseConfig& config, const DerivedQuantities& derived, const PolyMesh& mesh,
                               const MeshGeometry& geometry, const MacroParams& params) {
  const ReferenceScales& ref = derived.ref;
  MacroGas gas;
  gas.mu_ref = config.gas.mu_ref / ref.viscosity;
  gas.T_ref = config.gas.T_ref / ref.temperature;
  gas.omega = config.gas.omega;
  gas.Pr = config.gas.Pr;

  std::vector<MacroBoundaryCondition> bcs;
  for (const Patch& p : mesh.patches) {
    const BoundarySpec* s = config.find_boundary(p.name);
    GSIS_CHECK(s != nullptr, std::format("パッチ \"{}\" の境界条件がケース定義にありません", p.name));
    MacroBoundaryCondition bc;
    bc.kind = to_macro_kind(s->type);
    switch (s->type) {
      case BoundaryType::DiffuseWall:
        bc.temperature = *s->temperature / ref.temperature;
        if (s->velocity) bc.velocity = *s->velocity / ref.velocity;
        break;
      case BoundaryType::PressureInlet:
        bc.temperature = *s->temperature / ref.temperature;
        bc.pressure = (s->pressure ? *s->pressure : derived.p_in) / ref.pressure;
        bc.zero_tangential = s->inlet_zero_tangential;
        break;
      case BoundaryType::PressureOutlet:
        bc.pressure = *s->pressure / ref.pressure;
        break;
      case BoundaryType::Vacuum:
      case BoundaryType::Symmetry:
      case BoundaryType::PeriodicSlab:
        break;
    }
    bcs.push_back(bc);
  }
  for (const BoundarySpec& s : config.boundaries) {
    GSIS_CHECK(mesh.find_patch(s.patch) >= 0,
               std::format("境界条件のパッチ \"{}\" がメッシュにありません", s.patch));
  }
  return PbcSolver(mesh, geometry, ref.length, gas, std::move(bcs), params);
}

void PbcSolver::set_far_field_states(int patch, std::vector<Vec5> states) {
  GSIS_CHECK(patch >= 0 && static_cast<std::size_t>(patch) < patch_bc_.size(), "パッチの番号が範囲外です");
  GSIS_CHECK(states.empty() || static_cast<Index>(states.size()) == mesh_.patches[zu(patch)].count,
             "遠方場の状態の数がパッチの面数と違います");
  far_field_faces_[zu(patch)] = std::move(states);
}

void PbcSolver::build_pattern() {
  const Index nc = mesh_.n_cells;
  row_ptr_.assign(zu(nc) + 1, 0);
  col_.clear();
  diag_block_.assign(zu(nc), 0);
  other_block_.assign(csr_.faces.size(), -1);
  std::vector<Index> cols;
  for (Index c = 0; c < nc; ++c) {
    cols.clear();
    cols.push_back(c);
    for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
      const Index o = csr_.other[zu(k)];
      if (o != kInvalidIndex) cols.push_back(o);
    }
    std::sort(cols.begin(), cols.end());
    cols.erase(std::unique(cols.begin(), cols.end()), cols.end());
    const auto base = static_cast<std::ptrdiff_t>(col_.size());
    for (Index j : cols) col_.push_back(j);
    row_ptr_[zu(c) + 1] = static_cast<std::ptrdiff_t>(col_.size());
    auto find = [&](Index j) {
      const auto it = std::lower_bound(cols.begin(), cols.end(), j);
      return base + static_cast<std::ptrdiff_t>(it - cols.begin());
    };
    diag_block_[zu(c)] = find(c);
    for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
      const Index o = csr_.other[zu(k)];
      if (o != kInvalidIndex) other_block_[zu(k)] = find(o);
    }
  }
}

// ---------------------------------------------------------------------------
// 状態の出し入れ
// ---------------------------------------------------------------------------

void PbcSolver::load_state(const CellMacroState& s) {
  GSIS_CHECK(static_cast<Index>(s.size()) == mesh_.n_cells && s.p.size() == s.rho.size() &&
                 s.T.size() == s.rho.size() && s.ux.size() == s.rho.size(),
             std::format("マクロ量のセル数 {} がメッシュのセル数 {} と違います", s.size(), mesh_.n_cells));
  for (Index c = 0; c < mesh_.n_cells; ++c) {
    const auto i = zu(c);
    GSIS_CHECK(s.p[i] > 0.0 && s.T[i] > 0.0 && std::isfinite(s.p[i]) && std::isfinite(s.T[i]),
               std::format("セル {} の圧力・温度が正の有限値ではありません(p = {}, T = {})", c, s.p[i], s.T[i]));
    W_[0][i] = s.p[i];
    W_[1][i] = s.ux[i];
    W_[2][i] = s.uy[i];
    W_[3][i] = s.uz[i];
    W_[4][i] = s.T[i];
  }
}

void PbcSolver::store_state(CellMacroState& s) const {
  s.resize(zu(mesh_.n_cells));
  for (Index c = 0; c < mesh_.n_cells; ++c) {
    const auto i = zu(c);
    s.p[i] = W_[0][i];
    s.ux[i] = W_[1][i];
    s.uy[i] = W_[2][i];
    s.uz[i] = W_[3][i];
    s.T[i] = W_[4][i];
    s.rho[i] = W_[0][i] / W_[4][i];
  }
}

bool PbcSolver::is_periodic(Index bface) const {
  return patch_bc_[zu(bface_patch_[zu(bface)])].kind == MacroBoundaryKind::PeriodicSlab;
}

bool PbcSolver::reconstructs_to(Index bface) const {
  const std::string& mode = standalone_ ? params_.standalone_boundary_reconstruction : params_.boundary_reconstruction;
  if (mode == "limited") return true;
  if (mode == "cell") return false;
  const MacroBoundaryKind k = patch_bc_[zu(bface_patch_[zu(bface)])].kind;
  return k == MacroBoundaryKind::DiffuseWall || k == MacroBoundaryKind::Symmetry;
}

bool PbcSolver::uses_face_moments(Index bface) const {
  if (params_.standalone_face_state == "moments") return true;
  if (params_.standalone_face_state == "cell") return false;
  // "wall_moments": 壁と対称面だけ境界面の分布のモーメント、開いた境界はセルの値
  const MacroBoundaryKind k = patch_bc_[zu(bface_patch_[zu(bface)])].kind;
  return k == MacroBoundaryKind::DiffuseWall || k == MacroBoundaryKind::Symmetry;
}

BoundaryFaceExtra PbcSolver::extra_of(Index bface) const {
  BoundaryFaceExtra e;
  const int p = bface_patch_[zu(bface)];
  const std::vector<Vec5>& ff = far_field_faces_[static_cast<std::size_t>(p)];
  if (!ff.empty()) {
    const Index f = bface + mesh_.n_internal_faces();
    e.far_field_state = &ff[zu(f - mesh_.patches[static_cast<std::size_t>(p)].start)];
  }
  return e;
}

void PbcSolver::set_face_state_from_kinetic(const BoundaryKineticFluxes& boundary) {
  const Index nbf = mesh_.n_boundary_faces();
  GSIS_CHECK(static_cast<Index>(boundary.face_state.size()) == nbf &&
                 static_cast<Index>(boundary.F_plus.size()) == nbf &&
                 static_cast<Index>(boundary.F_minus.size()) == nbf,
             std::format("境界面の運動論フラックスの数が境界面の数 {} と違います", nbf));
  const Index nif = mesh_.n_internal_faces();
  const std::string& mode = params_.gsis_face_state;
  for (Index b = 0; b < nbf; ++b) {
    const std::array<double, 5>& fs = boundary.face_state[zu(b)];
    const Index c = mesh_.owner[zu(b + nif)];
    const double rho = fs[0];
    const double T = fs[4];
    // "walls" は壁と対称面だけ運動論の値、"cell" はすべてセルの値(勾配 0 の外挿)
    const MacroBoundaryKind kind = patch_bc_[zu(bface_patch_[zu(b)])].kind;
    const bool use_kinetic = mode == "kinetic" || (mode == "walls" && (kind == MacroBoundaryKind::DiffuseWall ||
                                                                       kind == MacroBoundaryKind::Symmetry));
    if (use_kinetic && rho > 0.0 && T > 0.0 && std::isfinite(rho) && std::isfinite(T)) {
      face_state_[zu(b)] = make_state(rho * T, Vec3{fs[1], fs[2], fs[3]}, T);
    } else {
      // 退化した値(密度 0 など)なら勾配 0 の外挿にする
      face_state_[zu(b)] = Vec5{{W_[0][zu(c)], W_[1][zu(c)], W_[2][zu(c)], W_[3][zu(c)], W_[4][zu(c)]}};
    }
  }
}

void PbcSolver::set_face_state_from_cells() {
  const Index nif = mesh_.n_internal_faces();
  const Index nbf = mesh_.n_boundary_faces();
  const bool moments = params_.standalone_face_state != "cell";
#pragma omp parallel for schedule(static)
  for (Index b = 0; b < nbf; ++b) {
    const Index f = b + nif;
    const auto c = zu(mesh_.owner[zu(f)]);
    const Vec5 Wi{{W_[0][c], W_[1][c], W_[2][c], W_[3][c], W_[4][c]}};
    if (!moments || is_periodic(b) || !uses_face_moments(b)) {
      face_state_[zu(b)] = Wi;
    } else {
      const auto p = zu(bface_patch_[zu(b)]);
      face_state_[zu(b)] = boundary_face_state(patch_bc_[p], extra_of(b), Wi, geo_.face_normal[zu(f)]);
    }
  }
}

// ---------------------------------------------------------------------------
// 勾配・リミッタ・再構成
// ---------------------------------------------------------------------------

void PbcSolver::compute_gradients() {
  const Index nc = mesh_.n_cells;
  const Index nif = mesh_.n_internal_faces();
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    double phic[kNumVars];
    for (int v = 0; v < kNumVars; ++v) phic[v] = W_[v][ci];
    Vec3 g[kNumVars] = {};
    for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
      const auto ki = zu(k);
      const Index o = csr_.other[ki];
      const Vec3& coef = lsq_.coeff[ki];
      if (o != kInvalidIndex) {
        for (int v = 0; v < kNumVars; ++v) g[v] += (W_[v][zu(o)] - phic[v]) * coef;
      } else {
        const Index b = csr_.faces[ki] - nif;
        if (is_periodic(b)) continue;  // 並進周期: 勾配 0(境界面の値 = セルの値)
        const Vec5& fs = face_state_[zu(b)];
        for (int v = 0; v < kNumVars; ++v) g[v] += (fs[v] - phic[v]) * coef;
      }
    }
    for (int v = 0; v < kNumVars; ++v) grad_[v][ci] = g[v];
  }
}

void PbcSolver::compute_limiters() {
  const Index nc = mesh_.n_cells;
  const Index nif = mesh_.n_internal_faces();
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    const Vec3& xc = geo_.cell_center[ci];
    for (int v = 0; v < kNumVars; ++v) {
      const double phic = W_[v][ci];
      double mx = phic;
      double mn = phic;
      for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
        const auto ki = zu(k);
        const Index o = csr_.other[ki];
        double val;
        if (o != kInvalidIndex) {
          val = W_[v][zu(o)];
        } else {
          const Index b = csr_.faces[ki] - nif;
          val = is_periodic(b) ? phic : face_state_[zu(b)][v];
        }
        mx = std::max(mx, val);
        mn = std::min(mn, val);
      }
      VenkatakrishnanAccumulator<double> acc(mx - phic, mn - phic, eps2_[ci]);
      for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
        const Index f = csr_.faces[zu(k)];
        acc.add(dot(grad_[v][ci], geo_.face_center[zu(f)] - xc));
      }
      lim_[v][ci] = acc.value();
    }
  }
}

Vec5 PbcSolver::reconstruct_to(Index cell, const Vec3& x) const {
  const auto ci = zu(cell);
  const Vec3 dx = x - geo_.cell_center[ci];
  Vec5 W;
  for (int v = 0; v < kNumVars; ++v) {
    W[v] = reconstruct_limited(W_[v][ci], grad_[v][ci], lim_[v][ci], dx);
  }
  if (!(W[0] > 0.0) || !(W[4] > 0.0)) {
    // 正値でなくなったら 1 次(セルの値)にする
    for (int v = 0; v < kNumVars; ++v) W[v] = W_[v][ci];
  }
  return W;
}

// ---------------------------------------------------------------------------
// 残差の評価(内部面・境界面のフラックス、ヤコビアンの部品、擬似時間の Λ)
// ---------------------------------------------------------------------------

void PbcSolver::evaluate() {
  const Index nc = mesh_.n_cells;
  const Index nif = mesh_.n_internal_faces();
  const Index nbf = mesh_.n_boundary_faces();
  const bool upwind2 = params_.advection == "upwind2";
  const bool jac_reconstruction = params_.boundary_jacobian == "reconstruction";


  // 単独モード: モーメントを使わない境界面(standalone_face_state = "cell"、"wall_moments" の開いた境界)の
  // 勾配用の値は、いまのセルの値(勾配 0 の外挿)にする。モーメントを使う面は前回の評価の値(ラグ付き)
  if (standalone_) {
#pragma omp parallel for schedule(static)
    for (Index b = 0; b < nbf; ++b) {
      if (uses_face_moments(b) && !is_periodic(b)) continue;
      const auto ci = zu(mesh_.owner[zu(b + nif)]);
      face_state_[zu(b)] = Vec5{{W_[0][ci], W_[1][ci], W_[2][ci], W_[3][ci], W_[4][ci]}};
    }
  }

  compute_gradients();
  compute_limiters();

#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto i = zu(c);
    const double T = W_[4][i];
    rho_[i] = W_[0][i] / T;
    mu_[i] = gas_.mu(T);
    kappa_[i] = gas_.kappa(T);
    cs_[i] = std::sqrt(kGamma * T);
  }

  // 境界面: 再構成、ヤコビアン(W_i で評価)、‖B_u‖₂、Λ の寄与
#pragma omp parallel for schedule(static)
  for (Index b = 0; b < nbf; ++b) {
    const auto bi = zu(b);
    const Index f = b + nif;
    const auto fi = zu(f);
    const Index c = mesh_.owner[fi];
    const auto ci = zu(c);
    if (is_periodic(b)) {
      bW_[bi] = Vec5{{W_[0][ci], W_[1][ci], W_[2][ci], W_[3][ci], W_[4][ci]}};
      bJ_[bi] = mat5_zero();
      bBnorm_[bi] = 0.0;
      bLam_[bi] = 0.0;
      continue;
    }
    const MacroBoundaryCondition& bc = patch_bc_[zu(bface_patch_[bi])];
    const Vec3& n = geo_.face_normal[fi];
    const double A = geo_.face_area[fi];
    const Vec5 Wi{{W_[0][ci], W_[1][ci], W_[2][ci], W_[3][ci], W_[4][ci]}};
    bW_[bi] = reconstructs_to(b) ? reconstruct_to(c, geo_.face_center[fi]) : Wi;
    Mat5 J = boundary_flux_jacobian(bc, extra_of(b), Wi, n, A);
    if (jac_reconstruction && reconstructs_to(b)) {
      // 再構成のセル自身への依存: W_b,v = W_i,v + φ_v Σ_k c_k (W_k,v − W_i,v)·dx
      //   → ∂W_b,v/∂W_i,v = 1 − φ_v (Σ_k c_k)·dx(勾配 0 の外挿の境界面はセルと一緒に動くので和から除く)
      const Vec3 dx = geo_.face_center[fi] - geo_.cell_center[ci];
      Vec3 csum{};
      for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
        const auto ki = zu(k);
        const Index o = csr_.other[ki];
        if (o == kInvalidIndex) {
          const Index bb = csr_.faces[ki] - nif;
          if (is_periodic(bb) || (standalone_ && !uses_face_moments(bb))) continue;
        }
        csum += lsq_.coeff[ki];
      }
      const double s = dot(csum, dx);
      for (int v = 0; v < kNumVars; ++v) {
        const double gfac = std::clamp(1.0 - lim_[v][ci] * s, 0.5, 2.0);
        for (int r = 0; r < 5; ++r) J(r, v) *= gfac;
      }
    }
    bJ_[bi] = J;
    const double B[9] = {J(1, 1), J(1, 2), J(1, 3), J(2, 1), J(2, 2), J(2, 3), J(3, 1), J(3, 2), J(3, 3)};
    bBnorm_[bi] = mat3_norm2(B);
    const Vec3 u = velocity_of(Wi);
    const double U = norm(u);
    const double chi = std::min(1.0, std::max(0.01, U / cs_[ci]));
    bLam_[bi] = A * (std::abs(dot(u, n)) + chi * cs_[ci] + 8.0 * mu_[ci] / (3.0 * rho_[ci] * fv_.dn[fi]));
  }

  // a_u(式 (26))と D^RC = V/a_u
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    double a = 0.0;
    for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
      const auto ki = zu(k);
      const Index f = csr_.faces[ki];
      const auto fi = zu(f);
      if (f < nif) {
        const Index o = csr_.other[ki];
        double m;
        if (has_mdot_hat_) {
          m = csr_.sign[ki] * mdot_hat_[fi];
        } else {
          // 初期値 (ρ_i + ρ_j)[(u_i + u_j)·n] A / 4(n はセル c から外向き)
          const auto oi = zu(o);
          const Vec3 usum{W_[1][ci] + W_[1][oi], W_[2][ci] + W_[2][oi], W_[3][ci] + W_[3][oi]};
          m = (rho_[ci] + rho_[oi]) * dot(usum, geo_.face_normal[fi]) * geo_.face_area[fi] / 4.0 *
              csr_.sign[ki];
        }
        const double mu_f = 0.5 * (mu_[ci] + mu_[zu(o)]);
        a += std::max(m, 0.0) + 4.0 * mu_f * geo_.face_area[fi] / (3.0 * fv_.dn[fi]);
      } else {
        a += bBnorm_[zu(f - nif)];
      }
    }
    a_u_[ci] = a;
    D_[ci] = a > 0.0 ? geo_.cell_volume[ci] / a : 0.0;
  }

  // 内部面
#pragma omp parallel for schedule(static)
  for (Index f = 0; f < nif; ++f) {
    const auto fi = zu(f);
    const auto o = zu(mesh_.owner[fi]);
    const auto nb = zu(mesh_.neighbour[fi]);
    InternalFaceGeometry g;
    g.S = geo_.face_area_vector[fi];
    g.n = geo_.face_normal[fi];
    g.A = geo_.face_area[fi];
    g.d = fv_.d[fi];
    g.dn = fv_.dn[fi];
    g.wo = fv_.w_owner[fi];
    g.wn = fv_.w_neighbour[fi];
    FaceCellState Wo{W_[0][o], Vec3{W_[1][o], W_[2][o], W_[3][o]}, W_[4][o]};
    FaceCellState Wn{W_[0][nb], Vec3{W_[1][nb], W_[2][nb], W_[3][nb]}, W_[4][nb]};
    FaceAuxiliary aux;
    aux.beta = g.A / g.dn * (g.wo * D_[o] + g.wn * D_[nb]);
    aux.gradp_bar = g.wo * grad_[0][o] + g.wn * grad_[0][nb];
    for (int a = 0; a < 3; ++a) {
      aux.gradu_bar[static_cast<std::size_t>(a)] = g.wo * grad_[1 + a][o] + g.wn * grad_[1 + a][nb];
    }
    aux.gradT_bar = g.wo * grad_[4][o] + g.wn * grad_[4][nb];
    aux.mu = 0.5 * (mu_[o] + mu_[nb]);
    aux.kappa = 0.5 * (kappa_[o] + kappa_[nb]);
    if (face_hot_) {
      aux.hot_sigma = face_hot_sigma_[fi];
      aux.hot_q = face_hot_q_[fi];
    } else if (has_hot_) {
      for (std::size_t k = 0; k < 6; ++k) aux.hot_sigma[k] = g.wo * hot_sigma_[o][k] + g.wn * hot_sigma_[nb][k];
      aux.hot_q = g.wo * hot_q_[o] + g.wn * hot_q_[nb];
    }
    InternalFaceResult r;
    if (upwind2) {
      const Vec5 ro = reconstruct_to(static_cast<Index>(o), geo_.face_center[fi]);
      const Vec5 rn = reconstruct_to(static_cast<Index>(nb), geo_.face_center[fi]);
      const FaceCellState Ro{ro[0], velocity_of(ro), ro[4]};
      const FaceCellState Rn{rn[0], velocity_of(rn), rn[4]};
      r = internal_face_flux(g, Wo, Wn, aux, &Ro, &Rn);
    } else {
      r = internal_face_flux(g, Wo, Wn, aux);
    }
    face_res_[fi] = r;
    face_jac_[fi] = face_pu_jacobian_data(g, Wo, Wn, aux, r);
    face_ckappa_[fi] = aux.kappa * g.A / g.dn;
    // Λ の寄与(式 (56))
    const Vec3 uf = g.wo * Wo.u + g.wn * Wn.u;
    const double cf = 0.5 * (cs_[o] + cs_[nb]);
    const double U = std::sqrt(0.5 * (dot(Wo.u, Wo.u) + dot(Wn.u, Wn.u)));
    const double chi = std::min(1.0, std::max(0.01, U / cf));
    const double base = std::abs(dot(uf, g.n)) + chi * cf;
    face_lam_o_[fi] = g.A * (base + 8.0 * aux.mu / (3.0 * rho_[o] * g.dn));
    face_lam_n_[fi] = g.A * (base + 8.0 * aux.mu / (3.0 * rho_[nb] * g.dn));
  }

  // 境界面のフラックス(式 (36)–(38))
  const bool update_face_state = standalone_ && params_.standalone_face_state != "cell";  // モーメントを使う面がある
#pragma omp parallel for schedule(static)
  for (Index b = 0; b < nbf; ++b) {
    const auto bi = zu(b);
    if (is_periodic(b)) {
      bF_[bi] = vec5_zero();
      next_face_state_[bi] = bW_[bi];
      continue;
    }
    const auto fi = zu(b + nif);
    const MacroBoundaryCondition& bc = patch_bc_[zu(bface_patch_[bi])];
    const BoundaryFaceExtra extra = extra_of(b);
    const BoundaryFaceFlux bf = boundary_flux(bc, extra, bW_[bi], geo_.face_normal[fi], geo_.face_area[fi],
                                              C_plus_[bi], C_minus_[bi]);
    bF_[bi] = bf.total();
    if (update_face_state && uses_face_moments(b)) {
      next_face_state_[bi] = boundary_face_state(bc, extra, bW_[bi], geo_.face_normal[fi]);
    }
  }

  // セルへ集める(CSR の順。atomic なし)
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    std::array<double, 5> R{};
    double defect = 0.0;
    double lam = 0.0;
    for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
      const auto ki = zu(k);
      const Index f = csr_.faces[ki];
      const auto fi = zu(f);
      if (f < nif) {
        const InternalFaceResult& r = face_res_[fi];
        if (csr_.sign[ki] > 0) {
          for (int q = 0; q < 5; ++q) R[static_cast<std::size_t>(q)] += r.F[q];
          defect += r.defect_owner;
          lam += face_lam_o_[fi];
        } else {
          for (int q = 0; q < 5; ++q) R[static_cast<std::size_t>(q)] -= r.F[q];
          defect += r.defect_neighbour;
          lam += face_lam_n_[fi];
        }
      } else {
        const auto bi = zu(f - nif);
        for (int q = 0; q < 5; ++q) R[static_cast<std::size_t>(q)] += bF_[bi][q];
        lam += bLam_[bi];
      }
    }
    if (source_ != nullptr) {
      for (std::size_t q = 0; q < 5; ++q) R[q] -= (*source_)[ci][q];
    }
    R_[ci] = R;
    defect_[ci] = defect;
    lambda_[ci] = lam;
  }

  // ラグ付きの ṁ̂ を更新する(RC の補正と選んだ再構成を含む ṁ)
  if (!freeze_lag_) {
#pragma omp parallel for schedule(static)
    for (Index f = 0; f < nif; ++f) mdot_hat_[zu(f)] = face_res_[zu(f)].mdot;
    has_mdot_hat_ = true;
  }

  // 出力用の境界面の量
  boundary_out_.flux = bF_;
  boundary_out_.face_state = face_state_;
  boundary_out_.reconstructed = bW_;

  // 単独モード: 次の評価の勾配に使う境界面の値(ラグ付き)
  if (update_face_state && !freeze_lag_) {
#pragma omp parallel for schedule(static)
    for (Index b = 0; b < nbf; ++b) {
      if (uses_face_moments(b) && !is_periodic(b)) face_state_[zu(b)] = next_face_state_[zu(b)];
    }
  }
}

// ---------------------------------------------------------------------------
// 線形系の組み立て
// ---------------------------------------------------------------------------

void PbcSolver::assemble_pu(BlockCsrMatrix& A, std::vector<double>& rhs, bool continuity_pseudo_time) const {
  const Index nc = mesh_.n_cells;
  const Index nif = mesh_.n_internal_faces();
  A.block_size = 4;
  A.ptr = row_ptr_;
  A.col = col_;
  A.val.assign(col_.size() * 16, 0.0);
  rhs.assign(zu(nc) * 4, 0.0);
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    double* Dg = &A.val[zu(diag_block_[ci]) * 16];
    // 擬似時間 M_pu = V/Δt diag(0, ρ, ρ, ρ)(式 (57))
    const double m = geo_.cell_volume[ci] / dt_[ci] * rho_[ci];
    Dg[5] += m;
    Dg[10] += m;
    Dg[15] += m;
    if (continuity_pseudo_time) Dg[0] += geo_.cell_volume[ci] / (dt_[ci] * W_[4][ci]);
    for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
      const auto ki = zu(k);
      const Index f = csr_.faces[ki];
      const auto fi = zu(f);
      if (f < nif) {
        InternalFaceGeometry g;
        g.S = geo_.face_area_vector[fi];
        g.n = geo_.face_normal[fi];
        g.A = geo_.face_area[fi];
        g.d = fv_.d[fi];
        g.dn = fv_.dn[fi];
        g.wo = fv_.w_owner[fi];
        g.wn = fv_.w_neighbour[fi];
        Block4 Joo;
        Block4 Jon;
        face_pu_blocks(g, face_jac_[fi], Joo, Jon);
        double* Og = &A.val[zu(other_block_[ki]) * 16];
        if (csr_.sign[ki] > 0) {
          for (std::size_t q = 0; q < 16; ++q) {
            Dg[q] += Joo[q];
            Og[q] += Jon[q];
          }
        } else {
          for (std::size_t q = 0; q < 16; ++q) {
            Og[q] -= Joo[q];
            Dg[q] -= Jon[q];
          }
        }
      } else {
        const Index b = f - nif;
        if (is_periodic(b)) continue;
        const Mat5& J = bJ_[zu(b)];
        for (int r = 0; r < 4; ++r) {
          for (int s = 0; s < 4; ++s) Dg[4 * r + s] += J(r, s);
        }
      }
    }
    for (std::size_t r = 0; r < 4; ++r) rhs[4 * ci + r] = -R_[ci][r];
  }
  if (closed_domain_) {
    // 閉じた領域: 1 セルの連続式を Δp = 0 に置き換える(全質量は更新後に合わせる)
    const auto pc = zu(pin_cell_);
    for (std::ptrdiff_t blk = row_ptr_[pc]; blk < row_ptr_[pc + 1]; ++blk) {
      for (std::size_t s = 0; s < 4; ++s) A.val[zu(blk) * 16 + s] = 0.0;
    }
    A.val[zu(diag_block_[pc]) * 16] = 1.0;
    rhs[4 * pc] = 0.0;
  }
  if (n_frozen_ > 0) {
    // 固定したセル: 行を恒等(Δp = Δu = 0)にする
#pragma omp parallel for schedule(static)
    for (Index c = 0; c < nc; ++c) {
      const auto ci = zu(c);
      if (!frozen_[ci]) continue;
      for (std::ptrdiff_t blk = row_ptr_[ci]; blk < row_ptr_[ci + 1]; ++blk) {
        for (std::size_t s = 0; s < 16; ++s) A.val[zu(blk) * 16 + s] = 0.0;
      }
      double* Dg = &A.val[zu(diag_block_[ci]) * 16];
      Dg[0] = Dg[5] = Dg[10] = Dg[15] = 1.0;
      for (std::size_t r = 0; r < 4; ++r) rhs[4 * ci + r] = 0.0;
    }
  }
}

void PbcSolver::assemble_T(BlockCsrMatrix& A, std::vector<double>& rhs) const {
  const Index nc = mesh_.n_cells;
  const Index nif = mesh_.n_internal_faces();
  A.block_size = 1;
  A.ptr = row_ptr_;
  A.col = col_;
  A.val.assign(col_.size(), 0.0);
  rhs.assign(zu(nc), 0.0);
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    const double T = W_[4][ci];
    const Vec3 u{W_[1][ci], W_[2][ci], W_[3][ci]};
    // 擬似時間 M_T = 3/2 ρ R V/Δt(式 (57))
    double diag = 1.5 * rho_[ci] * geo_.cell_volume[ci] / dt_[ci];
    for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
      const auto ki = zu(k);
      const Index f = csr_.faces[ki];
      const auto fi = zu(f);
      if (f < nif) {
        const double m_i = csr_.sign[ki] * face_jac_[fi].mdot1;
        const std::array<double, 2> cf = temperature_face_coefficients(m_i, face_ckappa_[fi]);
        diag += cf[0];
        A.val[zu(other_block_[ki])] += cf[1];
      } else {
        const Index b = f - nif;
        if (is_periodic(b)) continue;
        diag += temperature_boundary_coefficient(bJ_[zu(b)], bF_[zu(b)], u, T);  // 式 (55)
      }
    }
    A.val[zu(diag_block_[ci])] += diag;
    // R_T = R_E − u·R_mom + (K − 3/2 T) R_ρ − Σ 𝒦(式 (51))
    const std::array<double, 5>& R = R_[ci];
    const double RT = temperature_flux_from_conservative(Vec5{{R[0], R[1], R[2], R[3], R[4]}}, u, T) - defect_[ci];
    rhs[ci] = -RT;
    if (n_frozen_ > 0 && frozen_[ci]) {
      // 固定したセル: ΔT = 0
      for (std::ptrdiff_t blk = row_ptr_[ci]; blk < row_ptr_[ci + 1]; ++blk) A.val[zu(blk)] = 0.0;
      A.val[zu(diag_block_[ci])] = 1.0;
      rhs[ci] = 0.0;
    }
  }
}

// ---------------------------------------------------------------------------
// 質量の拘束
// ---------------------------------------------------------------------------

double PbcSolver::total_mass() const {
  std::vector<double> m(zu(mesh_.n_cells));
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < mesh_.n_cells; ++c) {
    m[zu(c)] = W_[0][zu(c)] / W_[4][zu(c)] * geo_.cell_volume[zu(c)];
  }
  return deterministic_sum(m);
}

void PbcSolver::apply_mass_constraint(double target_mass) {
  const double M = total_mass();
  if (!(M > 0.0) || !(target_mass > 0.0)) return;
  if (n_frozen_ > 0) {
    // 固定したセルは動かさず、残りのセルの p だけを掛け算で合わせる
    std::vector<double> mf(zu(mesh_.n_cells), 0.0);
#pragma omp parallel for schedule(static)
    for (Index c = 0; c < mesh_.n_cells; ++c) {
      if (frozen_[zu(c)]) mf[zu(c)] = W_[0][zu(c)] / W_[4][zu(c)] * geo_.cell_volume[zu(c)];
    }
    const double Mf = deterministic_sum(mf);
    if (!(M - Mf > 0.0) || !(target_mass - Mf > 0.0)) return;
    const double s = (target_mass - Mf) / (M - Mf);
#pragma omp parallel for schedule(static)
    for (Index c = 0; c < mesh_.n_cells; ++c) {
      if (!frozen_[zu(c)]) W_[0][zu(c)] *= s;
    }
    return;
  }
  const double s = target_mass / M;
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < mesh_.n_cells; ++c) W_[0][zu(c)] *= s;
}

// ---------------------------------------------------------------------------
// GSIS の補正の低域フィルタ
// ---------------------------------------------------------------------------

CorrectionFilterReport PbcSolver::filter_correction(const CellMacroState& half, CellMacroState& w,
                                                    double length_factor, double max_length, double min_cells) const {
  CorrectionFilterReport rep;
  if (!(length_factor > 0.0) && !(min_cells > 0.0)) return rep;
  Timer timer;
  const Index nc = mesh_.n_cells;
  const Index nif = mesh_.n_internal_faces();
  GSIS_CHECK(half.size() == zu(nc) && w.size() == zu(nc), "補正のフィルタの状態のセル数がメッシュと違います");
  // ℓ_i²(W_half の局所平均自由行程から)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    GSIS_CHECK(half.rho[ci] > 0.0 && half.T[ci] > 0.0 && w.rho[ci] > 0.0 && w.T[ci] > 0.0,
               std::format("補正のフィルタ: セル {} の密度か温度が正ではありません", c));
  }
  std::vector<double> D(zu(nc));
  std::vector<double> ell(zu(nc));
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    const double T = half.T[ci];
    const double p = half.rho[ci] * T;
    double l = length_factor > 0.0 ? length_factor * gas_.mu(T) / p * std::sqrt(kPi * T / 2.0) : 0.0;
    // 格子規模の下限(min_cells × h、h = V^{1/3})
    if (min_cells > 0.0) l = std::max(l, min_cells * std::cbrt(geo_.cell_volume[ci]));
    if (max_length > 0.0) l = std::min(l, max_length);
    ell[ci] = l;
    D[ci] = l * l;
  }
  rep.max_length = *std::max_element(ell.begin(), ell.end());
  // (V − ∇·(ℓ²∇))。境界面(周期を含む)は流束 0。
  // 直前の段で固定したセル(gsis_freeze_kn)は δ̃ = 0 とする(行を恒等にし、隣のセルから見ると δ̃ = 0 の Dirichlet 条件)。
  // 固定したセルへ補正が広がらず、固定した領域に近いセルの補正は滑らかに 0 へ近づく
  const bool use_frozen = n_frozen_ > 0 && frozen_.size() == zu(nc);
  macro::BlockCsrMatrix A;
  A.block_size = 1;
  A.ptr = row_ptr_;
  A.col = col_;
  A.val.assign(col_.size(), 0.0);
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    if (use_frozen && frozen_[ci]) {
      A.val[zu(diag_block_[ci])] = 1.0;
      continue;
    }
    double diag = geo_.cell_volume[ci];
    for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
      const auto ki = zu(k);
      const Index f = csr_.faces[ki];
      if (f >= nif) continue;
      const Index nb = csr_.other[ki];
      const double Da = D[ci];
      const double Db = D[zu(nb)];
      const double Df = Da + Db > 0.0 ? 2.0 * Da * Db / (Da + Db) : 0.0;
      const double a = Df * geo_.face_area[zu(f)] / fv_.dn[zu(f)];
      diag += a;
      A.val[zu(other_block_[ki])] -= a;
    }
    A.val[zu(diag_block_[ci])] += diag;
  }
  macro::LinearSolverOptions lo;
  lo.solver = "gmres";
  lo.preconditioner = "amg";
  lo.rtol = 1e-8;
  lo.max_iterations = 1000;
  std::vector<double> rhs(zu(nc));
  std::vector<double> x(zu(nc));
  std::vector<double> delta[5];
  for (int v = 0; v < 5; ++v) {
#pragma omp parallel for schedule(static)
    for (Index c = 0; c < nc; ++c) {
      const auto ci = zu(c);
      double d = 0.0;
      switch (v) {
        case 0: d = std::log(w.rho[ci] / half.rho[ci]); break;
        case 1: d = w.ux[ci] - half.ux[ci]; break;
        case 2: d = w.uy[ci] - half.uy[ci]; break;
        case 3: d = w.uz[ci] - half.uz[ci]; break;
        default: d = std::log(w.T[ci] / half.T[ci]); break;
      }
      rhs[ci] = use_frozen && frozen_[ci] ? 0.0 : geo_.cell_volume[ci] * d;
    }
    std::fill(x.begin(), x.end(), 0.0);
    const macro::LinearSolveResult lr = macro::solve_linear_system(A, rhs, x, lo);
    rep.linear_iterations += lr.iterations;
    if (!lr.converged) rep.converged = false;
    GSIS_CHECK(std::isfinite(lr.relative_residual),
               std::format("補正のフィルタの線形ソルバーが止まりました({})", lr.breakdown));
    delta[v] = x;
  }
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    w.rho[ci] = half.rho[ci] * std::exp(delta[0][ci]);
    w.ux[ci] = half.ux[ci] + delta[1][ci];
    w.uy[ci] = half.uy[ci] + delta[2][ci];
    w.uz[ci] = half.uz[ci] + delta[3][ci];
    w.T[ci] = half.T[ci] * std::exp(delta[4][ci]);
    w.p[ci] = w.rho[ci] * w.T[ci];
  }
  rep.applied = true;
  rep.seconds = timer.seconds();
  return rep;
}

// ---------------------------------------------------------------------------
// HoT と PBC 段
// ---------------------------------------------------------------------------

HigherOrderTerms PbcSolver::compute_hot(const KineticMoments& moments, const BoundaryKineticFluxes& boundary) {
  const Index nc = mesh_.n_cells;
  GSIS_CHECK(static_cast<Index>(moments.sigma.size()) == nc && static_cast<Index>(moments.q.size()) == nc,
             "運動論のモーメント σ・q のセル数がメッシュと違います");
  load_state(moments.state);
  set_face_state_from_kinetic(boundary);
  compute_gradients();
  HigherOrderTerms hot;
  hot.sigma.resize(zu(nc));
  hot.q.resize(zu(nc));
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    const double T = W_[4][ci];
    const double mu = gas_.mu(T);
    const double kappa = gas_.kappa(T);
    const std::array<double, 6> s = nsf_stress(mu, grad_[1][ci], grad_[2][ci], grad_[3][ci]);
    const Vec3 qn = -kappa * grad_[4][ci];
    for (std::size_t k = 0; k < 6; ++k) hot.sigma[ci][k] = moments.sigma[ci][k] - s[k];
    hot.q[ci] = {moments.q[ci][0] - qn.x, moments.q[ci][1] - qn.y, moments.q[ci][2] - qn.z};
  }
  return hot;
}

void PbcSolver::build_face_hot() {
  // HoT_f = Σ_s w_s (HoT_s + σ^NSF_s(W⁰)) − σ^NSF_f(W⁰) = (運動論の σ の補間) − σ^NSF_f(W⁰)(q も同様)。
  // σ^NSF_s は compute_hot と同じセル中心の式、σ^NSF_f は evaluate の面の式(式 (29) の面勾配、μ_f = 平均)。
  // grad_ と W_ は W⁰(段の初め)の値であること
  const Index nif = mesh_.n_internal_faces();
  face_hot_sigma_.resize(zu(nif));
  face_hot_q_.resize(zu(nif));
#pragma omp parallel for schedule(static)
  for (Index f = 0; f < nif; ++f) {
    const auto fi = zu(f);
    const auto o = zu(mesh_.owner[fi]);
    const auto nb = zu(mesh_.neighbour[fi]);
    InternalFaceGeometry g;
    g.S = geo_.face_area_vector[fi];
    g.n = geo_.face_normal[fi];
    g.A = geo_.face_area[fi];
    g.d = fv_.d[fi];
    g.dn = fv_.dn[fi];
    g.wo = fv_.w_owner[fi];
    g.wn = fv_.w_neighbour[fi];
    const double To = W_[4][o];
    const double Tn = W_[4][nb];
    const double mu_o = gas_.mu(To);
    const double mu_n = gas_.mu(Tn);
    const double ka_o = gas_.kappa(To);
    const double ka_n = gas_.kappa(Tn);
    const std::array<double, 6> so = nsf_stress(mu_o, grad_[1][o], grad_[2][o], grad_[3][o]);
    const std::array<double, 6> sn = nsf_stress(mu_n, grad_[1][nb], grad_[2][nb], grad_[3][nb]);
    const Vec3 qo = -ka_o * grad_[4][o];
    const Vec3 qn = -ka_n * grad_[4][nb];
    FaceAuxiliary aux;
    for (int a = 0; a < 3; ++a) {
      aux.gradu_bar[static_cast<std::size_t>(a)] = g.wo * grad_[1 + a][o] + g.wn * grad_[1 + a][nb];
    }
    aux.gradT_bar = g.wo * grad_[4][o] + g.wn * grad_[4][nb];
    aux.mu = 0.5 * (mu_o + mu_n);
    aux.kappa = 0.5 * (ka_o + ka_n);
    const Vec3 uo{W_[1][o], W_[2][o], W_[3][o]};
    const Vec3 un{W_[1][nb], W_[2][nb], W_[3][nb]};
    const std::array<double, 6> sf = face_nsf_stress(g, uo, un, aux);
    const Vec3 qf = face_nsf_heat_flux(g, To, Tn, aux);
    for (std::size_t k = 0; k < 6; ++k) {
      face_hot_sigma_[fi][k] = g.wo * (hot_sigma_[o][k] + so[k]) + g.wn * (hot_sigma_[nb][k] + sn[k]) - sf[k];
    }
    face_hot_q_[fi] = g.wo * (hot_q_[o] + qo) + g.wn * (hot_q_[nb] + qn) - qf;
  }
}

PbcStageReport PbcSolver::run_stage(const PbcStageInput& input, const PbcStageOptions& options, CellMacroState& out) {
  GSIS_CHECK(input.initial != nullptr, "PBC 段の初期状態がありません");
  GSIS_CHECK(options.max_iterations >= 0 && options.cfl > 0.0, "PBC 段の反復数と CFL が正しくありません");
  const Index nc = mesh_.n_cells;
  const Index nif = mesh_.n_internal_faces();
  const Index nbf = mesh_.n_boundary_faces();
  PbcStageReport report;
  Timer stage_timer;

  load_state(*input.initial);
  // HoT
  has_hot_ = input.hot != nullptr;
  if (has_hot_) {
    GSIS_CHECK(static_cast<Index>(input.hot->sigma.size()) == nc && static_cast<Index>(input.hot->q.size()) == nc,
               "HoT のセル数がメッシュと違います");
    for (Index c = 0; c < nc; ++c) {
      hot_sigma_[zu(c)] = input.hot->sigma[zu(c)];
      const std::array<double, 3>& q = input.hot->q[zu(c)];
      hot_q_[zu(c)] = Vec3{q[0], q[1], q[2]};
    }
  }
  source_ = input.source;
  if (source_ != nullptr) {
    GSIS_CHECK(static_cast<Index>(source_->size()) == nc, "源項のセル数がメッシュと違います");
  }

  // 境界: 勾配用の境界面の値と整合補正
  standalone_ = input.boundary == nullptr;
  face_hot_ = false;
  if (standalone_) {
    set_face_state_from_cells();
    std::fill(C_plus_.begin(), C_plus_.end(), vec5_zero());
    std::fill(C_minus_.begin(), C_minus_.end(), vec5_zero());
  } else {
    set_face_state_from_kinetic(*input.boundary);
    compute_gradients();
    compute_limiters();
#pragma omp parallel for schedule(static)
    for (Index b = 0; b < nbf; ++b) {
      const auto bi = zu(b);
      if (is_periodic(b)) {
        C_plus_[bi] = vec5_zero();
        C_minus_[bi] = vec5_zero();
        continue;
      }
      const auto fi = zu(b + nif);
      const Index c = mesh_.owner[fi];
      const Vec5 W0b = reconstructs_to(b)
                           ? reconstruct_to(c, geo_.face_center[fi])
                           : Vec5{{W_[0][zu(c)], W_[1][zu(c)], W_[2][zu(c)], W_[3][zu(c)], W_[4][zu(c)]}};
      const std::array<double, 5>& fp = input.boundary->F_plus[bi];
      const std::array<double, 5>& fm = input.boundary->F_minus[bi];
      const Vec5 Fp0{{fp[0], fp[1], fp[2], fp[3], fp[4]}};
      const Vec5 Fm0{{fm[0], fm[1], fm[2], fm[3], fm[4]}};
      const ConsistencyCorrection cc =
          consistency_correction(patch_bc_[zu(bface_patch_[bi])], extra_of(b), W0b,
                                 geo_.face_normal[fi], geo_.face_area[fi], Fp0, Fm0);
      C_plus_[bi] = cc.C_plus;
      C_minus_[bi] = cc.C_minus;
    }
    // HoT の面の値を W⁰ の面の NSF と整合させる(hot_interpolation = "face")。勾配は W⁰ のもの(上で計算済み)
    if (has_hot_ && params_.hot_interpolation == "face") {
      build_face_hot();
      face_hot_ = true;
    }
    // 連続流の領域で HoT を弱める(gsis_hot_kn、フェーズ 5b)。面の値は面を挟む 2 セルの局所 Kn の平均で、
    // セルの値はそのセルの局所 Kn で w = Kn²/(Kn² + Kn_c²) 倍にする(面の HoT はセルの値から作った後に掛ける)
    if (has_hot_ && params_.gsis_hot_kn > 0.0) {
      const double kc2 = params_.gsis_hot_kn * params_.gsis_hot_kn;
      std::vector<double> kn(zu(nc));
#pragma omp parallel for schedule(static)
      for (Index c = 0; c < nc; ++c) {
        const double T = W_[4][zu(c)];
        kn[zu(c)] = gas_.mu(T) / W_[0][zu(c)] * std::sqrt(kPi * T / 2.0);
      }
      if (face_hot_) {
#pragma omp parallel for schedule(static)
        for (Index f = 0; f < nif; ++f) {
          const double k = 0.5 * (kn[zu(mesh_.owner[zu(f)])] + kn[zu(mesh_.neighbour[zu(f)])]);
          const double w = k * k / (k * k + kc2);
          for (double& v : face_hot_sigma_[zu(f)]) v *= w;
          face_hot_q_[zu(f)] = w * face_hot_q_[zu(f)];
        }
      }
#pragma omp parallel for schedule(static)
      for (Index c = 0; c < nc; ++c) {
        const double k = kn[zu(c)];
        const double w = k * k / (k * k + kc2);
        for (double& v : hot_sigma_[zu(c)]) v *= w;
        hot_q_[zu(c)] = w * hot_q_[zu(c)];
      }
    }
  }
  // 局所 Kn の大きいセルを段の間は固定する(GSIS の段だけ。gsis_freeze_kn)
  frozen_.assign(zu(nc), 0);
  n_frozen_ = 0;
  if (!standalone_ && (params_.gsis_freeze_kn > 0.0 || params_.gsis_freeze_tmin > 0.0)) {
    for (Index c = 0; c < nc; ++c) {
      const double T = W_[4][zu(c)];
      const double kn = gas_.mu(T) / W_[0][zu(c)] * std::sqrt(kPi * T / 2.0);
      const bool by_kn = params_.gsis_freeze_kn > 0.0 && kn > params_.gsis_freeze_kn;
      const bool by_t = params_.gsis_freeze_tmin > 0.0 && T < params_.gsis_freeze_tmin;  // 冷たい流れ(フェーズ 5a)
      if (by_kn || by_t) {
        frozen_[zu(c)] = 1;
        ++n_frozen_;
      }
    }
  }
  report.frozen_cells = n_frozen_;
  const double target_mass = closed_domain_ ? total_mass() : 0.0;

  BlockCsrMatrix A;
  std::vector<double> rhs;
  std::vector<double> x;
  std::vector<double> old[kNumVars];
  std::vector<double> num(zu(nc));
  std::vector<double> den(zu(nc));

  const bool ramp = options.cfl_start > 0.0 && options.cfl_start < options.cfl;
  double ser_r0 = 0.0;                                         // SER の基準の残差
  double cfl_prev = 0.0;                                       // 直前に受け入れた反復の CFL
  double cfl_cap = std::numeric_limits<double>::infinity();    // 更新を捨てた後の CFL の上限
  std::vector<double> old_mdot_hat;
  std::vector<Vec5> old_face_state;
  bool old_has_mdot_hat = false;
  for (int m = 0; m < options.max_iterations; ++m) {
    Timer it_timer;
    PbcIterationInfo info;
    info.iteration = m;
    for (int v = 0; v < kNumVars; ++v) old[v] = W_[v];
    old_mdot_hat = mdot_hat_;
    old_has_mdot_hat = has_mdot_hat_;
    old_face_state = face_state_;
    // 更新を捨てて W^m に戻す(ラグ付きの ṁ̂・境界面の値も戻す)
    auto reject = [&]() {
      for (int v = 0; v < kNumVars; ++v) W_[v] = old[v];
      mdot_hat_ = old_mdot_hat;
      has_mdot_hat_ = old_has_mdot_hat;
      face_state_ = old_face_state;
      cfl_cap = 0.5 * info.cfl;
      info.rejected = true;
      ++report.rejected_iterations;
      report.iterations = m + 1;
      info.seconds = it_timer.seconds();
      if (options.on_iteration) options.on_iteration(info);
    };

    // --- 圧力–速度(T 固定) ---
    {
      ScopedTimer st(report.timing, "評価");
      evaluate();
    }
    residual_norms(info.residual_mass, info.residual_momentum, info.residual_energy, info.kinetic_energy_defect);
    GSIS_CHECK(std::isfinite(info.residual_mass) && std::isfinite(info.residual_momentum) &&
                   std::isfinite(info.residual_energy),
               std::format("PBC の反復 {} で残差が有限でなくなりました(発散)", m));
    info.patch_mass_flow = patch_mass_flow();
    if (m == 0) {
      report.initial_residual_mass = info.residual_mass;
      // 診断用: W⁰ の残差と、セルを通る質量流量
      R0_ = R_;
      throughput0_.assign(zu(nc), 0.0);
#pragma omp parallel for schedule(static)
      for (Index c = 0; c < nc; ++c) {
        double s = 0.0;
        for (Offset k = csr_.begin(c); k < csr_.end(c); ++k) {
          const Index f = csr_.faces[zu(k)];
          s += f < nif ? std::abs(face_res_[zu(f)].mdot) : std::abs(bF_[zu(f - nif)][0]);
        }
        throughput0_[zu(c)] = s;
      }
    }
    // 擬似時間刻み(式 (56))。cfl_start を与えたときは残差に応じて立ち上げる(SER)
    double cfl = options.cfl;
    {
      const double growth = std::max(options.cfl_growth, 1.0);
      const double r = info.residual_mass + info.residual_momentum + info.residual_energy;
      if (m == 0) ser_r0 = r;
      if (ramp) {
        const double target = std::max(options.cfl_start, options.cfl_start * ser_r0 / std::max(r, 1e-300));
        cfl = cfl_prev > 0.0 ? std::min({options.cfl, cfl_prev * growth, target}) : options.cfl_start;
      } else if (cfl_prev > 0.0) {
        cfl = std::min(options.cfl, cfl_prev * growth);
      }
      cfl = std::min(cfl, cfl_cap);
    }
    info.cfl = cfl;
#pragma omp parallel for schedule(static)
    for (Index c = 0; c < nc; ++c) {
      dt_[zu(c)] = cfl * geo_.cell_volume[zu(c)] / lambda_[zu(c)];
    }
    {
      ScopedTimer st(report.timing, "組み立て(p, u)");
      assemble_pu(A, rhs, options.continuity_pseudo_time);
    }
    x.assign(rhs.size(), 0.0);
    const LinearSolveResult lr_pu = solve_linear_system(A, rhs, x, linear_);
    report.timing.add("線形ソルバー(p, u)", lr_pu.setup_seconds + lr_pu.solve_seconds);
    info.linear_iterations_pu = lr_pu.iterations;
    info.linear_residual_pu = lr_pu.relative_residual;
    report.linear_iterations_pu += lr_pu.iterations;
    report.max_linear_iterations_pu = std::max(report.max_linear_iterations_pu, lr_pu.iterations);
    if (!lr_pu.converged) ++report.linear_not_converged;
    if (!(lr_pu.relative_residual <= params_.reject_linear_residual)) {
      reject();
      continue;
    }
    {
      const double alpha = params_.relaxation;
      const double keep = 1.0 - params_.max_relative_decrease;
      const double floor = params_.pressure_floor;
      long long clipped = 0;
#pragma omp parallel for schedule(static) reduction(+ : clipped)
      for (Index c = 0; c < nc; ++c) {
        const auto ci = zu(c);
        const double p = W_[0][ci];
        double pn = p + alpha * x[4 * ci];
        if (!(pn >= keep * p)) {
          pn = keep * p;
          ++clipped;
        }
        if (pn < floor) {
          pn = std::max(floor, p);
          ++clipped;
        }
        W_[0][ci] = pn;
        for (std::size_t a = 0; a < 3; ++a) W_[1 + a][ci] += alpha * x[4 * ci + 1 + a];
      }
      info.clipped_p = clipped;
    }

    // --- 温度(p, u 固定) ---
    {
      ScopedTimer st(report.timing, "評価");
      evaluate();
    }
    {
      ScopedTimer st(report.timing, "組み立て(T)");
      assemble_T(A, rhs);
    }
    {
      std::vector<double> r2(zu(nc));
#pragma omp parallel for schedule(static)
      for (Index c = 0; c < nc; ++c) r2[zu(c)] = rhs[zu(c)] * rhs[zu(c)];
      info.residual_temperature = std::sqrt(deterministic_sum(r2));
    }
    x.assign(rhs.size(), 0.0);
    const LinearSolveResult lr_T = solve_linear_system(A, rhs, x, linear_);
    report.timing.add("線形ソルバー(T)", lr_T.setup_seconds + lr_T.solve_seconds);
    info.linear_iterations_T = lr_T.iterations;
    info.linear_residual_T = lr_T.relative_residual;
    report.linear_iterations_T += lr_T.iterations;
    if (!lr_T.converged) ++report.linear_not_converged;
    if (!(lr_T.relative_residual <= params_.reject_linear_residual)) {
      reject();
      continue;
    }
    {
      const double alpha = params_.relaxation;
      const double keep = 1.0 - params_.max_relative_decrease;
      const double floor = params_.temperature_floor;
      long long clipped = 0;
#pragma omp parallel for schedule(static) reduction(+ : clipped)
      for (Index c = 0; c < nc; ++c) {
        const auto ci = zu(c);
        const double T = W_[4][ci];
        double Tn = T + alpha * x[ci];
        if (!(Tn >= keep * T)) {
          Tn = keep * T;
          ++clipped;
        }
        if (Tn < floor) {
          Tn = std::max(floor, T);
          ++clipped;
        }
        W_[4][ci] = Tn;
      }
      info.clipped_T = clipped;
    }
    if (closed_domain_) apply_mass_constraint(target_mass);

    // --- 更新量 ---
    {
      const auto& V = geo_.cell_volume;
#pragma omp parallel for schedule(static)
      for (Index c = 0; c < nc; ++c) {
        const auto ci = zu(c);
        const double d = W_[0][ci] - old[0][ci];
        num[ci] = d * d * V[ci];
        den[ci] = old[0][ci] * old[0][ci] * V[ci];
      }
      info.update_p = ratio_norm(num, den);
#pragma omp parallel for schedule(static)
      for (Index c = 0; c < nc; ++c) {
        const auto ci = zu(c);
        double d2 = 0.0;
        double u2 = 0.0;
        for (int a = 1; a <= 3; ++a) {
          const double d = W_[a][ci] - old[a][ci];
          d2 += d * d;
          u2 += old[a][ci] * old[a][ci];
        }
        num[ci] = d2 * V[ci];
        den[ci] = (u2 + old[4][ci]) * V[ci];
      }
      info.update_u = ratio_norm(num, den);
#pragma omp parallel for schedule(static)
      for (Index c = 0; c < nc; ++c) {
        const auto ci = zu(c);
        const double d = W_[4][ci] - old[4][ci];
        num[ci] = d * d * V[ci];
        den[ci] = old[4][ci] * old[4][ci] * V[ci];
      }
      info.update_T = ratio_norm(num, den);
    }

    report.iterations = m + 1;
    report.final_update = info.update_max();
    cfl_prev = info.cfl;
    cfl_cap = std::numeric_limits<double>::infinity();
    info.seconds = it_timer.seconds();
    if (options.on_iteration) options.on_iteration(info);
    GSIS_CHECK(std::isfinite(info.update_max()),
               std::format("PBC の反復 {} で更新量が有限でなくなりました(発散)", m));
    if (info.update_max() < options.tolerance) {
      report.converged = true;
      break;
    }
    if (options.should_stop && options.should_stop()) {
      report.stopped = true;
      break;
    }
  }
  // 最後の状態で評価し直す(診断量・出力と、次の段の ṁ̂ のため)
  {
    ScopedTimer st(report.timing, "評価");
    evaluate();
  }
  residual_norms(report.final_residual_mass, report.final_residual_momentum, report.final_residual_energy,
                 report.final_kinetic_energy_defect);
  report.final_patch_mass_flow = patch_mass_flow();
  store_state(out);
  report.timing.add("段の合計", stage_timer.seconds());
  return report;
}

void PbcSolver::residual_norms(double& mass, double& momentum, double& energy, double& defect) const {
  const Index nc = mesh_.n_cells;
  std::vector<double> a(zu(nc));
  std::vector<double> b(zu(nc));
  std::vector<double> e(zu(nc));
#pragma omp parallel for schedule(static)
  for (Index c = 0; c < nc; ++c) {
    const auto ci = zu(c);
    const std::array<double, 5>& R = R_[ci];
    a[ci] = R[0] * R[0];
    b[ci] = R[1] * R[1] + R[2] * R[2] + R[3] * R[3];
    const double re = R[4] - defect_[ci];
    e[ci] = re * re;
  }
  mass = std::sqrt(deterministic_sum(a));
  momentum = std::sqrt(deterministic_sum(b));
  energy = std::sqrt(deterministic_sum(e));
  defect = deterministic_sum(defect_);
}

PbcStageReport PbcSolver::solve_gsis_stage(const KineticMoments& moments, const BoundaryKineticFluxes& boundary,
                                           int max_iterations, double cfl, CellMacroState& out,
                                           std::function<void(const PbcIterationInfo&)> on_iteration) {
  const HigherOrderTerms hot = compute_hot(moments, boundary);
  PbcStageInput in;
  in.initial = &moments.state;
  in.hot = &hot;
  in.boundary = &boundary;
  PbcStageOptions opt;
  opt.max_iterations = max_iterations;
  opt.cfl = cfl;
  opt.tolerance = params_.pbc_tolerance;
  opt.continuity_pseudo_time = params_.gsis_continuity_pseudo_time;
  opt.cfl_start = params_.gsis_cfl_start;  // 0 なら CFL 固定(論文どおり)
  opt.cfl_growth = params_.gsis_cfl_growth;
  opt.on_iteration = std::move(on_iteration);
  return run_stage(in, opt, out);
}

PbcStageReport PbcSolver::solve_standalone(CellMacroState& state, const PbcStageOptions& options) {
  const CellMacroState initial = state;
  PbcStageInput in;
  in.initial = &initial;
  PbcStageOptions opt = options;
  if (!(opt.tolerance > 0.0)) opt.tolerance = params_.pbc_tolerance;
  return run_stage(in, opt, state);
}

PbcStageReport PbcSolver::solve_standalone(CellMacroState& state, int max_iterations, double cfl) {
  PbcStageOptions opt;
  opt.max_iterations = max_iterations;
  opt.cfl = cfl;
  opt.tolerance = params_.pbc_tolerance;
  return solve_standalone(state, opt);
}

std::vector<std::array<double, 5>> PbcSolver::evaluate_residual(const CellMacroState& state) {
  load_state(state);
  has_hot_ = false;
  face_hot_ = false;
  n_frozen_ = 0;
  source_ = nullptr;
  standalone_ = true;
  set_face_state_from_cells();
  std::fill(C_plus_.begin(), C_plus_.end(), vec5_zero());
  std::fill(C_minus_.begin(), C_minus_.end(), vec5_zero());
  evaluate();
  return R_;
}

std::vector<std::array<double, 5>> PbcSolver::residual_frozen(const CellMacroState& state) {
  load_state(state);
  has_hot_ = false;
  face_hot_ = false;
  n_frozen_ = 0;
  source_ = nullptr;
  standalone_ = true;
  std::fill(C_plus_.begin(), C_plus_.end(), vec5_zero());
  std::fill(C_minus_.begin(), C_minus_.end(), vec5_zero());
  const bool had = has_mdot_hat_;
  freeze_lag_ = true;
  has_mdot_hat_ = had;
  evaluate();
  freeze_lag_ = false;
  return R_;
}

void PbcSolver::linearize_pu(const CellMacroState& state, double cfl, BlockCsrMatrix& A, std::vector<double>& rhs) {
  residual_frozen(state);
  for (Index c = 0; c < mesh_.n_cells; ++c) dt_[zu(c)] = cfl * geo_.cell_volume[zu(c)] / lambda_[zu(c)];
  assemble_pu(A, rhs);
}

void PbcSolver::linearize_T(const CellMacroState& state, double cfl, BlockCsrMatrix& A, std::vector<double>& rhs) {
  residual_frozen(state);
  for (Index c = 0; c < mesh_.n_cells; ++c) dt_[zu(c)] = cfl * geo_.cell_volume[zu(c)] / lambda_[zu(c)];
  assemble_T(A, rhs);
}

HigherOrderTerms PbcSolver::last_hot() const {
  HigherOrderTerms h;
  h.sigma.assign(zu(mesh_.n_cells), std::array<double, 6>{});
  h.q.assign(zu(mesh_.n_cells), std::array<double, 3>{});
  if (has_hot_) {
    for (Index c = 0; c < mesh_.n_cells; ++c) {
      h.sigma[zu(c)] = hot_sigma_[zu(c)];
      h.q[zu(c)] = {hot_q_[zu(c)].x, hot_q_[zu(c)].y, hot_q_[zu(c)].z};
    }
  }
  return h;
}

std::vector<Vec5> PbcSolver::patch_flux() const {
  std::vector<Vec5> flux(mesh_.patches.size(), vec5_zero());
  const Index nif = mesh_.n_internal_faces();
  for (std::size_t p = 0; p < mesh_.patches.size(); ++p) {
    const Patch& patch = mesh_.patches[p];
    for (Index f = patch.start; f < patch.end(); ++f) flux[p] = vec5_add(flux[p], bF_[zu(f - nif)]);
  }
  return flux;
}

std::vector<double> PbcSolver::internal_face_mass_flux() const {
  std::vector<double> out(face_res_.size(), 0.0);
  for (std::size_t f = 0; f < face_res_.size(); ++f) out[f] = face_res_[f].F[0];
  return out;
}

std::vector<double> PbcSolver::patch_mass_flow() const {
  std::vector<double> flow(mesh_.patches.size(), 0.0);
  const Index nif = mesh_.n_internal_faces();
  for (std::size_t p = 0; p < mesh_.patches.size(); ++p) {
    const Patch& patch = mesh_.patches[p];
    double s = 0.0;
    for (Index f = patch.start; f < patch.end(); ++f) s += bF_[zu(f - nif)][0];
    flow[p] = s;
  }
  return flow;
}

}  // namespace gsis
