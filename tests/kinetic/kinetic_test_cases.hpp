#pragma once
// 運動論ソルバーのテスト用のケース(TOML の文字列を組み立てる)と小さなメッシュ。

#include <array>
#include <string>
#include <vector>

#include "config/case_config.hpp"
#include "core/vec3.hpp"
#include "mesh/pmesh_io.hpp"
#include "support/box_mesh.hpp"

namespace gsis::test {

// 境界条件(温度・圧力・速度は基準量 T0・p_ref・√(R T0) に対する比で与える)
struct KineticBc {
  std::string patch;
  std::string type;  // diffuse_wall / pressure_inlet / pressure_outlet / vacuum / symmetry / periodic_slab
  double T = 1.0;
  double p = 1.0;
  Vec3 u{};
};

struct KineticCaseOptions {
  double Kn = 0.5;                  // 基準状態の Kn(L_ref = 1 m)
  std::array<int, 3> nv{10, 10, 10};
  double vmax = 5.0;                // 速度格子の範囲 [−vmax, vmax]³
  std::string kinetic;              // [kinetic] の中身(例: "backend = \"cpu\"\nsweep_blocks = 1\n")
  std::string extra;                // 末尾に足すそのほかの節(例: "[gsis]\nn_cis = 5\n[macro]\ninit_iterations = 20\n")
};

CaseConfig make_kinetic_case(const std::vector<KineticBc>& bcs, const KineticCaseOptions& options);

// 箱のメッシュ(長さは m。L_ref = 1 m なので無次元の長さと同じ)
LoadedMesh make_kinetic_box(std::array<int, 3> n, Vec3 lo, Vec3 hi, std::array<std::string, 6> patch_names,
                            double distortion = 0.0, bool tetrahedra = false, unsigned seed = 1);

}  // namespace gsis::test
