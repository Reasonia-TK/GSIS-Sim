#include "macro/linear_solver.hpp"

#include <algorithm>
#include <exception>
#include <format>
#include <limits>
#include <tuple>
#include <type_traits>

#ifdef _MSC_VER
// C4701(未初期化の可能性)は AMGCL の spectral_radius で出る誤検出。コード生成時の警告なので
// /external:W0 では消えず、この翻訳単位全体で抑える。
#pragma warning(disable : 4701)
#pragma warning(push, 0)
#endif
#include <amgcl/adapter/crs_tuple.hpp>
#include <amgcl/amg.hpp>
#include <amgcl/backend/builtin.hpp>
#include <amgcl/coarsening/smoothed_aggregation.hpp>
#include <amgcl/make_solver.hpp>
#include <amgcl/preconditioner/cpr.hpp>
#include <amgcl/relaxation/spai0.hpp>
#include <amgcl/relaxation/as_preconditioner.hpp>
#include <amgcl/relaxation/ilu0.hpp>
#include <amgcl/solver/bicgstab.hpp>
#include <amgcl/solver/gmres.hpp>
#include <amgcl/value_type/static_matrix.hpp>
#ifdef _MSC_VER
#pragma warning(pop)
#endif

#include "core/error.hpp"
#include "core/timer.hpp"

#ifdef _OPENMP
#include <omp.h>
#endif

namespace gsis::macro {

namespace {

// 総和の区間の長さ(スレッド数によらず同じ区間に分ける)
constexpr std::ptrdiff_t kSumBlock = 2048;

// スレッド数によらない内積(AMGCL の反復法の InnerProduct として使う)
struct DeterministicInnerProduct {
  template <class Vec1, class Vec2>
  typename amgcl::math::inner_product_impl<typename amgcl::backend::value_type<Vec1>::type>::return_type
  operator()(const Vec1& x, const Vec2& y) const {
    using R = typename amgcl::math::inner_product_impl<
        typename amgcl::backend::value_type<Vec1>::type>::return_type;
    const auto n = static_cast<std::ptrdiff_t>(x.size());
    const std::ptrdiff_t nb = (n + kSumBlock - 1) / kSumBlock;
    std::vector<R> part(static_cast<std::size_t>(std::max<std::ptrdiff_t>(nb, 1)), amgcl::math::zero<R>());
#pragma omp parallel for schedule(static)
    for (std::ptrdiff_t b = 0; b < nb; ++b) {
      const std::ptrdiff_t lo = b * kSumBlock;
      const std::ptrdiff_t hi = std::min(n, lo + kSumBlock);
      R s = amgcl::math::zero<R>();
      for (std::ptrdiff_t i = lo; i < hi; ++i) s += amgcl::math::inner_product(x[i], y[i]);
      part[static_cast<std::size_t>(b)] = s;
    }
    R s = amgcl::math::zero<R>();
    for (const R& v : part) s += v;
    return s;
  }
  int rank() const { return 0; }
};

template <class Backend>
using IluPrecond = amgcl::relaxation::as_preconditioner<Backend, amgcl::relaxation::ilu0>;

template <class Backend>
using AmgPrecond = amgcl::amg<Backend, amgcl::coarsening::smoothed_aggregation, amgcl::relaxation::ilu0>;

// CPR(圧力の部分系に AMG(平滑化に ILU0)、全体に ILU0)。ブロックの最初の未知数を圧力とみなす(4×4 ブロック専用)
template <class Backend>
using CprPrecond = amgcl::preconditioner::cpr<
    amgcl::amg<amgcl::backend::builtin<double>, amgcl::coarsening::smoothed_aggregation, amgcl::relaxation::ilu0>,
    amgcl::relaxation::as_preconditioner<Backend, amgcl::relaxation::ilu0>>;

template <class Backend>
using BicgstabSolver = amgcl::solver::bicgstab<Backend, DeterministicInnerProduct>;

template <class Backend>
using GmresSolver = amgcl::solver::gmres<Backend, DeterministicInnerProduct>;

template <class Solver, class Matrix, class RhsRange, class XRange>
LinearSolveResult run_solver(const Matrix& A, const RhsRange& rhs, XRange& x, const LinearSolverOptions& o,
                             bool gmres) {
  typename Solver::params prm;
  prm.solver.tol = o.rtol;
  prm.solver.maxiter = static_cast<decltype(prm.solver.maxiter)>(o.max_iterations);
  if constexpr (requires { prm.solver.M; }) {
    if (gmres) prm.solver.M = static_cast<decltype(prm.solver.M)>(o.gmres_restart);
  }
  // ILU0 の三角解法: AMGCL は 4 スレッド未満で逐次版、それ以上でレベル分割の並列版を使い、
  // 両者は行の中の足し方が違う(丸めが変わる)。スレッド数によらず同じ結果にするため、常に並列版にする
  // (並列版の行ごとの計算順序はスレッド数によらない)。
  if constexpr (requires { prm.precond.solve.serial; }) prm.precond.solve.serial = false;
  if constexpr (requires { prm.precond.relax.solve.serial; }) prm.precond.relax.solve.serial = false;
  if constexpr (requires { prm.precond.sprecond.solve.serial; }) prm.precond.sprecond.solve.serial = false;
  if constexpr (requires { prm.precond.pprecond.relax.solve.serial; }) {
    prm.precond.pprecond.relax.solve.serial = false;
  }
  LinearSolveResult r;
  Timer t_setup;
  try {
    Solver solve(A, prm);
    r.setup_seconds = t_setup.seconds();
    Timer t_solve;
    std::size_t iters = 0;
    double error = 0.0;
    std::tie(iters, error) = solve(rhs, x);
    r.solve_seconds = t_solve.seconds();
    r.iterations = static_cast<long long>(iters);
    r.relative_residual = error;
  } catch (const std::exception& e) {
    // AMGCL の破綻(例: BiCGStab の "Zero rho"、ILU0 の 0 のピボット)は、収束しなかった解として返す
    r.relative_residual = std::numeric_limits<double>::infinity();
    r.iterations = static_cast<long long>(o.max_iterations);
    r.breakdown = e.what();
  }
  r.converged = r.relative_residual <= o.rtol;
  return r;
}

template <class Backend, class Matrix, class RhsRange, class XRange>
LinearSolveResult dispatch(const Matrix& A, const RhsRange& rhs, XRange& x, const LinearSolverOptions& o) {
  const bool gmres = o.solver == "gmres";
  if constexpr (!std::is_same_v<typename Backend::value_type, double>) {
    if (o.preconditioner == "cpr") {
      if (gmres) return run_solver<amgcl::make_solver<CprPrecond<Backend>, GmresSolver<Backend>>>(A, rhs, x, o, true);
      return run_solver<amgcl::make_solver<CprPrecond<Backend>, BicgstabSolver<Backend>>>(A, rhs, x, o, false);
    }
  }
  const bool amg = o.preconditioner == "amg";
  if (!gmres && !amg) {
    return run_solver<amgcl::make_solver<IluPrecond<Backend>, BicgstabSolver<Backend>>>(A, rhs, x, o, false);
  }
  if (gmres && !amg) {
    return run_solver<amgcl::make_solver<IluPrecond<Backend>, GmresSolver<Backend>>>(A, rhs, x, o, true);
  }
  if (!gmres && amg) {
    return run_solver<amgcl::make_solver<AmgPrecond<Backend>, BicgstabSolver<Backend>>>(A, rhs, x, o, false);
  }
  return run_solver<amgcl::make_solver<AmgPrecond<Backend>, GmresSolver<Backend>>>(A, rhs, x, o, true);
}

}  // namespace

LinearSolveResult solve_linear_system(const BlockCsrMatrix& A, std::span<const double> b, std::span<double> x,
                                      const LinearSolverOptions& options) {
  GSIS_CHECK(options.solver == "bicgstab" || options.solver == "gmres",
             std::format("線形ソルバー \"{}\" は使えません(bicgstab か gmres)", options.solver));
  GSIS_CHECK(options.preconditioner == "ilu0" || options.preconditioner == "amg" || options.preconditioner == "cpr",
             std::format("前処理 \"{}\" は使えません(ilu0・amg・cpr)", options.preconditioner));
  const std::ptrdiff_t n = A.n_rows();
  const int bs = A.block_size;
  GSIS_ASSERT(bs == 1 || bs == 4);
  GSIS_ASSERT(static_cast<std::ptrdiff_t>(b.size()) == n * bs);
  GSIS_ASSERT(static_cast<std::ptrdiff_t>(x.size()) == n * bs);
  GSIS_ASSERT(static_cast<std::ptrdiff_t>(A.val.size()) == A.n_blocks() * bs * bs);
  std::fill(x.begin(), x.end(), 0.0);

  auto ptr = amgcl::make_iterator_range(A.ptr.data(), A.ptr.data() + A.ptr.size());
  auto col = amgcl::make_iterator_range(A.col.data(), A.col.data() + A.col.size());
  if (bs == 4) {
    using M4 = amgcl::static_matrix<double, 4, 4>;
    using V4 = amgcl::static_matrix<double, 4, 1>;
    static_assert(sizeof(M4) == 16 * sizeof(double) && sizeof(V4) == 4 * sizeof(double));
    const auto* vp = reinterpret_cast<const M4*>(A.val.data());
    auto val = amgcl::make_iterator_range(vp, vp + A.n_blocks());
    const auto matrix = std::make_tuple(n, ptr, col, val);
    const auto* bp = reinterpret_cast<const V4*>(b.data());
    auto* xp = reinterpret_cast<V4*>(x.data());
    const auto rhs = amgcl::make_iterator_range(bp, bp + n);
    auto xs = amgcl::make_iterator_range(xp, xp + n);
    return dispatch<amgcl::backend::builtin<M4>>(matrix, rhs, xs, options);
  }
  auto val = amgcl::make_iterator_range(A.val.data(), A.val.data() + A.val.size());
  const auto matrix = std::make_tuple(n, ptr, col, val);
  const auto rhs = amgcl::make_iterator_range(b.data(), b.data() + b.size());
  auto xs = amgcl::make_iterator_range(x.data(), x.data() + x.size());
  LinearSolverOptions scalar = options;
  if (scalar.preconditioner == "cpr") scalar.preconditioner = "ilu0";  // スカラーの系(温度)に CPR はない
  return dispatch<amgcl::backend::builtin<double>>(matrix, rhs, xs, scalar);
}

void block_csr_multiply(const BlockCsrMatrix& A, std::span<const double> x, std::span<double> y) {
  const std::ptrdiff_t n = A.n_rows();
  const int bs = A.block_size;
  GSIS_ASSERT(static_cast<std::ptrdiff_t>(x.size()) == n * bs && static_cast<std::ptrdiff_t>(y.size()) == n * bs);
#pragma omp parallel for schedule(static)
  for (std::ptrdiff_t i = 0; i < n; ++i) {
    for (int r = 0; r < bs; ++r) {
      double s = 0.0;
      for (std::ptrdiff_t k = A.ptr[static_cast<std::size_t>(i)]; k < A.ptr[static_cast<std::size_t>(i) + 1]; ++k) {
        const std::ptrdiff_t j = A.col[static_cast<std::size_t>(k)];
        const double* blk = &A.val[static_cast<std::size_t>(k * bs * bs)];
        for (int c = 0; c < bs; ++c) s += blk[r * bs + c] * x[static_cast<std::size_t>(j * bs + c)];
      }
      y[static_cast<std::size_t>(i * bs + r)] = s;
    }
  }
}

double deterministic_sum(std::span<const double> values) {
  const auto n = static_cast<std::ptrdiff_t>(values.size());
  const std::ptrdiff_t nb = (n + kSumBlock - 1) / kSumBlock;
  std::vector<double> part(static_cast<std::size_t>(std::max<std::ptrdiff_t>(nb, 1)), 0.0);
#pragma omp parallel for schedule(static)
  for (std::ptrdiff_t b = 0; b < nb; ++b) {
    const std::ptrdiff_t lo = b * kSumBlock;
    const std::ptrdiff_t hi = std::min(n, lo + kSumBlock);
    double s = 0.0;
    for (std::ptrdiff_t i = lo; i < hi; ++i) s += values[static_cast<std::size_t>(i)];
    part[static_cast<std::size_t>(b)] = s;
  }
  double s = 0.0;
  for (double v : part) s += v;
  return s;
}

int openmp_max_threads() {
#ifdef _OPENMP
  return omp_get_max_threads();
#else
  return 1;
#endif
}

void set_openmp_threads(int n) {
#ifdef _OPENMP
  if (n > 0) omp_set_num_threads(n);
#else
  (void)n;
#endif
}

}  // namespace gsis::macro
