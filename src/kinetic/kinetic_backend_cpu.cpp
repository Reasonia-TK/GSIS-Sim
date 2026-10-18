// 運動論ソルバーの CPU 参照実装。1 セル・1 速度点の計算は kinetic_kernels.hpp の共通関数を使い、
// 並列化は OpenMP(書き込み先が重ならない単位で分けるので、結果はスレッド数によらない)。

#include <algorithm>
#include <cmath>
#include <format>
#include <vector>

#include "core/error.hpp"
#include "core/timer.hpp"
#include "kinetic/kinetic_backend.hpp"
#include "kinetic/kinetic_kernels.hpp"

namespace gsis::kinetic {
namespace {

template <class F>
class CpuBackend final : public KineticBackend {
 public:
  explicit CpuBackend(const KineticSetup& s) : s_(s) {
    nv_ = s.nv();
    n_cells_ = s.n_cells();
    n_bfaces_ = s.n_bfaces();
    auto to_f = [](const std::vector<double>& x) {
      std::vector<F> out(x.size());
      for (std::size_t i = 0; i < x.size(); ++i) out[i] = static_cast<F>(x[i]);
      return out;
    };
    xi_x_ = to_f(s.velocity.xi_x);
    xi_y_ = to_f(s.velocity.xi_y);
    xi_z_ = to_f(s.velocity.xi_z);
    csr_offsets_ = make_csr_offsets(s);
    csr_ = make_csr_entries<F>(s);
    eps2_ = make_eps2<F>(s);
    bfaces_ = make_bface_infos<F>(s);
    binflow_.assign(static_cast<std::size_t>(n_bfaces_), BoundaryInflow<F>{});
    cells_.assign(static_cast<std::size_t>(n_cells_), CellParams<F>{});
    f_.assign(static_cast<std::size_t>(n_cells_) * static_cast<std::size_t>(nv_), F(0));
    moments_.assign(static_cast<std::size_t>(n_cells_) * kMomentsPerCell, 0.0);
    shift_.assign(static_cast<std::size_t>(n_cells_) * 3, 0.0);
    // チャンク幅: 指定があればそれ(軌道の大きさの倍数に切り上げ)、なければ全点
    const int osize = s.velocity.orbit_size;
    long long k = s.params.velocity_chunk > 0 ? s.params.velocity_chunk : nv_;
    k = (k + osize - 1) / osize * osize;
    chunk_ = static_cast<int>(std::min<long long>(k, nv_));
    const std::size_t work = static_cast<std::size_t>(n_cells_) * static_cast<std::size_t>(chunk_);
    gx_.assign(work, F(0));
    gy_.assign(work, F(0));
    gz_.assign(work, F(0));
    r_.assign(work, F(0));
  }

  const char* name() const override { return "cpu"; }
  int chunk_width() const override { return chunk_; }
  std::size_t memory_bytes() const override {
    return sizeof(F) * (f_.size() + gx_.size() + gy_.size() + gz_.size() + r_.size()) +
           sizeof(CsrEntry<F>) * csr_.size() + sizeof(double) * moments_.size();
  }

  void set_equilibrium(std::span<const double> states) override {
    GSIS_CHECK(states.size() == static_cast<std::size_t>(n_cells_) * 5, "初期状態の長さがセル数と合いません");
    Timer timer;
    const KineticView<F> v = view();
#pragma omp parallel for schedule(static)
    for (int c = 0; c < n_cells_; ++c) {
      const double* st = &states[static_cast<std::size_t>(c) * 5];
      const MaxwellParams<F> m = equilibrium_params(st[0], st[1], st[2], st[3], st[4]);
      for (int p = 0; p < nv_; ++p) {
        v.f[static_cast<i64>(c) * nv_ + p] = maxwellian(m, velocity_at(v, p));
      }
      shift_[static_cast<std::size_t>(c) * 3 + 0] = st[1];
      shift_[static_cast<std::size_t>(c) * 3 + 1] = st[2];
      shift_[static_cast<std::size_t>(c) * 3 + 2] = st[3];
    }
    timings_.other += timer.seconds();
  }

  void set_distribution(std::span<const double> f) override {
    GSIS_CHECK(f.size() == f_.size(), "分布関数の長さが合いません");
    for (std::size_t i = 0; i < f.size(); ++i) f_[i] = static_cast<F>(f[i]);
  }

  void get_distribution(std::span<double> f) override {
    GSIS_CHECK(f.size() == f_.size(), "分布関数の長さが合いません");
    for (std::size_t i = 0; i < f.size(); ++i) f[i] = static_cast<double>(f_[i]);
  }

  void compute_moments(std::span<double> out) override {
    GSIS_CHECK(out.size() == moments_.size(), "モーメントの出力の長さが合いません");
    Timer timer;
    const double w = s_.velocity.weight;
#pragma omp parallel for schedule(static)
    for (int c = 0; c < n_cells_; ++c) {
      const double u0x = shift_[static_cast<std::size_t>(c) * 3 + 0];
      const double u0y = shift_[static_cast<std::size_t>(c) * 3 + 1];
      const double u0z = shift_[static_cast<std::size_t>(c) * 3 + 2];
      double sums[kMomentSums] = {};
      double t[kMomentSums];
      for (int p = 0; p < nv_; ++p) {
        const double fv = static_cast<double>(f_[static_cast<std::size_t>(c) * nv_ + static_cast<std::size_t>(p)]);
        moment_terms<double>(fv, s_.velocity.xi_x[static_cast<std::size_t>(p)] - u0x,
                             s_.velocity.xi_y[static_cast<std::size_t>(p)] - u0y,
                             s_.velocity.xi_z[static_cast<std::size_t>(p)] - u0z, t);
        for (int k = 0; k < kMomentSums; ++k) sums[k] += t[k];
      }
      double* m = &moments_[static_cast<std::size_t>(c) * kMomentsPerCell];
      finalize_moments(sums, w, u0x, u0y, u0z, m);
      if (m[0] > 0.0) {
        shift_[static_cast<std::size_t>(c) * 3 + 0] = m[1];
        shift_[static_cast<std::size_t>(c) * 3 + 1] = m[2];
        shift_[static_cast<std::size_t>(c) * 3 + 2] = m[3];
      }
    }
    std::copy(moments_.begin(), moments_.end(), out.begin());
    timings_.moments += timer.seconds();
  }

  void scale_distribution(double s) override {
    Timer timer;
    const F sf = static_cast<F>(s);
    for (F& x : f_) x *= sf;
    for (int c = 0; c < n_cells_; ++c) {
      double* m = &moments_[static_cast<std::size_t>(c) * kMomentsPerCell];
      m[0] *= s;  // ρ
      for (int k = 5; k < kMomentsPerCell; ++k) m[k] *= s;  // p, σ, q
    }
    timings_.other += timer.seconds();
  }

  void update_cell_params() override {
    Timer timer;
    deq_flags_.assign(static_cast<std::size_t>(n_cells_), 0);
#pragma omp parallel for schedule(static)
    for (int c = 0; c < n_cells_; ++c) {
      const double* m = &moments_[static_cast<std::size_t>(c) * kMomentsPerCell];
      CellParams<F> cp = make_cell_params<F>(m, s_.volume[static_cast<std::size_t>(c)],
                                             s_.vdt[static_cast<std::size_t>(c)], s_.mu0, s_.omega, s_.Pr,
                                             s_.params.cold_bgk_temperature);
      if (s_.discrete_eq) {
        MaxwellParams<F> eq;
        double gamma[5];
        const int r = discrete_maxwell_seq<F>(xi_x_.data(), xi_y_.data(), xi_z_.data(), nv_, s_.velocity.weight,
                                              s_.deq_grid, m[0], m[1], m[2], m[3], m[4], &cp.s, eq, gamma);
        if (r > 0) {
          cp.s.eq = eq;
          cp.s.g0 = static_cast<F>(gamma[0]);
          cp.s.g = Vec3T<F>{static_cast<F>(gamma[1]), static_cast<F>(gamma[2]), static_cast<F>(gamma[3])};
          cp.s.g4 = static_cast<F>(gamma[4]);
        } else if (r < 0 && deq_fallback_collisionless()) {
          // ニュートン法が収束しない(モーメントが速度格子で表せる限界に近い): 衝突なし(目標分布 = f)
          cp.coll = F(0);
          cp.diag = static_cast<F>(s_.vdt[static_cast<std::size_t>(c)]);
        }
        deq_flags_[static_cast<std::size_t>(c)] = r;
      }
      cells_[static_cast<std::size_t>(c)] = cp;
    }
    timings_.other += timer.seconds();
  }

  DeqCounts deq_counts() override {
    DeqCounts out;
    for (const int x : deq_flags_) {
      if (x > 0) ++out.corrected;
      if (x < 0) ++out.failed;
      out.max_iterations = std::max(out.max_iterations, x);
    }
    return out;
  }

  void set_inflow(std::span<const InflowState> inflow) override {
    GSIS_CHECK(inflow.size() == static_cast<std::size_t>(n_bfaces_), "流入分布の長さが境界面の数と合いません");
    for (std::size_t b = 0; b < inflow.size(); ++b) {
      const InflowState& in = inflow[b];
      binflow_[b].m = maxwell_params_from<F>(in.rho, in.u.x, in.u.y, in.u.z, in.T);
      binflow_[b].scale = static_cast<F>(in.scale);
    }
  }

  void boundary_sums(std::span<double> out) override {
    GSIS_CHECK(out.size() == static_cast<std::size_t>(n_bfaces_) * kBoundarySumsPerFace,
               "境界の集計の出力の長さが合いません");
    Timer timer;
    const KineticView<F> v = view();
    const F w = static_cast<F>(s_.velocity.weight);
#pragma omp parallel for schedule(dynamic, 4)
    for (int b = 0; b < n_bfaces_; ++b) {
      const BoundaryFaceInfo<F>& bf = bfaces_[static_cast<std::size_t>(b)];
      Accum<double> acc[kBoundarySumsPerFace];
      for (int p = 0; p < nv_; ++p) {
        const Vec3T<F> xi = velocity_at(v, p);
        const F a_n = xi.x * bf.nx + xi.y * bf.ny + xi.z * bf.nz;
        if (a_n >= F(0) || bf.kind == kBcPeriodic) {
          const F fb = reconstruct_at_boundary(v, bf, p, xi);
          add_boundary_terms<F, Accum<double>>(w * fb, xi, a_n, acc + 0, acc + 10);
        } else {
          F fb;
          if (bf.kind == kBcSymmetry) {
            const int pm = p ^ bf.mirror_xor;
            fb = reconstruct_at_boundary(v, bf, pm, velocity_at(v, pm));
          } else {
            fb = maxwellian(binflow_[static_cast<std::size_t>(b)].m, xi);  // 単位(scale を掛けない)
          }
          add_boundary_terms<F, Accum<double>>(w * fb, xi, a_n, acc + 5, acc + 15);
        }
      }
      for (int k = 0; k < kBoundarySumsPerFace; ++k) {
        out[static_cast<std::size_t>(b) * kBoundarySumsPerFace + static_cast<std::size_t>(k)] = acc[k].value();
      }
    }
    timings_.boundary += timer.seconds();
  }

  void internal_face_mass_flux(std::span<const std::int32_t> cells, std::span<const std::int32_t> entries,
                               std::span<double> out) override {
    GSIS_CHECK(cells.size() == entries.size() && out.size() == cells.size(), "内部面の流束の引数の長さが合いません");
    Timer timer;
    const KineticView<F> v = view();
    const int n = static_cast<int>(cells.size());
#pragma omp parallel for schedule(dynamic, 4)
    for (int i = 0; i < n; ++i) {
      const int c = cells[static_cast<std::size_t>(i)];
      const int e = entries[static_cast<std::size_t>(i)];
      GSIS_ASSERT(c >= 0 && c < n_cells_ && e >= csr_offsets_[static_cast<std::size_t>(c)] &&
                  e < csr_offsets_[static_cast<std::size_t>(c) + 1] && csr_[static_cast<std::size_t>(e)].other >= 0);
      const CsrEntry<F>& ce = csr_[static_cast<std::size_t>(e)];
      Accum<double> acc;
      for (int p = 0; p < nv_; ++p) acc.add(static_cast<double>(internal_face_flux_term(v, c, ce, p, velocity_at(v, p))));
      out[static_cast<std::size_t>(i)] = static_cast<double>(v.weight) * acc.value();
    }
    timings_.other += timer.seconds();
  }

  void sweep(std::span<double> residual_cell) override {
    GSIS_CHECK(residual_cell.size() == static_cast<std::size_t>(n_cells_), "残差の出力の長さが合いません");
    std::fill(residual_cell.begin(), residual_cell.end(), 0.0);
    const KineticView<F> v = view();
    const int n_blocks = s_.n_blocks();
    for (int k0 = 0; k0 < nv_; k0 += chunk_) {
      ChunkView<F> ch;
      ch.k0 = k0;
      ch.kc = std::min(chunk_, nv_ - k0);
      ch.stride = chunk_;
      ch.gx = gx_.data();
      ch.gy = gy_.data();
      ch.gz = gz_.data();
      ch.r = r_.data();
      Timer timer;
#pragma omp parallel for schedule(static)
      for (int c = 0; c < n_cells_; ++c) {
        for (int t = 0; t < ch.kc; ++t) {
          const int p = k0 + t;
          const Vec3T<F> xi = velocity_at(v, p);
          const Vec3T<F> g = limited_gradient(v, c, p, xi, f_at(v, c, p));
          const i64 wi = static_cast<i64>(c) * ch.stride + t;
          ch.gx[wi] = g.x;
          ch.gy[wi] = g.y;
          ch.gz[wi] = g.z;
        }
      }
      timings_.gradient += timer.seconds();
      timer.reset();
#pragma omp parallel for schedule(static)
      for (int c = 0; c < n_cells_; ++c) {
        double r2 = 0.0;
        for (int t = 0; t < ch.kc; ++t) {
          const F R = residual_value(v, ch, c, t);
          ch.r[static_cast<i64>(c) * ch.stride + t] = R;
          r2 += static_cast<double>(R) * static_cast<double>(R);
        }
        residual_cell[static_cast<std::size_t>(c)] += r2;
      }
      timings_.residual += timer.seconds();
      const int n_lanes = n_blocks * ch.kc;
      // ブロック間の結合の反復(block_passes)。gx に残差、gy に前の回の Δf を置く
      const int passes = static_cast<int>(s_.params.block_passes);
      if (passes > 1) std::copy(r_.begin(), r_.end(), gx_.begin());
      for (int pass = 0; pass < passes; ++pass) {
        ChunkView<F> cp = ch;
        if (pass > 0) {
          std::copy(r_.begin(), r_.end(), gy_.begin());
          cp.r0 = gx_.data();
          cp.dprev = gy_.data();
        }
        cp.update_f = pass + 1 == passes ? 1 : 0;
        timer.reset();
#pragma omp parallel for schedule(static)
        for (int lane = 0; lane < n_lanes; ++lane) {
          const int b = lane / ch.kc;
          const int t = lane % ch.kc;
          const Vec3T<F> xi = velocity_at(v, k0 + t);
          const int c0 = s_.block_offsets[static_cast<std::size_t>(b)];
          const int c1 = s_.block_offsets[static_cast<std::size_t>(b) + 1];
          const std::int32_t* order;
          const std::int32_t* pos;
          sweep_order_of(v, k0 + t, order, pos);
          for (int k = c0; k < c1; ++k) sweep_forward_cell(v, cp, order ? order[k] : k, t, c0, c1, xi, pos);
        }
        timings_.sweep_forward += timer.seconds();
        timer.reset();
#pragma omp parallel for schedule(static)
        for (int lane = 0; lane < n_lanes; ++lane) {
          const int b = lane / ch.kc;
          const int t = lane % ch.kc;
          const Vec3T<F> xi = velocity_at(v, k0 + t);
          const int c0 = s_.block_offsets[static_cast<std::size_t>(b)];
          const int c1 = s_.block_offsets[static_cast<std::size_t>(b) + 1];
          const std::int32_t* order;
          const std::int32_t* pos;
          sweep_order_of(v, k0 + t, order, pos);
          for (int k = c1 - 1; k >= c0; --k) sweep_backward_cell(v, cp, order ? order[k] : k, t, c0, c1, xi, pos);
        }
        timings_.sweep_backward += timer.seconds();
      }
    }
  }

  long long gsis_correction(std::span<const double> w_half, std::span<const double> w_new,
                            std::span<int> replaced_flags) override {
    GSIS_CHECK(w_half.size() == static_cast<std::size_t>(n_cells_) * 5 &&
                   w_new.size() == static_cast<std::size_t>(n_cells_) * 5,
               "GSIS の補正のマクロ量の長さがセル数と合いません");
    Timer timer;
    const KineticView<F> v = view();
    const std::string& rule = s_.params.gsis_negative_check;
    const int mode = rule == "created" ? kNegativeCreated
                     : rule == "limit"   ? kNegativeLimit
                     : rule == "blend"   ? kNegativeBlend
                                         : kNegativeAll;
    long long replaced = 0;
#pragma omp parallel for schedule(static) reduction(+ : replaced)
    for (int c = 0; c < n_cells_; ++c) {
      const double* wh = &w_half[static_cast<std::size_t>(c) * 5];
      const double* wn = &w_new[static_cast<std::size_t>(c) * 5];
      int rh = 0;
      int rn = 0;
      const MaxwellParams<F> mh = equilibrium_params(wh[0], wh[1], wh[2], wh[3], wh[4], &rh);
      const MaxwellParams<F> mn = equilibrium_params(wn[0], wn[1], wn[2], wn[3], wn[4], &rn);
      if (deq_fallback_collisionless() && (rh < 0 || rn < 0)) {
        // 離散平衡が作れない(W が速度格子で表せる限界に近い・超える)セルは補正しない(f はそのまま)
        ++replaced;
        if (!replaced_flags.empty()) replaced_flags[static_cast<std::size_t>(c)] = kCorrectionSkipped;
        shift_[static_cast<std::size_t>(c) * 3 + 0] = wh[1];
        shift_[static_cast<std::size_t>(c) * 3 + 1] = wh[2];
        shift_[static_cast<std::size_t>(c) * 3 + 2] = wh[3];
        continue;
      }
      F* fc = v.f + static_cast<i64>(c) * nv_;
      const double threshold = -1e-8 * wn[0] / (wn[4] * std::sqrt(wn[4]));
      bool replace = false;
      if (mode == kNegativeLimit) {
        // 置き換えずに、負の部分が閾値を超えない範囲まで補正を縮める(f += α d)
        F alpha = F(1);
        for (int p = 0; p < nv_; ++p) {
          const Vec3T<F> xi = velocity_at(v, p);
          alpha = std::min(alpha, correction_alpha_limit(fc[p], maxwellian(mn, xi) - maxwellian(mh, xi),
                                                          static_cast<F>(threshold)));
        }
        for (int p = 0; p < nv_; ++p) {
          const Vec3T<F> xi = velocity_at(v, p);
          fc[p] += alpha * (maxwellian(mn, xi) - maxwellian(mh, xi));
        }
        replace = alpha < F(1);
      } else if (mode == kNegativeBlend) {
        // 補正した f と f^eq(W_new) を、負の部分が閾値を超えない最小の割合 θ で混ぜる(モーメントは W_new のまま)
        F theta = F(0);
        for (int p = 0; p < nv_; ++p) {
          const Vec3T<F> xi = velocity_at(v, p);
          const F e = maxwellian(mn, xi);
          theta = std::max(theta, blend_theta(fc[p], e - maxwellian(mh, xi), e, static_cast<F>(threshold)));
        }
        for (int p = 0; p < nv_; ++p) {
          const Vec3T<F> xi = velocity_at(v, p);
          const F e = maxwellian(mn, xi);
          const F g = fc[p] + (e - maxwellian(mh, xi));
          fc[p] = (F(1) - theta) * g + theta * e;
        }
        replace = theta > F(0);
      } else {
        F fmin = F(0);
        bool first = true;
        for (int p = 0; p < nv_; ++p) {
          const Vec3T<F> xi = velocity_at(v, p);
          const F old = fc[p];
          const F x = old + (maxwellian(mn, xi) - maxwellian(mh, xi));
          fc[p] = x;
          const F chk = negative_check_value(old, x, mode == kNegativeCreated);
          fmin = first ? chk : std::min(fmin, chk);
          first = false;
        }
        replace = static_cast<double>(fmin) < threshold;
        if (replace) {
          for (int p = 0; p < nv_; ++p) fc[p] = maxwellian(mn, velocity_at(v, p));
        }
      }
      if (replace) ++replaced;
      if (!replaced_flags.empty()) replaced_flags[static_cast<std::size_t>(c)] = replace ? 1 : 0;
      shift_[static_cast<std::size_t>(c) * 3 + 0] = wn[1];
      shift_[static_cast<std::size_t>(c) * 3 + 1] = wn[2];
      shift_[static_cast<std::size_t>(c) * 3 + 2] = wn[3];
    }
    timings_.other += timer.seconds();
    return replaced;
  }

  StepTimings take_timings() override {
    const StepTimings t = timings_;
    timings_ = StepTimings{};
    return t;
  }

 private:
  // 平衡分布の係数([kinetic].equilibrium = "discrete" なら保存的な離散平衡)。result にニュートン法の結果
  // (0 = 補正不要、正 = 反復数、−1 = 失敗で連続の Maxwell 分布)を返す
  MaxwellParams<F> equilibrium_params(double rho, double ux, double uy, double uz, double T,
                                      int* result = nullptr) const {
    if (result != nullptr) *result = 0;
    if (!s_.discrete_eq) return maxwell_params_from<F>(rho, ux, uy, uz, T);
    MaxwellParams<F> m;
    double gamma[5];
    const int r = discrete_maxwell_seq<F>(xi_x_.data(), xi_y_.data(), xi_z_.data(), nv_, s_.velocity.weight,
                                          s_.deq_grid, rho, ux, uy, uz, T, nullptr, m, gamma);
    if (result != nullptr) *result = r;
    return m;
  }

  bool deq_fallback_collisionless() const { return s_.params.deq_fallback == "collisionless"; }

  KineticView<F> view() {
    KineticView<F> v;
    v.n_cells = n_cells_;
    v.nv = nv_;
    v.n_bfaces = n_bfaces_;
    v.xi_x = xi_x_.data();
    v.xi_y = xi_y_.data();
    v.xi_z = xi_z_.data();
    v.weight = static_cast<F>(s_.velocity.weight);
    v.csr_offsets = csr_offsets_.data();
    v.csr = csr_.data();
    v.eps2 = eps2_.data();
    v.limiter_mode = s_.limiter_mode;
    v.positivity = s_.params.positivity_limiter ? 1 : 0;
    v.diag_scale = static_cast<F>(s_.diag_scale);
    v.interface_damping = static_cast<F>(s_.params.interface_damping);
    v.bfaces = bfaces_.data();
    v.binflow = binflow_.data();
    v.cells = cells_.data();
    v.f = f_.data();
    v.n_sweep_dirs = s_.n_sweep_dirs;
    if (s_.n_sweep_dirs > 0) {
      v.sweep_order = s_.sweep_order.data();
      v.sweep_pos = s_.sweep_pos.data();
      v.sweep_dir_of = s_.sweep_dir_of.data();
    }
    v.discrete_eq = s_.discrete_eq ? 1 : 0;
    v.deq = s_.deq_grid;
    v.deq_fallback = deq_fallback_collisionless() ? 1 : 0;
    v.cold_bgk_temperature = s_.params.cold_bgk_temperature;
    return v;
  }

  const KineticSetup& s_;
  int nv_ = 0;
  int n_cells_ = 0;
  int n_bfaces_ = 0;
  int chunk_ = 0;
  std::vector<F> xi_x_, xi_y_, xi_z_;
  std::vector<std::int32_t> csr_offsets_;
  std::vector<CsrEntry<F>> csr_;
  std::vector<F> eps2_;
  std::vector<BoundaryFaceInfo<F>> bfaces_;
  std::vector<BoundaryInflow<F>> binflow_;
  std::vector<CellParams<F>> cells_;
  std::vector<F> f_;
  std::vector<double> moments_;
  std::vector<double> shift_;
  std::vector<F> gx_, gy_, gz_, r_;
  std::vector<int> deq_flags_;
  StepTimings timings_;
};

}  // namespace

std::unique_ptr<KineticBackend> make_cpu_backend(const KineticSetup& setup) {
  if (setup.params.precision == "fp64") return std::make_unique<CpuBackend<double>>(setup);
  return std::make_unique<CpuBackend<float>>(setup);
}

}  // namespace gsis::kinetic
