#pragma once
// GSIS の試験用のケースと小さな問題の解き方(CPU の運動論ソルバー + PBC)。

#include <array>
#include <string>
#include <vector>

#include "config/case_config.hpp"
#include "gsis/gsis_solver.hpp"
#include "gsis/interfaces.hpp"
#include "kinetic/kinetic_test_cases.hpp"
#include "mesh/pmesh_io.hpp"

namespace gsis::test {

// 1 次元の平板(x 方向に nx セル、y・z は 1 セル厚、長さ 1 m = L_ref)。
// 側面は並進周期(CIS 単独の収束が速い)。壁の温度・速度は基準量に対する比
struct SlabProblem {
  LoadedMesh mesh;
  CaseConfig config;
};
SlabProblem make_slab_problem(int nx, double Kn, double T_left, double T_right, double u_wall,
                              const std::string& kinetic = "backend = \"cpu\"\nprecision = \"fp64\"\n",
                              const std::string& extra = "");

// 管に似た箱(x− が圧力入口、x+ が真空、それ以外が拡散壁)
struct BoxProblem {
  LoadedMesh mesh;
  CaseConfig config;
};
BoxProblem make_tube_box_problem(std::array<int, 3> n, double Kn, const std::string& kinetic,
                                 const std::string& extra);

struct SolveResult {
  KineticMoments moments;     // 最後の分布関数のモーメント
  CellMacroState state;       // GSIS: 最後の PBC 段の出力 W^n、CIS: モーメントの状態
  long long iterations = 0;   // CIS 単独なら CIS 反復、GSIS なら GSIS 反復
  long long cis_iterations = 0;
  bool converged = false;
  double epsilon = 0.0;
};

// CIS 単独で ε < tol まで(一様な静止状態 ρ = T = 1 から)
SolveResult solve_cis(const CaseConfig& cfg, const LoadedMesh& lm, double tol, long long max_iter);
// GSIS で ε < tol まで(初期化は PBC 単独、[macro].init_iterations 回まで)
SolveResult solve_gsis(const CaseConfig& cfg, const LoadedMesh& lm, double tol, long long max_iter);

// セルの値の体積平均
double volume_mean(const std::vector<double>& v, const LoadedMesh& lm);

}  // namespace gsis::test
