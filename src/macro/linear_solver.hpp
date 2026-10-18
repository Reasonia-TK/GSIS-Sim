#pragma once
// PBC の線形系(4×4 ブロックの圧力–速度系、スカラーの温度系)を AMGCL で解く(docs/design_pbc.md §3・§4)。
//
// - AMGCL の builtin バックエンド(OpenMP)。値の型は static_matrix<double, 4, 4>(圧力–速度)か double(温度)
// - 反復法: BiCGStab か GMRES(再始動付き)。前処理: ILU0 か AMG(平滑化集約、平滑化に ILU0)
// - 内積は、スレッド数によらず同じ順序で足す自前の関数を使う(同じ入力なら同じ結果、反復回数も同じ)
// - AMGCL のテンプレートはこのファイルの .cpp の中だけで実体化する(コンパイル時間を抑える)

#include <cstddef>
#include <span>
#include <string>
#include <vector>

namespace gsis::macro {

struct LinearSolverOptions {
  std::string solver = "gmres";         // "bicgstab" | "gmres"
  std::string preconditioner = "ilu0";  // "ilu0" | "amg" | "cpr"(4×4 ブロックのみ。スカラーの系は ilu0)
  double rtol = 1e-3;                   // 相対許容誤差 ‖b − A x‖/‖b‖
  long long max_iterations = 200;
  int gmres_restart = 30;
};

struct LinearSolveResult {
  long long iterations = 0;
  double relative_residual = 0.0;  // AMGCL が返す ‖b − A x‖/‖b‖(右前処理なので真の残差)
  double setup_seconds = 0.0;
  double solve_seconds = 0.0;
  bool converged = false;          // relative_residual <= rtol
  std::string breakdown;           // AMGCL が例外で止まったときの文(このとき relative_residual = ∞)
};

// ブロック CSR。行 i のブロックは [ptr[i], ptr[i+1])、列は col、値は block_size² 個ずつ行優先。
struct BlockCsrMatrix {
  int block_size = 4;
  std::vector<std::ptrdiff_t> ptr;  // 長さ n + 1
  std::vector<std::ptrdiff_t> col;  // ブロックの列(行ごとに昇順)
  std::vector<double> val;          // ブロックの値

  std::ptrdiff_t n_rows() const { return static_cast<std::ptrdiff_t>(ptr.size()) - 1; }
  std::ptrdiff_t n_blocks() const { return static_cast<std::ptrdiff_t>(col.size()); }
};

// A x = b を解く(x の初期値は 0 にする)。block_size は 4 か 1。
// b と x の長さは n_rows × block_size。
LinearSolveResult solve_linear_system(const BlockCsrMatrix& A, std::span<const double> b, std::span<double> x,
                                      const LinearSolverOptions& options);

// y = A x(ブロック CSR の積。検査・診断用)
void block_csr_multiply(const BlockCsrMatrix& A, std::span<const double> x, std::span<double> y);

// スレッド数によらない決まった順序の総和(長さ n の配列を固定長の区間に分け、区間の和を順に足す)
double deterministic_sum(std::span<const double> values);

// OpenMP のスレッド数(OpenMP なしでビルドしたときは 1。set は 0 以下なら何もしない)
int openmp_max_threads();
void set_openmp_threads(int n);

}  // namespace gsis::macro
