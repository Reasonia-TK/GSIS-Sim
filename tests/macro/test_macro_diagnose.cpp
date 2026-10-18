// macro: 診断用(既定では実行しない。タグ [.diagnose])。
// 環境変数 GSIS_DIAG_CASE(ケース TOML)と GSIS_DIAG_STATE(pbc-run --cells-csv の cells.csv)で与えた状態の
// 圧力–速度の行列を調べ、対角ブロックの条件や符号のおかしいセルを表示する。

#include <algorithm>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "config/case_config.hpp"
#include "core/paths.hpp"
#include "macro/linear_solver.hpp"
#include "macro/pbc_solver.hpp"
#include "mesh/pmesh_io.hpp"

using namespace gsis;
using namespace gsis::macro;

namespace {

// getenv の代わり(MSVC の C4996 を避ける)
const char* env(const char* name) {
#ifdef _MSC_VER
  static thread_local std::string value;
  char* buf = nullptr;
  std::size_t len = 0;
  if (_dupenv_s(&buf, &len, name) != 0 || buf == nullptr) return nullptr;
  value = buf;
  std::free(buf);
  return value.c_str();
#else
  return std::getenv(name);
#endif
}

CellMacroState read_state(const std::string& path, std::size_t n) {
  CellMacroState s;
  s.resize(n);
  const std::string text = read_file(path_from_utf8(path));
  std::size_t pos = text.find('\n');
  std::size_t c = 0;
  while (pos + 1 < text.size() && c < n) {
    const std::size_t end = std::min(text.find('\n', pos + 1), text.size());
    const std::string line = text.substr(pos + 1, end - pos - 1);
    pos = end;
    if (line.empty()) continue;
    double v[11];
    std::size_t start = 0;
    for (int k = 0; k < 11; ++k) {
      const std::size_t comma = k < 10 ? line.find(',', start) : line.size();
      v[k] = std::stod(line.substr(start, comma - start));
      start = comma + 1;
    }
    s.p[c] = v[5];
    s.ux[c] = v[6];
    s.uy[c] = v[7];
    s.uz[c] = v[8];
    s.T[c] = v[9];
    s.rho[c] = v[5] / v[9];
    ++c;
  }
  return s;
}

// 4×4 の行列式
double det4(const double* a) {
  double m[4][4];
  for (int i = 0; i < 16; ++i) m[i / 4][i % 4] = a[i];
  double det = 1.0;
  for (int c = 0; c < 4; ++c) {
    int piv = c;
    for (int r = c + 1; r < 4; ++r) {
      if (std::abs(m[r][c]) > std::abs(m[piv][c])) piv = r;
    }
    if (m[piv][c] == 0.0) return 0.0;
    if (piv != c) {
      for (int k = 0; k < 4; ++k) std::swap(m[c][k], m[piv][k]);
      det = -det;
    }
    det *= m[c][c];
    for (int r = c + 1; r < 4; ++r) {
      const double f = m[r][c] / m[c][c];
      for (int k = c; k < 4; ++k) m[r][k] -= f * m[c][k];
    }
  }
  return det;
}

}  // namespace

TEST_CASE("diagnose the pressure-velocity matrix of a given state", "[.diagnose]") {
  const std::string case_file_s = env("GSIS_DIAG_CASE") ? env("GSIS_DIAG_CASE") : "";
  const std::string state_file_s = env("GSIS_DIAG_STATE") ? env("GSIS_DIAG_STATE") : "";
  const char* case_file = case_file_s.empty() ? nullptr : case_file_s.c_str();
  const char* state_file = state_file_s.empty() ? nullptr : state_file_s.c_str();
  if (case_file == nullptr || state_file == nullptr) SKIP("GSIS_DIAG_CASE と GSIS_DIAG_STATE を与えてください");
  const CaseConfig cfg = load_case(path_from_utf8(case_file));
  const DerivedQuantities d = derive_quantities(cfg);
  const MacroParams params = parse_macro_params(cfg);
  const LoadedMesh lm = load_pmesh(cfg.mesh_path());
  PbcSolver solver = PbcSolver::from_case(cfg, d, lm.mesh, lm.geometry, params);
  const std::size_t nc = static_cast<std::size_t>(lm.mesh.n_cells);
  const CellMacroState W = read_state(state_file, nc);
  solver.evaluate_residual(W);
  BlockCsrMatrix A;
  std::vector<double> rhs;
  const double cfl = env("GSIS_DIAG_CFL") ? std::atof(env("GSIS_DIAG_CFL")) : 20.0;
  solver.linearize_pu(W, cfl, A, rhs);
  const MeshGeometry& g = solver.geometry();

  struct Bad {
    std::size_t c;
    double score;
  };
  std::vector<Bad> bad;
  int neg_cont = 0;
  int neg_mom = 0;
  for (std::size_t c = 0; c < nc; ++c) {
    // 対角ブロック
    const double* D = nullptr;
    double offsum[4] = {0, 0, 0, 0};
    for (std::ptrdiff_t k = A.ptr[c]; k < A.ptr[c + 1]; ++k) {
      const double* blk = &A.val[static_cast<std::size_t>(k) * 16];
      if (static_cast<std::size_t>(A.col[static_cast<std::size_t>(k)]) == c) {
        D = blk;
      } else {
        for (int r = 0; r < 4; ++r) {
          for (int s = 0; s < 4; ++s) offsum[r] += std::abs(blk[4 * r + s]);
        }
      }
    }
    REQUIRE(D != nullptr);
    if (D[0] <= 0.0) ++neg_cont;
    if (D[5] <= 0.0 || D[10] <= 0.0 || D[15] <= 0.0) ++neg_mom;
    double dsum[4] = {0, 0, 0, 0};
    for (int r = 0; r < 4; ++r) {
      for (int s = 0; s < 4; ++s) dsum[r] += std::abs(D[4 * r + s]);
    }
    const double det = det4(D);
    const double scale = dsum[0] * dsum[1] * dsum[2] * dsum[3];
    const double rel = scale > 0.0 ? std::abs(det) / scale : 0.0;
    bad.push_back({c, rel});
  }
  std::sort(bad.begin(), bad.end(), [](const Bad& a, const Bad& b) { return a.score < b.score; });
  std::printf("セル %zu、連続式の対角 <= 0: %d、運動量の対角 <= 0: %d\n", nc, neg_cont, neg_mom);
  for (int i = 0; i < 12; ++i) {
    const std::size_t c = bad[static_cast<std::size_t>(i)].c;
    const Vec3& x = g.cell_center[c];
    const double* D = nullptr;
    for (std::ptrdiff_t k = A.ptr[c]; k < A.ptr[c + 1]; ++k) {
      if (static_cast<std::size_t>(A.col[static_cast<std::size_t>(k)]) == c) D = &A.val[static_cast<std::size_t>(k) * 16];
    }
    std::printf("cell %zu x=%.2f r=%.2f p=%.3e T=%.3e u=(%.3f,%.3f,%.3f) |det|/Πrowsum=%.2e diag=(%.2e %.2e %.2e %.2e)\n", c,
                x.x, std::hypot(x.y, x.z), W.p[c], W.T[c], W.ux[c], W.uy[c], W.uz[c], bad[static_cast<std::size_t>(i)].score,
                D[0], D[5], D[10], D[15]);
  }
}
