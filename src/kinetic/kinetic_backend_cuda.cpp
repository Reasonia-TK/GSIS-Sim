// 運動論ソルバーの GPU 版(ホスト側)。GPU のメモリの確保・転送・カーネルの起動と、処理ごとの計時。
// カーネルは kinetic_cuda_kernels.cu。1 回の起動を短くするため(WDDM の TDR)、セル・面・スイープの
// 範囲を分けて起動する(launch_cells、sweep_cells_per_launch)。

#include <cuda_runtime.h>

#include <algorithm>
#include <format>
#include <vector>

#include "core/error.hpp"
#include "core/log.hpp"
#include "gpu/cuda_check.hpp"
#include "gpu/device_buffer.hpp"
#include "gpu/device_info.hpp"
#include "kinetic/kinetic_backend.hpp"
#include "kinetic/kinetic_cuda_launch.hpp"
#include "kinetic/kinetic_kernels.hpp"

namespace gsis::kinetic {
namespace {

using gpu::DeviceBuffer;

constexpr int kBoundaryFacesPerLaunch = 2048;
constexpr int kTile = 256;  // 残差の部分和の単位(kinetic_cuda_kernels.cu の kCellTile と同じ)

// 処理ごとの時間を CUDA イベントで測る(既定のストリームの順に記録し、flush で同期して足す)
class PhaseTimer {
 public:
  enum Phase { kMoments, kBoundary, kGradient, kResidual, kForward, kBackward, kOther, kCount };

  ~PhaseTimer() {
    for (cudaEvent_t e : pool_) cudaEventDestroy(e);
  }
  void begin(Phase phase) {
    current_ = phase;
    start_ = take();
    GSIS_CUDA_CHECK(cudaEventRecord(start_));
  }
  void end() {
    cudaEvent_t stop = take();
    GSIS_CUDA_CHECK(cudaEventRecord(stop));
    pending_.push_back({current_, start_, stop});
  }
  // 記録した区間を同期して足す
  void flush(StepTimings& t) {
    if (pending_.empty()) return;
    GSIS_CUDA_CHECK(cudaEventSynchronize(pending_.back().stop));
    for (const Pending& p : pending_) {
      float ms = 0.0f;
      GSIS_CUDA_CHECK(cudaEventElapsedTime(&ms, p.start, p.stop));
      const double s = 1e-3 * ms;
      switch (p.phase) {
        case kMoments: t.moments += s; break;
        case kBoundary: t.boundary += s; break;
        case kGradient: t.gradient += s; break;
        case kResidual: t.residual += s; break;
        case kForward: t.sweep_forward += s; break;
        case kBackward: t.sweep_backward += s; break;
        default: t.other += s; break;
      }
    }
    pending_.clear();
    used_ = 0;
  }

 private:
  struct Pending {
    Phase phase;
    cudaEvent_t start;
    cudaEvent_t stop;
  };
  cudaEvent_t take() {
    if (used_ == pool_.size()) {
      cudaEvent_t e{};
      GSIS_CUDA_CHECK(cudaEventCreate(&e));
      pool_.push_back(e);
    }
    return pool_[used_++];
  }
  std::vector<cudaEvent_t> pool_;
  std::size_t used_ = 0;
  std::vector<Pending> pending_;
  Phase current_ = kOther;
  cudaEvent_t start_{};
};

template <class F>
class CudaBackend final : public KineticBackend {
 public:
  explicit CudaBackend(const KineticSetup& s) : s_(s) {
    GSIS_CHECK(gpu::device_count() > 0, "CUDA デバイスがありません(kinetic.backend = \"cpu\" を使ってください)");
    nv_ = s.nv();
    n_cells_ = s.n_cells();
    n_bfaces_ = s.n_bfaces();
    n_blocks_ = s.n_blocks();
    GSIS_CHECK(n_blocks_ <= 65535, std::format("空間ブロック数 {} が多すぎます(65535 以下)", n_blocks_));
    launch_cells_ = static_cast<int>(std::min<long long>(s.params.launch_cells, n_cells_));
    sweep_cells_ = static_cast<int>(std::min<long long>(s.params.sweep_cells_per_launch, 1LL << 30));

    // 分布関数(最大のもの)が載るかを先に確かめる
    const std::size_t f_bytes = sizeof(F) * static_cast<std::size_t>(n_cells_) * static_cast<std::size_t>(nv_);
    const gpu::MemoryInfo mem0 = gpu::memory_info(0);
    GSIS_CHECK(f_bytes + kMarginBytes < mem0.free,
               std::format("分布関数 {:.3g} GB が GPU の空き {:.3g} GB に収まりません", f_bytes / 1e9, mem0.free / 1e9));

    upload(xi_x_, to_f(s.velocity.xi_x));
    upload(xi_y_, to_f(s.velocity.xi_y));
    upload(xi_z_, to_f(s.velocity.xi_z));
    upload(csr_offsets_, make_csr_offsets(s));
    upload(csr_, make_csr_entries<F>(s));
    upload(eps2_, make_eps2<F>(s));
    upload(bfaces_, make_bface_infos<F>(s));
    binflow_.allocate(static_cast<std::size_t>(std::max(n_bfaces_, 1)));
    cells_.allocate(static_cast<std::size_t>(n_cells_));
    upload(volume_, s.volume);
    upload(vdt_, s.vdt);
    {
      std::vector<std::int32_t> bo(s.block_offsets.begin(), s.block_offsets.end());
      upload(block_offsets_, bo);
    }
    if (s.n_sweep_dirs > 0) {
      upload(sweep_order_, s.sweep_order);
      upload(sweep_pos_, s.sweep_pos);
      upload(sweep_dir_of_, s.sweep_dir_of);
    }
    max_block_cells_ = 0;
    for (int b = 0; b < n_blocks_; ++b) {
      max_block_cells_ = std::max(max_block_cells_, s.block_offsets[static_cast<std::size_t>(b) + 1] -
                                                        s.block_offsets[static_cast<std::size_t>(b)]);
    }
    moments_.allocate(static_cast<std::size_t>(n_cells_) * kMomentsPerCell);
    shift_.allocate(static_cast<std::size_t>(n_cells_) * 3);
    GSIS_CUDA_CHECK(cudaMemset(shift_.data(), 0, shift_.bytes()));
    residual_cell_.allocate(static_cast<std::size_t>(n_cells_));
    bsums_.allocate(static_cast<std::size_t>(std::max(n_bfaces_, 1)) * kBoundarySumsPerFace);
    f_.allocate(static_cast<std::size_t>(n_cells_) * static_cast<std::size_t>(nv_));
    GSIS_CUDA_CHECK(cudaMemset(f_.data(), 0, f_.bytes()));

    // 速度チャンク: 指定があればそれ、なければ空きのメモリから決める
    const int osize = s.velocity.orbit_size;
    const double per_velocity = static_cast<double>(n_cells_) * (4.0 * sizeof(F) + 8.0 / kTile);
    long long k = 0;
    if (s.params.velocity_chunk > 0) {
      k = (s.params.velocity_chunk + osize - 1) / osize * osize;
    } else {
      const gpu::MemoryInfo mem = gpu::memory_info(0);
      const double avail = static_cast<double>(mem.free) - static_cast<double>(kMarginBytes);
      k = static_cast<long long>(avail / per_velocity);
      k = k / kTile * kTile;
      GSIS_CHECK(k >= kTile || k >= nv_,
                 std::format("作業領域に使える GPU のメモリが足りません(空き {:.3g} GB)", mem.free / 1e9));
    }
    chunk_ = static_cast<int>(std::min<long long>(k, nv_));
    const std::size_t work = static_cast<std::size_t>(n_cells_) * static_cast<std::size_t>(chunk_);
    gx_.allocate(work);
    gy_.allocate(work);
    gz_.allocate(work);
    r_.allocate(work);
    partial_stride_ = (chunk_ + kTile - 1) / kTile;
    partial_.allocate(static_cast<std::size_t>(n_cells_) * static_cast<std::size_t>(partial_stride_));
    w_half_.allocate(static_cast<std::size_t>(n_cells_) * 5);
    w_new_.allocate(static_cast<std::size_t>(n_cells_) * 5);
    flags_.allocate(static_cast<std::size_t>(n_cells_));
    GSIS_CUDA_CHECK(cudaDeviceSynchronize());
    log_debug("運動論ソルバー(GPU): 分布関数 {:.3g} GB、速度チャンク {} 点、作業領域 {:.3g} GB", f_bytes / 1e9, chunk_,
              4.0 * sizeof(F) * static_cast<double>(work) / 1e9);
  }

  const char* name() const override { return "cuda"; }
  int chunk_width() const override { return chunk_; }
  std::size_t memory_bytes() const override {
    return xi_x_.bytes() * 3 + csr_offsets_.bytes() + csr_.bytes() + eps2_.bytes() + bfaces_.bytes() +
           binflow_.bytes() + cells_.bytes() + volume_.bytes() + vdt_.bytes() + block_offsets_.bytes() +
           moments_.bytes() + shift_.bytes() + residual_cell_.bytes() + bsums_.bytes() + f_.bytes() + gx_.bytes() * 4 +
           partial_.bytes() + w_half_.bytes() * 2 + flags_.bytes() + sweep_order_.bytes() + sweep_pos_.bytes() +
           sweep_dir_of_.bytes();
  }

  void set_equilibrium(std::span<const double> states) override {
    GSIS_CHECK(states.size() == static_cast<std::size_t>(n_cells_) * 5, "初期状態の長さがセル数と合いません");
    DeviceBuffer<double> d_states{states};
    timer_.begin(PhaseTimer::kOther);
    const KineticView<F> v = view();
    for (int c0 = 0; c0 < n_cells_; c0 += launch_cells_) {
      cuda::launch_set_equilibrium(v, d_states.data(), shift_.data(), c0, std::min(launch_cells_, n_cells_ - c0));
    }
    timer_.end();
    timer_.flush(timings_);
  }

  void set_distribution(std::span<const double> f) override {
    GSIS_CHECK(f.size() == f_.size(), "分布関数の長さが合いません");
    std::vector<F> tmp(f.size());
    for (std::size_t i = 0; i < f.size(); ++i) tmp[i] = static_cast<F>(f[i]);
    f_.upload(std::span<const F>(tmp));
  }

  void get_distribution(std::span<double> f) override {
    GSIS_CHECK(f.size() == f_.size(), "分布関数の長さが合いません");
    std::vector<F> tmp(f.size());
    f_.download(std::span<F>(tmp));
    for (std::size_t i = 0; i < f.size(); ++i) f[i] = static_cast<double>(tmp[i]);
  }

  void compute_moments(std::span<double> out) override {
    GSIS_CHECK(out.size() == moments_.size(), "モーメントの出力の長さが合いません");
    timer_.begin(PhaseTimer::kMoments);
    const KineticView<F> v = view();
    for (int c0 = 0; c0 < n_cells_; c0 += launch_cells_) {
      cuda::launch_moments(v, shift_.data(), moments_.data(), c0, std::min(launch_cells_, n_cells_ - c0));
    }
    timer_.end();
    moments_.download(out);
    timer_.flush(timings_);
  }

  void scale_distribution(double s) override {
    timer_.begin(PhaseTimer::kOther);
    cuda::launch_scale(f_.data(), static_cast<long long>(f_.size()), static_cast<F>(s));
    cuda::launch_scale_moments(moments_.data(), n_cells_, s);
    timer_.end();
    timer_.flush(timings_);
  }

  void update_cell_params() override {
    timer_.begin(PhaseTimer::kOther);
    if (s_.discrete_eq) {
      const KineticView<F> v = view();
      for (int c0 = 0; c0 < n_cells_; c0 += launch_cells_) {
        cuda::launch_cell_params_deq(v, moments_.data(), volume_.data(), vdt_.data(), s_.mu0, s_.omega, s_.Pr,
                                     cells_.data(), flags_.data(), c0, std::min(launch_cells_, n_cells_ - c0));
      }
      deq_valid_ = true;
    } else {
      cuda::launch_cell_params(moments_.data(), volume_.data(), vdt_.data(), s_.mu0, s_.omega, s_.Pr, cells_.data(),
                               n_cells_, s_.params.cold_bgk_temperature);
      deq_valid_ = false;
    }
    timer_.end();
    timer_.flush(timings_);
  }

  DeqCounts deq_counts() override {
    DeqCounts out;
    if (!deq_valid_) return out;
    std::vector<int> flags(static_cast<std::size_t>(n_cells_));
    flags_.download(std::span<int>(flags));
    for (const int x : flags) {
      if (x > 0) ++out.corrected;
      if (x < 0) ++out.failed;
      out.max_iterations = std::max(out.max_iterations, x);
    }
    return out;
  }

  void set_inflow(std::span<const InflowState> inflow) override {
    GSIS_CHECK(inflow.size() == static_cast<std::size_t>(n_bfaces_), "流入分布の長さが境界面の数と合いません");
    if (n_bfaces_ == 0) return;
    std::vector<BoundaryInflow<F>> h(inflow.size());
    for (std::size_t b = 0; b < inflow.size(); ++b) {
      h[b].m = maxwell_params_from<F>(inflow[b].rho, inflow[b].u.x, inflow[b].u.y, inflow[b].u.z, inflow[b].T);
      h[b].scale = static_cast<F>(inflow[b].scale);
    }
    binflow_.upload(std::span<const BoundaryInflow<F>>(h));
  }

  void boundary_sums(std::span<double> out) override {
    GSIS_CHECK(out.size() == static_cast<std::size_t>(n_bfaces_) * kBoundarySumsPerFace,
               "境界の集計の出力の長さが合いません");
    if (n_bfaces_ == 0) return;
    timer_.begin(PhaseTimer::kBoundary);
    const KineticView<F> v = view();
    for (int b0 = 0; b0 < n_bfaces_; b0 += kBoundaryFacesPerLaunch) {
      cuda::launch_boundary_sums(v, bsums_.data(), b0, std::min(kBoundaryFacesPerLaunch, n_bfaces_ - b0));
    }
    timer_.end();
    GSIS_CUDA_CHECK(cudaMemcpy(out.data(), bsums_.data(), out.size() * sizeof(double), cudaMemcpyDeviceToHost));
    timer_.flush(timings_);
  }

  void internal_face_mass_flux(std::span<const std::int32_t> cells, std::span<const std::int32_t> entries,
                               std::span<double> out) override {
    GSIS_CHECK(cells.size() == entries.size() && out.size() == cells.size(), "内部面の流束の引数の長さが合いません");
    const int n = static_cast<int>(cells.size());
    if (n == 0) return;
    for (int i = 0; i < n; ++i) {
      const int c = cells[static_cast<std::size_t>(i)];
      const int e = entries[static_cast<std::size_t>(i)];
      GSIS_CHECK(c >= 0 && c < n_cells_ && e >= 0 && e < static_cast<int>(csr_.size()),
                 "内部面の流束のセル・面の番号が範囲外です");
    }
    // 面の一覧は小さい(管の断面 5 つで 1 万面程度)ので、呼ぶたびに転送する
    DeviceBuffer<std::int32_t> d_cells{cells};
    DeviceBuffer<std::int32_t> d_entries{entries};
    DeviceBuffer<double> d_out;
    d_out.allocate(static_cast<std::size_t>(n));
    timer_.begin(PhaseTimer::kOther);
    const KineticView<F> v = view();
    for (int i0 = 0; i0 < n; i0 += kBoundaryFacesPerLaunch) {
      cuda::launch_internal_face_mass_flux(v, d_cells.data(), d_entries.data(), d_out.data(), i0,
                                           std::min(kBoundaryFacesPerLaunch, n - i0));
    }
    timer_.end();
    d_out.download(out);
    timer_.flush(timings_);
  }

  void sweep(std::span<double> residual_cell) override {
    GSIS_CHECK(residual_cell.size() == static_cast<std::size_t>(n_cells_), "残差の出力の長さが合いません");
    GSIS_CUDA_CHECK(cudaMemset(residual_cell_.data(), 0, residual_cell_.bytes()));
    const KineticView<F> v = view();
    for (int k0 = 0; k0 < nv_; k0 += chunk_) {
      ChunkView<F> ch;
      ch.k0 = k0;
      ch.kc = std::min(chunk_, nv_ - k0);
      ch.stride = chunk_;
      ch.gx = gx_.data();
      ch.gy = gy_.data();
      ch.gz = gz_.data();
      ch.r = r_.data();
      timer_.begin(PhaseTimer::kGradient);
      for (int c0 = 0; c0 < n_cells_; c0 += launch_cells_) {
        cuda::launch_gradient(v, ch, c0, std::min(launch_cells_, n_cells_ - c0));
      }
      timer_.end();
      timer_.begin(PhaseTimer::kResidual);
      for (int c0 = 0; c0 < n_cells_; c0 += launch_cells_) {
        cuda::launch_residual(v, ch, partial_.data(), partial_stride_, c0, std::min(launch_cells_, n_cells_ - c0));
      }
      cuda::launch_residual_reduce(partial_.data(), partial_stride_, (ch.kc + kTile - 1) / kTile,
                                   residual_cell_.data(), n_cells_);
      timer_.end();
      // ブロック間の結合の反復(block_passes)。勾配は残差の後は使わないので、gx に残差、gy に前の回の Δf を置く
      const int passes = static_cast<int>(s_.params.block_passes);
      const std::size_t work_bytes = sizeof(F) * static_cast<std::size_t>(n_cells_) * static_cast<std::size_t>(chunk_);
      if (passes > 1) GSIS_CUDA_CHECK(cudaMemcpyAsync(gx_.data(), r_.data(), work_bytes, cudaMemcpyDeviceToDevice));
      for (int pass = 0; pass < passes; ++pass) {
        ChunkView<F> cp = ch;
        if (pass > 0) {
          GSIS_CUDA_CHECK(cudaMemcpyAsync(gy_.data(), r_.data(), work_bytes, cudaMemcpyDeviceToDevice));
          cp.r0 = gx_.data();
          cp.dprev = gy_.data();
        }
        cp.update_f = pass + 1 == passes ? 1 : 0;
        timer_.begin(PhaseTimer::kForward);
        for (int s0 = 0; s0 < max_block_cells_; s0 += sweep_cells_) {
          cuda::launch_sweep_forward(v, cp, block_offsets_.data(), n_blocks_, s0,
                                     static_cast<int>(std::min<long long>(static_cast<long long>(s0) + sweep_cells_,
                                                                          max_block_cells_)));
        }
        timer_.end();
        timer_.begin(PhaseTimer::kBackward);
        for (int s0 = 0; s0 < max_block_cells_; s0 += sweep_cells_) {
          cuda::launch_sweep_backward(v, cp, block_offsets_.data(), n_blocks_, s0,
                                      static_cast<int>(std::min<long long>(static_cast<long long>(s0) + sweep_cells_,
                                                                           max_block_cells_)));
        }
        timer_.end();
      }
    }
    residual_cell_.download(residual_cell);
    timer_.flush(timings_);
  }

  long long gsis_correction(std::span<const double> w_half, std::span<const double> w_new,
                            std::span<int> replaced_flags) override {
    GSIS_CHECK(w_half.size() == static_cast<std::size_t>(n_cells_) * 5 &&
                   w_new.size() == static_cast<std::size_t>(n_cells_) * 5,
               "GSIS の補正のマクロ量の長さがセル数と合いません");
    w_half_.upload(w_half);
    w_new_.upload(w_new);
    timer_.begin(PhaseTimer::kOther);
    const KineticView<F> v = view();
    for (int c0 = 0; c0 < n_cells_; c0 += launch_cells_) {
      const std::string& rule = s_.params.gsis_negative_check;
      cuda::launch_gsis_correction(v, w_half_.data(), w_new_.data(), flags_.data(), shift_.data(), c0,
                                   std::min(launch_cells_, n_cells_ - c0),
                                   rule == "created" ? kNegativeCreated
                                   : rule == "limit" ? kNegativeLimit
                                   : rule == "blend" ? kNegativeBlend
                                                     : kNegativeAll);
    }
    timer_.end();
    std::vector<int> flags(static_cast<std::size_t>(n_cells_));
    flags_.download(std::span<int>(flags));
    timer_.flush(timings_);
    long long replaced = 0;
    for (const int x : flags) replaced += x != 0 ? 1 : 0;
    if (!replaced_flags.empty()) std::copy(flags.begin(), flags.end(), replaced_flags.begin());
    deq_valid_ = false;  // flags_ を補正の印に使ったので、離散平衡の記録は次の update_cell_params まで無効
    return replaced;
  }

  StepTimings take_timings() override {
    timer_.flush(timings_);
    const StepTimings t = timings_;
    timings_ = StepTimings{};
    return t;
  }

 private:
  // 画面表示・CUDA のコンテキストなどのための余裕。WDDM では cudaMemGetInfo の空きが実際より少なく出ることがあり、
  // 600 MB では確保の後に空きがほぼ 0 と報告された(管、2026-10-16)ので 1 GB にした
  static constexpr std::size_t kMarginBytes = std::size_t{1024} << 20;

  static std::vector<F> to_f(const std::vector<double>& v) {
    std::vector<F> out(v.size());
    for (std::size_t i = 0; i < v.size(); ++i) out[i] = static_cast<F>(v[i]);
    return out;
  }
  template <class T>
  static void upload(DeviceBuffer<T>& buf, const std::vector<T>& host) {
    if (host.empty()) {
      buf.allocate(1);
      return;
    }
    buf.upload(std::span<const T>(host));
  }

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
      v.sweep_order = sweep_order_.data();
      v.sweep_pos = sweep_pos_.data();
      v.sweep_dir_of = sweep_dir_of_.data();
    }
    v.discrete_eq = s_.discrete_eq ? 1 : 0;
    v.deq = s_.deq_grid;
    v.deq_fallback = s_.params.deq_fallback == "collisionless" ? 1 : 0;
    v.cold_bgk_temperature = s_.params.cold_bgk_temperature;
    return v;
  }

  const KineticSetup& s_;
  int nv_ = 0;
  int n_cells_ = 0;
  int n_bfaces_ = 0;
  int n_blocks_ = 0;
  int launch_cells_ = 1;
  int sweep_cells_ = 1;
  int max_block_cells_ = 0;
  int chunk_ = 0;
  int partial_stride_ = 1;
  DeviceBuffer<F> xi_x_, xi_y_, xi_z_;
  DeviceBuffer<std::int32_t> csr_offsets_;
  DeviceBuffer<CsrEntry<F>> csr_;
  DeviceBuffer<F> eps2_;
  DeviceBuffer<BoundaryFaceInfo<F>> bfaces_;
  DeviceBuffer<BoundaryInflow<F>> binflow_;
  DeviceBuffer<CellParams<F>> cells_;
  DeviceBuffer<double> volume_, vdt_;
  DeviceBuffer<std::int32_t> block_offsets_;
  DeviceBuffer<std::int32_t> sweep_order_, sweep_pos_;
  DeviceBuffer<std::uint8_t> sweep_dir_of_;
  DeviceBuffer<double> moments_, shift_, residual_cell_, bsums_;
  DeviceBuffer<F> f_;
  DeviceBuffer<F> gx_, gy_, gz_, r_;
  DeviceBuffer<double> partial_;
  DeviceBuffer<double> w_half_, w_new_;
  DeviceBuffer<int> flags_;
  PhaseTimer timer_;
  StepTimings timings_;
  bool deq_valid_ = false;  // flags_ に直前の update_cell_params の離散平衡の記録が入っている
};

}  // namespace

std::unique_ptr<KineticBackend> make_cuda_backend(const KineticSetup& setup) {
  if (setup.params.precision == "fp64") return std::make_unique<CudaBackend<double>>(setup);
  return std::make_unique<CudaBackend<float>>(setup);
}

}  // namespace gsis::kinetic
