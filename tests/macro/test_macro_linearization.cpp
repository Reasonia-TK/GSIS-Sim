// macro: 組み立てた線形系(J^pu、J_T)を、残差全体の差分近似と比べる。
// ヤコビアンは論文どおり近似(1 次の面フラックス、W_b = W_i、RC の平均勾配・非直交部は微分しない)なので
// 一致はしないが、主要な項の符号・大きさの誤りを見つけるための検査。

#include <algorithm>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstdio>
#include <random>
#include <vector>

#include "macro/linear_solver.hpp"
#include "macro/pbc_solver.hpp"
#include "mesh/geometry.hpp"
#include "support/box_mesh.hpp"

using namespace gsis;
using namespace gsis::macro;

namespace {

struct BoxProblem {
  PolyMesh mesh;
  MeshGeometry geometry;
};

BoxProblem make_box(std::array<int, 3> n, double distortion, bool tets) {
  test::BoxMeshOptions o = test::tube_like_box(n, Vec3{0.0, -1.0, -1.0}, Vec3{4.0, 1.0, 1.0});
  o.distortion = distortion;
  o.tetrahedra = tets;
  BoxProblem p;
  p.mesh = test::make_box_mesh(o);
  p.geometry = compute_geometry(p.mesh);
  return p;
}

std::vector<MacroBoundaryCondition> tube_bcs(const PolyMesh& mesh) {
  std::vector<MacroBoundaryCondition> bcs;
  for (const Patch& p : mesh.patches) {
    MacroBoundaryCondition b;
    if (p.name == "inlet") {
      b.kind = MacroBoundaryKind::PressureInlet;
      b.pressure = 1.0;
      b.temperature = 1.0;
    } else if (p.name == "outlet") {
      b.kind = MacroBoundaryKind::Vacuum;
    } else {
      b.kind = MacroBoundaryKind::DiffuseWall;
      b.temperature = 1.0;
    }
    bcs.push_back(b);
  }
  return bcs;
}

CellMacroState smooth_state(const PolyMesh& mesh, const MeshGeometry& g) {
  CellMacroState s;
  s.resize(static_cast<std::size_t>(mesh.n_cells));
  for (std::size_t c = 0; c < s.size(); ++c) {
    const Vec3& x = g.cell_center[c];
    s.p[c] = 1.0 - 0.15 * x.x + 0.03 * std::sin(2.0 * x.y);
    s.T[c] = 1.0 + 0.05 * std::cos(x.x) * std::cos(x.z);
    s.ux[c] = 0.4 * (1.0 - 0.5 * x.y * x.y) * (1.0 - 0.5 * x.z * x.z) + 0.1 * x.x;
    s.uy[c] = 0.05 * std::sin(x.x);
    s.uz[c] = -0.04 * std::cos(x.x + x.y);
    s.rho[c] = s.p[c] / s.T[c];
  }
  return s;
}

}  // namespace

TEST_CASE("assembled pressure-velocity matrix approximates the residual linearization",
          "[macro][linearization]") {
  for (const double mu : {0.05, 7e-4}) {
    BoxProblem bp = make_box({8, 4, 4}, 0.0, false);
    MacroGas gas;
    gas.mu_ref = mu;
    gas.omega = 0.66;
    MacroParams params;
    params.standalone_face_state = "cell";
    PbcSolver solver(bp.mesh, bp.geometry, 1.0, gas, tube_bcs(bp.mesh), params);
    const CellMacroState W = smooth_state(bp.mesh, bp.geometry);
    solver.evaluate_residual(W);
    BlockCsrMatrix A;
    std::vector<double> rhs;
    solver.linearize_pu(W, 1e30, A, rhs);
    const std::size_t nc = W.size();

    std::mt19937 rng(5);
    std::uniform_real_distribution<double> U(-1.0, 1.0);
    // 摂動の種類: 0 = p だけ、1 = u だけ
    for (int kind = 0; kind < 2; ++kind) {
      std::vector<double> dx(4 * nc, 0.0);
      for (std::size_t c = 0; c < nc; ++c) {
        if (kind == 0) dx[4 * c] = U(rng);
        if (kind == 1) {
          for (int a = 1; a < 4; ++a) dx[4 * c + static_cast<std::size_t>(a)] = U(rng);
        }
      }
      std::vector<double> Jdx(4 * nc);
      block_csr_multiply(A, dx, Jdx);
      const double eps = 1e-6;
      CellMacroState Wp = W;
      CellMacroState Wm = W;
      for (std::size_t c = 0; c < nc; ++c) {
        Wp.p[c] += eps * dx[4 * c];
        Wm.p[c] -= eps * dx[4 * c];
        Wp.ux[c] += eps * dx[4 * c + 1];
        Wm.ux[c] -= eps * dx[4 * c + 1];
        Wp.uy[c] += eps * dx[4 * c + 2];
        Wm.uy[c] -= eps * dx[4 * c + 2];
        Wp.uz[c] += eps * dx[4 * c + 3];
        Wm.uz[c] -= eps * dx[4 * c + 3];
      }
      const auto Rp = solver.residual_frozen(Wp);
      const auto Rm = solver.residual_frozen(Wm);
      double num_mass = 0.0;
      double den_mass = 0.0;
      double num_mom = 0.0;
      double den_mom = 0.0;
      for (std::size_t c = 0; c < nc; ++c) {
        const double fm = (Rp[c][0] - Rm[c][0]) / (2.0 * eps);
        num_mass += (fm - Jdx[4 * c]) * (fm - Jdx[4 * c]);
        den_mass += fm * fm;
        for (std::size_t a = 1; a < 4; ++a) {
          const double f = (Rp[c][a] - Rm[c][a]) / (2.0 * eps);
          num_mom += (f - Jdx[4 * c + a]) * (f - Jdx[4 * c + a]);
          den_mom += f * f;
        }
      }
      const double rel_mass = std::sqrt(num_mass / den_mass);
      const double rel_mom = std::sqrt(num_mom / den_mom);
      std::printf("mu = %.3g, 摂動 %s: 連続式の相対差 %.3e、運動量の相対差 %.3e\n", mu, kind == 0 ? "p" : "u",
                  rel_mass, rel_mom);
      // 近似ヤコビアンなので一致はしない。主要な項が正しければ相対差は 1 より十分小さい
      CHECK(rel_mass < 0.5);
      CHECK(rel_mom < 0.5);
    }
  }
}
