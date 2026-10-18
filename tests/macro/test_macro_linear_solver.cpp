// macro: AMGCL のラッパー(4×4 ブロックとスカラー、全ての反復法 × 前処理)と決定的な総和

#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <numeric>
#include <random>
#include <string>
#include <vector>

#include "macro/linear_solver.hpp"

using namespace gsis::macro;

namespace {

// 3 次元の格子(n³ セル)の 7 点の隣接で、対角優位な乱数のブロック行列を作る
BlockCsrMatrix make_matrix(int n, int bs, unsigned seed) {
  std::mt19937 rng(seed);
  std::uniform_real_distribution<double> U(-1.0, 1.0);
  BlockCsrMatrix A;
  A.block_size = bs;
  const int N = n * n * n;
  auto id = [n](int i, int j, int k) { return i + n * (j + n * k); };
  A.ptr.push_back(0);
  for (int k = 0; k < n; ++k) {
    for (int j = 0; j < n; ++j) {
      for (int i = 0; i < n; ++i) {
        std::vector<int> cols;
        if (k > 0) cols.push_back(id(i, j, k - 1));
        if (j > 0) cols.push_back(id(i, j - 1, k));
        if (i > 0) cols.push_back(id(i - 1, j, k));
        cols.push_back(id(i, j, k));
        if (i < n - 1) cols.push_back(id(i + 1, j, k));
        if (j < n - 1) cols.push_back(id(i, j + 1, k));
        if (k < n - 1) cols.push_back(id(i, j, k + 1));
        for (int c : cols) {
          A.col.push_back(c);
          for (int r = 0; r < bs; ++r) {
            for (int s = 0; s < bs; ++s) {
              double v = 0.3 * U(rng);
              if (c == id(i, j, k) && r == s) v += 8.0 + 2.0 * std::abs(U(rng));  // 対角優位
              if (c != id(i, j, k) && r == s) v -= 1.0;                          // ラプラシアンに似せる
              A.val.push_back(v);
            }
          }
        }
        A.ptr.push_back(static_cast<std::ptrdiff_t>(A.col.size()));
      }
    }
  }
  (void)N;
  return A;
}

double relative_residual(const BlockCsrMatrix& A, const std::vector<double>& b, const std::vector<double>& x) {
  std::vector<double> Ax(b.size());
  block_csr_multiply(A, x, Ax);
  double rr = 0.0;
  double bb = 0.0;
  for (std::size_t i = 0; i < b.size(); ++i) {
    rr += (b[i] - Ax[i]) * (b[i] - Ax[i]);
    bb += b[i] * b[i];
  }
  return std::sqrt(rr / bb);
}

}  // namespace

TEST_CASE("AMGCL wrapper solves block and scalar systems with every solver and preconditioner", "[macro][linear]") {
  for (const int bs : {4, 1}) {
    const BlockCsrMatrix A = make_matrix(8, bs, 42);
    std::mt19937 rng(7);
    std::uniform_real_distribution<double> U(-1.0, 1.0);
    std::vector<double> b(static_cast<std::size_t>(A.n_rows() * bs));
    for (double& v : b) v = U(rng);
    for (const char* solver : {"bicgstab", "gmres"}) {
      for (const char* precond : {"ilu0", "amg", "cpr"}) {
        LinearSolverOptions o;
        o.solver = solver;
        o.preconditioner = precond;
        o.rtol = 1e-10;
        o.max_iterations = 500;
        std::vector<double> x(b.size(), 1.0);
        const LinearSolveResult r = solve_linear_system(A, b, x, o);
        INFO("ブロック " << bs << "、" << solver << " + " << precond);
        CHECK(r.converged);
        CHECK(r.breakdown.empty());
        CHECK(relative_residual(A, b, x) < 1e-9);
      }
    }
  }
}

TEST_CASE("deterministic_sum adds in a fixed order", "[macro][linear]") {
  std::vector<double> v(10000);
  for (std::size_t i = 0; i < v.size(); ++i) v[i] = 1.0 / static_cast<double>(i + 1);
  const double a = deterministic_sum(v);
  const int saved = openmp_max_threads();
  set_openmp_threads(1);
  const double b = deterministic_sum(v);
  set_openmp_threads(saved);
  CHECK(a == b);
  CHECK(std::abs(a - std::accumulate(v.begin(), v.end(), 0.0)) < 1e-12);
}
