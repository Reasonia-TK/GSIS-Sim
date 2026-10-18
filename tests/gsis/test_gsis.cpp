// gsis: GSIS の反復(フェーズ 4)の試験。運動論は CPU 参照実装(FP64)、PBC は CPU。
//   - [gsis].max_iterations と GsisOptions::from_case、式 (59) の相対変化
//   - 平板で GSIS と CIS 単独が同じ不動点に収束する(差は空間離散化の違いの分だけで、格子を細かくすると減る)
//   - 小さな Kn で、GSIS の CIS 反復の合計が CIS 単独より大幅に少ない
//   - 補正の後の分布関数のモーメントが W^{n+1} に一致し、負値の置換の後は f が閾値以上
//   - hot_interpolation = "face": 段の初めの状態で面の粘性フラックスが運動論の σ・q の補間に一致する
//   - 決定性(同じ入力で 2 回、OpenMP のスレッド数 1 と 4 で、ビット単位で同じ)

#include <algorithm>
#include <array>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>

#include "core/error.hpp"
#include "core/math.hpp"
#include "gsis/gsis_solver.hpp"
#include "gsis_test_cases.hpp"
#include "kinetic/kinetic_output.hpp"
#include "kinetic/kinetic_params.hpp"
#include "macro/linear_solver.hpp"
#include "macro/macro_params.hpp"
#include "macro/pbc_solver.hpp"
#include "physics/maxwell.hpp"
#include "velocity/velocity_grid.hpp"

using namespace gsis;
using namespace gsis::test;

namespace {

double max_abs_diff(const std::vector<double>& a, const std::vector<double>& b) {
  double m = 0.0;
  for (std::size_t i = 0; i < a.size(); ++i) m = std::max(m, std::abs(a[i] - b[i]));
  return m;
}

bool bitwise_equal(const std::vector<double>& a, const std::vector<double>& b) {
  return a.size() == b.size() && std::memcmp(a.data(), b.data(), a.size() * sizeof(double)) == 0;
}

std::vector<double> component(const std::vector<std::array<double, 3>>& v, std::size_t k) {
  std::vector<double> out(v.size());
  for (std::size_t i = 0; i < v.size(); ++i) out[i] = v[i][k];
  return out;
}

const char* kGsisSection = "[gsis]\nn_cis = 5\nmax_pbc = 10\ncfl_pbc = 500.0\n";

}  // namespace

TEST_CASE("gsis: [gsis].max_iterations and GsisOptions::from_case", "[gsis]") {
  KineticCaseOptions o;
  o.extra = "[gsis]\nn_cis = 3\nmax_pbc = 7\ncfl_pbc = 250.0\ntolerance = 1e-7\nmax_iterations = 42\n"
            "filter_min_cells = 0.5\ndamp_kn = 0.02\n"
            "[macro]\ninit_iterations = 12\n";
  o.kinetic = "convergence_u_floor = 0.05\n";
  const CaseConfig cfg = make_kinetic_case({{"x", "vacuum"}}, o);
  CHECK(cfg.gsis.max_iterations == 42);
  const GsisOptions g = GsisOptions::from_case(cfg, parse_kinetic_params(cfg), parse_macro_params(cfg));
  CHECK(g.n_cis == 3);
  CHECK(g.max_pbc == 7);
  CHECK(g.cfl_pbc == 250.0);
  CHECK(g.init_iterations == 12);
  CHECK(g.u_floor == 0.05);
  CHECK(g.filter_min_cells == 0.5);
  CHECK(g.damp_kn == 0.02);
  // 既定値と範囲外
  const CaseConfig d = make_kinetic_case({{"x", "vacuum"}}, KineticCaseOptions{});
  CHECK(d.gsis.max_iterations == 1000);
  CHECK(d.gsis.filter_min_cells == 0.0);
  CHECK(d.gsis.damp_kn == 0.0);
  KineticCaseOptions bad;
  bad.extra = "[gsis]\nmax_iterations = 0\n";
  CHECK_THROWS(make_kinetic_case({{"x", "vacuum"}}, bad));
  bad.extra = "[gsis]\ndamp_kn = -1.0\n";
  CHECK_THROWS(make_kinetic_case({{"x", "vacuum"}}, bad));
  bad.extra = "[gsis]\nfilter_min_cells = -0.5\n";
  CHECK_THROWS(make_kinetic_case({{"x", "vacuum"}}, bad));
  // [macro] の GSIS 用の項目
  KineticCaseOptions m;
  m.extra = "[macro]\nhot_interpolation = \"face\"\ngsis_face_state = \"walls\"\ngsis_continuity_pseudo_time = true\n";
  const MacroParams mp = parse_macro_params(make_kinetic_case({{"x", "vacuum"}}, m));
  CHECK(mp.hot_interpolation == "face");
  CHECK(mp.gsis_face_state == "walls");
  CHECK(mp.gsis_continuity_pseudo_time);
  KineticCaseOptions badm;
  badm.extra = "[macro]\nhot_interpolation = \"faces\"\n";
  CHECK_THROWS(parse_macro_params(make_kinetic_case({{"x", "vacuum"}}, badm)));
}

TEST_CASE("gsis: mass balance settings and the section mass flows of a GSIS iteration", "[gsis][section]") {
  // フェーズ 5c: [gsis].mass_balance_tolerance・mass_balance_required と [output].mass_flow_sections
  KineticCaseOptions o;
  o.extra = "[gsis]\nmass_balance_tolerance = 0.01\nmass_balance_required = true\n";
  const CaseConfig cfg = make_kinetic_case({{"x", "vacuum"}}, o);
  CHECK(cfg.gsis.mass_balance_tolerance == 0.01);
  CHECK(cfg.gsis.mass_balance_required);
  const CaseConfig d = make_kinetic_case({{"x", "vacuum"}}, KineticCaseOptions{});
  CHECK(d.gsis.mass_balance_tolerance == 2e-3);
  CHECK_FALSE(d.gsis.mass_balance_required);
  KineticCaseOptions bad;
  bad.extra = "[gsis]\nmass_balance_tolerance = 0.0\n";
  CHECK_THROWS(make_kinetic_case({{"x", "vacuum"}}, bad));
  bad.extra = "[gsis]\nmass_balance_required = 1\n";
  CHECK_THROWS(make_kinetic_case({{"x", "vacuum"}}, bad));

  // 管に似た箱の GSIS の反復で、断面の質量流量(運動論 f^{n+1/2} と PBC W^{n+1})が記録される
  const BoxProblem p = make_tube_box_problem({6, 3, 3}, 0.5, "backend = \"cpu\"\nprecision = \"fp64\"\n",
                                             std::string(kGsisSection) + "[macro]\ninit_iterations = 10\n"
                                                                         "[output]\nmass_flow_sections = \"0.5, 1, 1.5\"\n");
  const GsisOptions g = GsisOptions::from_case(p.config, parse_kinetic_params(p.config), parse_macro_params(p.config));
  REQUIRE(g.sections.positions.size() == 3);
  GsisSolver solver(p.config, p.mesh.mesh, p.mesh.geometry, parse_kinetic_params(p.config),
                    parse_macro_params(p.config), g);
  REQUIRE(solver.section_cuts().size() == 3);
  for (const SectionCut& c : solver.section_cuts()) CHECK(std::abs(c.projected_area - 1.0) < 1e-12);
  solver.initialize();
  for (int i = 0; i < 3; ++i) {
    const GsisIterationInfo& it = solver.iterate();
    REQUIRE(it.section_flow_kinetic.size() == 3);
    REQUIRE(it.section_flow_pbc.size() == 3);
    const MassBalance bk = mass_balance(it.patch_mass_flow_kinetic);
    const MassBalance bp = mass_balance(it.patch_mass_flow_pbc);
    REQUIRE(bk.valid);
    REQUIRE(bp.valid);
    for (std::size_t k = 0; k < 3; ++k) {
      // 流れは +x 向き。途中の状態なので一致はしないが、境界の流量と同じ大きさ
      CHECK(it.section_flow_kinetic[k] > 0.2 * bk.inflow);
      CHECK(it.section_flow_kinetic[k] < 5.0 * bk.inflow);
      CHECK(it.section_flow_pbc[k] > 0.2 * bp.inflow);
      CHECK(it.section_flow_pbc[k] < 5.0 * bp.inflow);
    }
  }
  // 断面の流量は運動論ソルバーの section_mass_flow(内部の番号付けのメッシュ)とも合う(同じ f^{n+1/2} ではないので参考)
  CHECK(solver.kinetic().section_mass_flow(g.sections).size() == 3);
}

TEST_CASE("gsis: relative change of eq. (59)", "[gsis]") {
  CellMacroState a;
  CellMacroState b;
  a.resize(2);
  b.resize(2);
  const std::vector<double> V = {1.0, 3.0};
  for (std::size_t c = 0; c < 2; ++c) {
    b.rho[c] = 1.0;
    b.T[c] = 2.0;
    b.ux[c] = 0.0;
  }
  a = b;
  a.rho[1] = 1.1;  // Σ Δρ² V / Σ ρ² V = 0.03 / 4
  a.T[0] = 2.2;    // 0.04 / 16
  a.ux[0] = 0.01;  // u の分母は下限 floor² ΣV = 0.01 × 4
  const RelativeChange r = relative_change(a, b, V, 0.1);
  CHECK(std::abs(r.rho - std::sqrt(0.03 / 4.0)) < 1e-15);
  CHECK(std::abs(r.T - std::sqrt(0.04 / 16.0)) < 1e-15);
  CHECK(std::abs(r.u - std::sqrt(1e-4 / 0.04)) < 1e-15);
  CHECK(r.epsilon == std::max({r.rho, r.u, r.T}));
}

TEST_CASE("gsis: GSIS converges to the CIS steady state on a slab; the difference shrinks with refinement",
          "[gsis][fixed_point]") {
  // 平板の熱伝導(壁温 0.9・1.1)とクエット流(壁速度 ∓0.2)。CIS 単独と GSIS の不動点の差は運動論とマクロの空間離散化の
  // 違いだけなので、格子を細かくすると減る(面の HoT、補正のフィルタ ℓ = λ(既定)。Kn = 0.5 ではクヌーセン層も 8 セルで
  // 解像される)。論文どおりのセル中心の HoT は壁際の差が大きく、Kn = 0.1 では細かくしても減らない(docs/phase4_report.md)
  for (const double kn : {0.5, 0.1}) {
    for (const bool couette : {false, true}) {
      double prev_d = 0.0;
      for (const int nx : {8, 16}) {
        const std::string extra = std::string(kGsisSection) + "[macro]\nhot_interpolation = \"face\"\n";
        const SlabProblem p = couette ? make_slab_problem(nx, kn, 1.0, 1.0, 0.2, "backend = \"cpu\"\nprecision = \"fp64\"\n", extra)
                                      : make_slab_problem(nx, kn, 0.9, 1.1, 0.0, "backend = \"cpu\"\nprecision = \"fp64\"\n", extra);
        const SolveResult cis = solve_cis(p.config, p.mesh, 1e-12, 20000);
        const SolveResult gsis = solve_gsis(p.config, p.mesh, 1e-11, 300);
        REQUIRE(cis.converged);
        REQUIRE(gsis.converged);
        // 分布(T または u_y)と流束(q_x または P_xy の体積平均)
        const std::vector<double>& a = couette ? gsis.moments.state.uy : gsis.moments.state.T;
        const std::vector<double>& b = couette ? cis.moments.state.uy : cis.moments.state.T;
        const double d = max_abs_diff(a, b);
        std::vector<double> fa(a.size()), fb(a.size());
        for (std::size_t c = 0; c < a.size(); ++c) {
          fa[c] = couette ? gsis.moments.sigma[c][3] : gsis.moments.q[c][0];
          fb[c] = couette ? cis.moments.sigma[c][3] : cis.moments.q[c][0];
        }
        const double qa = volume_mean(fa, p.mesh);
        const double qb = volume_mean(fb, p.mesh);
        const double dq = std::abs(qa - qb) / std::abs(qb);
        std::printf("Kn = %.1f %s nx = %2d: CIS %lld 反復、GSIS %lld 反復(CIS %lld 回)、%s の差の最大 %.3e、"
                    "流束 %.6e / %.6e(相対差 %.2e)\n",
                    kn, couette ? "クエット流" : "熱伝導", nx, cis.iterations, gsis.iterations, gsis.cis_iterations,
                    couette ? "u_y" : "T", d, qa, qb, dq);
        // 差は小さく(壁温差 0.2・壁速度差 0.4 に対して 0.4% 未満、流束は 0.1% 未満)、格子を倍にすると減る
        // (フィルタなしでは 1/2.5〜1/3.2。フィルタありでは Kn = 0.5 の差がすでに 10⁻⁶〜10⁻⁵ で、比は 0.4〜0.6)
        CHECK(d < 8e-4);
        CHECK(dq < 1e-3);
        if (nx == 16) CHECK(d < 0.7 * prev_d);
        prev_d = d;
        // GSIS の反復は CIS 単独より大幅に少ない
        CHECK(gsis.cis_iterations < cis.iterations);
      }
    }
  }
}

TEST_CASE("gsis: at small Kn GSIS needs far fewer CIS iterations than CIS alone", "[gsis][acceleration]") {
  const SlabProblem p =
      make_slab_problem(16, 0.02, 0.9, 1.1, 0.0, "backend = \"cpu\"\nprecision = \"fp64\"\n", kGsisSection);
  const SolveResult cis = solve_cis(p.config, p.mesh, 1e-8, 50000);
  const SolveResult gsis = solve_gsis(p.config, p.mesh, 1e-8, 300);
  REQUIRE(cis.converged);
  REQUIRE(gsis.converged);
  const double qa = volume_mean(component(gsis.moments.q, 0), p.mesh);
  const double qb = volume_mean(component(cis.moments.q, 0), p.mesh);
  std::printf("Kn = 0.02: CIS %lld 反復、GSIS %lld 反復(CIS %lld 回)、q_x %.6e / %.6e\n", cis.iterations,
              gsis.iterations, gsis.cis_iterations, qa, qb);
  CHECK(gsis.cis_iterations * 5 < cis.iterations);
  CHECK(std::abs(qa - qb) < 0.02 * std::abs(qb));
}

TEST_CASE("gsis: after the correction the moments of f are W^{n+1} and f is not below the threshold",
          "[gsis][correction]") {
  // 真空の出口のある箱(Kn = 0.5)。最初の反復は NSF の初期場から大きく動くので、出口の近くで負値の置換が起きる。
  // 論文どおりの規則("all")で、置換の後の f が閾値以上になることを確かめる("created"・"limit" は別の試験)
  const BoxProblem p = make_tube_box_problem({6, 3, 3}, 0.5,
                                             "backend = \"cpu\"\nprecision = \"fp64\"\ngsis_negative_check = \"all\"\n",
                                             "[gsis]\nn_cis = 5\nmax_pbc = 10\n[macro]\ninit_iterations = 20\n"
                                             "hot_interpolation = \"face\"\ngsis_continuity_pseudo_time = true\n");
  const KineticParams kp = parse_kinetic_params(p.config);
  const MacroParams mp = parse_macro_params(p.config);
  GsisSolver g(p.config, p.mesh.mesh, p.mesh.geometry, kp, mp, GsisOptions::from_case(p.config, kp, mp));
  g.initialize();
  long long replaced = 0;
  for (int n = 0; n < 3; ++n) {
    const GsisIterationInfo& it = g.iterate();
    replaced += it.replaced_cells;
    // モーメント(ρ, u, T)が W^{n+1} に一致する(置換したセルは f^eq(W^{n+1}) そのもの、そのほかは式 (14))
    const KineticMoments m = g.kinetic().moments();
    const CellMacroState& w = g.state();
    double dr = 0.0, du = 0.0, dT = 0.0;
    for (std::size_t c = 0; c < w.size(); ++c) {
      dr = std::max(dr, std::abs(m.state.rho[c] / w.rho[c] - 1.0));
      dT = std::max(dT, std::abs(m.state.T[c] / w.T[c] - 1.0));
      du = std::max({du, std::abs(m.state.ux[c] - w.ux[c]), std::abs(m.state.uy[c] - w.uy[c]),
                     std::abs(m.state.uz[c] - w.uz[c])});
    }
    std::printf("反復 %d: 置換 %lld セル、モーメントと W^{n+1} の差 ρ %.2e、u %.2e、T %.2e\n", n + 1, it.replaced_cells, dr,
                du, dT);
    // 差は離散 Maxwell 分布のモーメントの誤差(12³ 点、±6)の水準
    CHECK(dr < 1e-5);
    CHECK(du < 1e-5);
    CHECK(dT < 1e-5);
    // f の最小値が閾値 −1e-8 ρ/T^{3/2} 以上(置換の規則)
    const std::vector<double> f = g.kinetic().distribution();
    const auto nv = static_cast<std::size_t>(g.kinetic().setup().nv());
    for (std::size_t c = 0; c < w.size(); ++c) {
      double fmin = f[c * nv];
      for (std::size_t k = 1; k < nv; ++k) fmin = std::min(fmin, f[c * nv + k]);
      const double threshold = -1e-8 * w.rho[c] / (w.T[c] * std::sqrt(w.T[c]));
      CHECK(fmin >= threshold);
    }
  }
  CHECK(replaced > 0);
}

TEST_CASE("gsis: the \"created\" negative check ignores negative values that were there before the correction",
          "[gsis][correction]") {
  // 2 セルの箱。f = f^eq(W) + δ(滑らかな非平衡の部分、f^eq の 5%)で、セル 0 の速度格子の隅の 1 点だけ負
  // (−1e−6、閾値 −1e−8 ρ/T^{3/2} より下)にしておく。
  //   補正 0: "all" はセル 0 を置き換える(論文どおり)、"created" は置き換えない
  //   大きな補正(u_x = 2): δ < 0 の速度点で f^eq(W_new) が小さくなり負になるので、どちらも両方のセルを置き換える
  const LoadedMesh lm = make_kinetic_box({2, 1, 1}, {0, 0, 0}, {1, 0.5, 0.5},
                                         {"wall", "wall", "wall", "wall", "wall", "wall"});
  for (const char* rule : {"all", "created", "limit", "blend"}) {
    KineticCaseOptions o;
    // 期待値を連続の Maxwell 分布で作るので、平衡分布は連続のもの(フェーズ 5b で既定が保存的な離散平衡になった。
    // 速度格子 ±6・Δξ = 1 では u_x = 2 の連続の Maxwell 分布の離散密度が範囲の外の裾の分 1.6e−5 ずれる)
    o.kinetic = std::string("backend = \"cpu\"\nprecision = \"fp64\"\nequilibrium = \"continuous\"\ngsis_negative_check = \"") +
                rule + "\"\n";
    o.nv = {12, 12, 12};
    o.vmax = 6.0;
    const CaseConfig cfg = make_kinetic_case({{"wall", "diffuse_wall", 1.0}}, o);
    KineticSolver s(cfg, lm.mesh, lm.geometry, parse_kinetic_params(cfg));
    const VelocityGrid grid(cfg.velocity_grid.n, cfg.velocity_grid.lo, cfg.velocity_grid.hi);
    const int nv = grid.size();
    CellMacroState w;
    w.resize(2);
    for (std::size_t c = 0; c < 2; ++c) {
      w.rho[c] = 1.0;
      w.T[c] = 1.0;
      w.p[c] = 1.0;
    }
    std::vector<double> f(2 * static_cast<std::size_t>(nv));
    int corner = -1;
    for (int k = 0; k < nv; ++k) {
      const Vec3 xi = grid.xi(k);
      if (xi.x > 5.0 && xi.y > 5.0 && xi.z > 5.0) corner = k;
      const double feq = maxwellian(1.0, Vec3{}, 1.0, 1.0, xi);
      for (std::size_t c = 0; c < 2; ++c) {
        f[c * static_cast<std::size_t>(nv) + static_cast<std::size_t>(k)] =
            feq + 0.05 * feq * xi.x * xi.y / (1.0 + dot(xi, xi));
      }
    }
    REQUIRE(corner >= 0);
    f[static_cast<std::size_t>(corner)] = -1e-6;
    s.set_distribution(f);
    const long long r0 = s.apply_gsis_correction(w, w);
    CHECK(r0 == (std::string(rule) == "all" ? 1 : 0));
    s.set_distribution(f);
    CellMacroState wbig = w;
    for (std::size_t c = 0; c < 2; ++c) wbig.ux[c] = 2.0;
    CHECK(s.apply_gsis_correction(w, wbig) == 2);
    if (std::string(rule) == "limit") {
      // 置き換えずに補正を縮める: f は閾値(補正の前から負の点はその値から)を下回らず、平衡分布ではない
      const std::vector<double> g = s.distribution();
      const double thr = -1e-8;
      double worst = 1.0;
      double dev = 0.0;
      for (std::size_t i = 0; i < g.size(); ++i) {
        worst = std::min(worst, g[i] - (std::min(f[i], 0.0) + thr));
        const Vec3 xi = grid.xi(static_cast<int>(i % static_cast<std::size_t>(nv)));
        dev = std::max(dev, std::abs(g[i] - maxwellian(1.0, Vec3{2.0, 0.0, 0.0}, 1.0, 1.0, xi)));
      }
      CHECK(worst >= -1e-12);
      CHECK(dev > 1e-3);
    }
    if (std::string(rule) == "blend") {
      // 平衡分布と最小の割合で混ぜる: f は閾値を下回らず、モーメントは補正した f と同じ(ここでは δ の密度・運動量の
      // モーメントが 0 なので、f^eq(W_new) の離散モーメント)、非平衡の部分が一部残る
      const std::vector<double> g = s.distribution();
      const double thr = -1e-8;
      const double wq = grid.weight();
      double worst = 1.0;
      double dev = 0.0;
      for (std::size_t c = 0; c < 2; ++c) {
        double rho = 0.0;
        double mx = 0.0;
        double rho_e = 0.0;
        double mx_e = 0.0;
        for (int k = 0; k < nv; ++k) {
          const std::size_t i = c * static_cast<std::size_t>(nv) + static_cast<std::size_t>(k);
          worst = std::min(worst, g[i] - (std::min(f[i], 0.0) + thr));
          const Vec3 xi = grid.xi(k);
          const double e = maxwellian(1.0, Vec3{2.0, 0.0, 0.0}, 1.0, 1.0, xi);
          dev = std::max(dev, std::abs(g[i] - e));
          rho += wq * g[i];
          mx += wq * g[i] * xi.x;
          rho_e += wq * e;
          mx_e += wq * e * xi.x;
        }
        CHECK(std::abs(rho - rho_e) < 2e-6);  // セル 0 は隅の 1 点(−1e−6)の分だけ違う
        CHECK(std::abs(mx - mx_e) < 2e-5);
      }
      CHECK(worst >= -1e-12);
      CHECK(dev > 1e-6);
    }
  }
}

TEST_CASE("gsis: with hot_interpolation = \"face\" the viscous flux at W^0 is the interpolated kinetic stress",
          "[gsis][hot]") {
  // 圧力が一様なら Rhie–Chow の項は 0 なので、段の初めの残差は粘性係数によらないはず(面の NSF が厳密に打ち消し合う)。
  // 論文どおりの "cell" では、セル中心と面の NSF の差の分だけ粘性係数によって変わる
  const BoxProblem p = make_tube_box_problem({5, 4, 3}, 0.5, "backend = \"cpu\"\n", "");
  const std::size_t nc = static_cast<std::size_t>(p.mesh.mesh.n_cells);
  const std::size_t nb = static_cast<std::size_t>(p.mesh.mesh.n_boundary_faces());
  std::mt19937 rng(7);
  std::uniform_real_distribution<double> U(-1.0, 1.0);
  KineticMoments km;
  km.state.resize(nc);
  km.sigma.resize(nc);
  km.q.resize(nc);
  for (std::size_t c = 0; c < nc; ++c) {
    const Vec3& x = p.mesh.geometry.cell_center[c];
    km.state.p[c] = 1.0;
    km.state.T[c] = 1.0 + 0.2 * x.x * x.y + 0.05 * U(rng);
    km.state.rho[c] = 1.0 / km.state.T[c];
    km.state.ux[c] = 0.3 * x.y * x.y + 0.05 * U(rng);
    km.state.uy[c] = 0.1 * x.x + 0.05 * U(rng);
    km.state.uz[c] = 0.05 * U(rng);
    for (double& s : km.sigma[c]) s = 0.1 * U(rng);
    for (double& q : km.q[c]) q = 0.1 * U(rng);
  }
  BoundaryKineticFluxes bk;
  bk.F_plus.assign(nb, {});
  bk.F_minus.assign(nb, {});
  bk.face_state.resize(nb);
  for (std::size_t b = 0; b < nb; ++b) {
    const auto c = static_cast<std::size_t>(p.mesh.mesh.owner[b + static_cast<std::size_t>(p.mesh.mesh.n_internal_faces())]);
    bk.face_state[b] = {km.state.rho[c], km.state.ux[c], km.state.uy[c], km.state.uz[c], km.state.T[c]};
  }
  const DerivedQuantities d = derive_quantities(p.config);
  auto initial_residual = [&](const char* mode, double mu_scale) {
    MacroParams mp;
    mp.hot_interpolation = mode;
    PbcSolver s = PbcSolver::from_case(p.config, d, p.mesh.mesh, p.mesh.geometry, mp);
    MacroGas gas = s.gas();
    gas.mu_ref *= mu_scale;
    PbcSolver t(p.mesh.mesh, p.mesh.geometry, p.config.conditions.L_ref, gas, s.boundary_conditions(), mp);
    std::array<double, 3> r{};
    CellMacroState out;
    t.solve_gsis_stage(km, bk, 1, 500.0, out, [&](const PbcIterationInfo& it) {
      if (it.iteration == 0) r = {it.residual_mass, it.residual_momentum, it.residual_energy};
    });
    return r;
  };
  const auto f1 = initial_residual("face", 1.0);
  const auto f2 = initial_residual("face", 10.0);
  const auto c1 = initial_residual("cell", 1.0);
  const auto c2 = initial_residual("cell", 10.0);
  std::printf("段の初めの残差(質量・運動量・エネルギー): face %.6e %.6e %.6e / μ×10 %.6e %.6e %.6e、"
              "cell %.6e %.6e %.6e / μ×10 %.6e %.6e %.6e\n",
              f1[0], f1[1], f1[2], f2[0], f2[1], f2[2], c1[0], c1[1], c1[2], c2[0], c2[1], c2[2]);
  for (std::size_t k = 0; k < 3; ++k) CHECK(std::abs(f1[k] - f2[k]) <= 1e-10 * (std::abs(f1[k]) + 1e-12));
  CHECK(std::abs(c1[1] - c2[1]) > 1e-3 * std::abs(c1[1]));
}

TEST_CASE("gsis: results are bitwise reproducible, also with 1 and 4 OpenMP threads", "[gsis][determinism]") {
  const BoxProblem p = make_tube_box_problem({6, 3, 3}, 0.5, "backend = \"cpu\"\nprecision = \"fp64\"\n",
                                             "[gsis]\nn_cis = 3\nmax_pbc = 5\n[macro]\ninit_iterations = 10\n"
                                             "hot_interpolation = \"face\"\ngsis_continuity_pseudo_time = true\n");
  struct Run {
    std::vector<double> f;
    CellMacroState w;
    std::vector<long long> counts;
  };
  auto run = [&](int threads) {
    const int saved = macro::openmp_max_threads();
    macro::set_openmp_threads(threads);
    const KineticParams kp = parse_kinetic_params(p.config);
    const MacroParams mp = parse_macro_params(p.config);
    GsisSolver g(p.config, p.mesh.mesh, p.mesh.geometry, kp, mp, GsisOptions::from_case(p.config, kp, mp));
    Run r;
    const GsisInitReport& init = g.initialize();
    r.counts.push_back(init.pbc.iterations);
    for (int n = 0; n < 3; ++n) {
      const GsisIterationInfo& it = g.iterate();
      r.counts.push_back(it.pbc.iterations);
      r.counts.push_back(it.pbc.linear_iterations_pu);
      r.counts.push_back(it.pbc.linear_iterations_T);
      r.counts.push_back(it.replaced_cells);
    }
    r.f = g.kinetic().distribution();
    r.w = g.state();
    macro::set_openmp_threads(saved);
    return r;
  };
  const Run a = run(4);
  const Run b = run(4);
  const Run c = run(1);
  CHECK(a.counts == b.counts);
  CHECK(bitwise_equal(a.f, b.f));
  CHECK(bitwise_equal(a.w.p, b.w.p));
  CHECK(bitwise_equal(a.w.ux, b.w.ux));
  CHECK(bitwise_equal(a.w.T, b.w.T));
  CHECK(a.counts == c.counts);
  CHECK(bitwise_equal(a.f, c.f));
  CHECK(bitwise_equal(a.w.p, c.w.p));
  CHECK(bitwise_equal(a.w.uy, c.w.uy));
  CHECK(bitwise_equal(a.w.T, c.w.T));
}

TEST_CASE("gsis: the correction filter damps waves shorter than the mean free path by 1/(1 + k^2 l^2)",
          "[gsis][filter]") {
  // 1 次元の平板(側面は並進周期)の一様な状態 ρ = T = 1 では、局所平均自由行程 λ = Kn(L_ref 単位)。
  // セル中心の cos(mπx) は流束 0 の境界の離散ラプラシアンの固有ベクトルなので、フィルタの係数は厳密に
  // g_m = 1 / (1 + (ℓ/h)² (2 − 2 cos(mπh)))、ℓ = length_factor × λ
  const int nx = 40;
  const double kn = 0.05;
  const SlabProblem p = make_slab_problem(nx, kn, 1.0, 1.0, 0.0);
  const PbcSolver s = PbcSolver::from_case(p.config, derive_quantities(p.config), p.mesh.mesh, p.mesh.geometry,
                                           MacroParams{});
  const auto nc = static_cast<std::size_t>(nx);
  const double h = 1.0 / nx;
  CellMacroState half;
  half.resize(nc);
  for (std::size_t c = 0; c < nc; ++c) {
    half.rho[c] = half.T[c] = half.p[c] = 1.0;
    half.ux[c] = half.uy[c] = half.uz[c] = 0.0;
  }
  const double amp = 0.3;
  auto wave = [&](int m) {
    CellMacroState w = half;
    for (std::size_t c = 0; c < nc; ++c) {
      const double v = amp * std::cos(m * kPi * p.mesh.geometry.cell_center[c].x);
      w.rho[c] = std::exp(v);
      w.ux[c] = v;
      w.uy[c] = -0.5 * v;
      w.T[c] = std::exp(-v);
      w.p[c] = w.rho[c] * w.T[c];
    }
    return w;
  };
  for (const double factor : {2.0, 0.5}) {
    const double ell = factor * kn;
    for (const int m : {0, 1, 5, 20}) {
      CellMacroState w = wave(m);
      const CorrectionFilterReport r = s.filter_correction(half, w, factor, 0.0);
      CHECK(r.applied);
      CHECK(r.converged);
      CHECK(std::abs(r.max_length - ell) < 1e-12);
      const double g = 1.0 / (1.0 + (ell / h) * (ell / h) * (2.0 - 2.0 * std::cos(m * kPi * h)));
      double err = 0.0;
      for (std::size_t c = 0; c < nc; ++c) {
        const double v = amp * std::cos(m * kPi * p.mesh.geometry.cell_center[c].x);
        err = std::max({err, std::abs(std::log(w.rho[c]) - g * v), std::abs(w.ux[c] - g * v),
                        std::abs(w.uy[c] + 0.5 * g * v), std::abs(w.uz[c]), std::abs(std::log(w.T[c]) + g * v),
                        std::abs(w.p[c] - w.rho[c] * w.T[c])});
      }
      std::printf("補正のフィルタ: ℓ = %.3f、m = %d、係数 %.6f、誤差 %.2e\n", ell, m, g, err);
      CHECK(err < 1e-6 * amp);
    }
  }
  // 上限 max_length: ℓ = min(100 λ, 0.1) は ℓ = 0.1(係数 2)と同じ
  CellMacroState a = wave(5);
  CellMacroState b = wave(5);
  s.filter_correction(half, a, 2.0, 0.0);
  const CorrectionFilterReport rb = s.filter_correction(half, b, 100.0, 0.1);
  CHECK(std::abs(rb.max_length - 0.1) < 1e-15);
  CHECK(max_abs_diff(a.rho, b.rho) < 1e-12);
  // 格子規模の下限 min_cells(フェーズ 5b): ℓ = max(length_factor λ, min_cells h)。λ ≪ h のとき係数は ℓ = min_cells h の値
  for (const double mc : {1.0, 0.5}) {
    const double ell = mc * h;  // 平板のセルは h × h × h(V^{1/3} = h)
    for (const int m : {1, 20, 39}) {
      CellMacroState w = wave(m);
      const CorrectionFilterReport r = s.filter_correction(half, w, 1e-3, 0.0, mc);
      CHECK(r.applied);
      CHECK(std::abs(r.max_length - ell) < 1e-12);
      const double g = 1.0 / (1.0 + (ell / h) * (ell / h) * (2.0 - 2.0 * std::cos(m * kPi * h)));
      double err = 0.0;
      for (std::size_t c = 0; c < nc; ++c) {
        const double v = amp * std::cos(m * kPi * p.mesh.geometry.cell_center[c].x);
        err = std::max({err, std::abs(std::log(w.rho[c]) - g * v), std::abs(w.ux[c] - g * v)});
      }
      std::printf("補正のフィルタ(下限 %.1f h): m = %d、係数 %.6f、誤差 %.2e\n", mc, m, g, err);
      CHECK(err < 1e-6 * amp);
    }
  }
  // length_factor = 0 でも下限だけで働く
  {
    CellMacroState w = wave(20);
    CHECK(s.filter_correction(half, w, 0.0, 0.0, 1.0).applied);
  }
  // length_factor = 0 なら何もしない
  CellMacroState c0 = wave(5);
  const CellMacroState c1 = c0;
  CHECK_FALSE(s.filter_correction(half, c0, 0.0, 0.0).applied);
  CHECK(bitwise_equal(c0.rho, c1.rho));
  CHECK(bitwise_equal(c0.T, c1.T));
  // 大きく下げる補正でも密度・温度は正のまま(比の対数を均す)
  CellMacroState d = half;
  d.rho[3] = 1e-8;
  d.T[3] = 1e-8;
  s.filter_correction(half, d, 2.0, 0.0);
  for (std::size_t c = 0; c < nc; ++c) {
    CHECK(d.rho[c] > 0.0);
    CHECK(d.T[c] > 0.0);
  }
  CHECK(d.rho[3] > 1e-8);
}

TEST_CASE("gsis: at large Kn the correction filter keeps GSIS stable; without it the NSF correction overshoots",
          "[gsis][filter]") {
  // Kn = 5 の平板の熱伝導。NSF の合成方程式は、平均自由行程より短い波長の誤差(ここでは領域全体)に kλ 倍ほど
  // 強く応え(速度の誤差を消すための圧力の補正が μk|δu| になる)、従来の CIS(LU-SGS 1 往復)5 回では減らしきれずに
  // 発散する。フィルタ(ℓ = λ、既定)ではその成分を 1/(1 + k²λ²) に弱めるので収束し、不動点は CIS 単独に一致する。
  // フェーズ 5b で CIS の既定が強化した設定(octant、内部反復 4 回 + 壁の更新、ブロック間の反復 2 回)になったので、
  // フィルタの効果は従来の CIS の設定を明示して確かめ、強化した CIS ではフィルタなしでも収束することを確かめる
  // (docs/phase5_report.md §4.1)
  const double kn = 5.0;
  const std::string kin_new = "backend = \"cpu\"\nprecision = \"fp64\"\n";
  const std::string kin = kin_new + "sweep_order = \"block\"\ninner_sweeps = 1\ninner_wall_update = false\nblock_passes = 1\n";
  const SlabProblem p = make_slab_problem(16, kn, 0.9, 1.1, 0.0, kin, kGsisSection);
  const SolveResult cis = solve_cis(p.config, p.mesh, 1e-12, 20000);
  const SolveResult g1 = solve_gsis(p.config, p.mesh, 1e-10, 100);
  REQUIRE(cis.converged);
  CHECK(g1.converged);
  const double dT = max_abs_diff(g1.moments.state.T, cis.moments.state.T);
  std::printf("Kn = %.0f: CIS %lld 反復、GSIS(フィルタ)%lld 反復(CIS %lld 回)、T の差の最大 %.3e\n", kn,
              cis.iterations, g1.iterations, g1.cis_iterations, dT);
  CHECK(dT < 1e-5);
  const SlabProblem p0 =
      make_slab_problem(16, kn, 0.9, 1.1, 0.0, kin, std::string(kGsisSection) + "filter_length = 0.0\n");
  bool diverged = false;
  double eps0 = 0.0;
  try {
    const SolveResult g0 = solve_gsis(p0.config, p0.mesh, 1e-10, 40);
    eps0 = g0.epsilon;
    diverged = !g0.converged;
  } catch (const gsis::Error&) {
    diverged = true;
  }
  std::printf("Kn = %.0f: フィルタなしの GSIS は %s(ε = %.3e)\n", kn, diverged ? "収束しない" : "収束した", eps0);
  CHECK(diverged);
  // 強化した CIS(既定)ではフィルタなしでも収束し、不動点は CIS 単独に一致する
  const SlabProblem p1 =
      make_slab_problem(16, kn, 0.9, 1.1, 0.0, kin_new, std::string(kGsisSection) + "filter_length = 0.0\n");
  const SolveResult g2 = solve_gsis(p1.config, p1.mesh, 1e-10, 100);
  CHECK(g2.converged);
  const double dT2 = max_abs_diff(g2.moments.state.T, cis.moments.state.T);
  std::printf("Kn = %.0f: 強化した CIS のフィルタなしの GSIS %lld 反復(CIS %lld 回)、T の差の最大 %.3e\n", kn,
              g2.iterations, g2.cis_iterations, dT2);
  CHECK(dT2 < 1e-5);
}

TEST_CASE("gsis: damp_kn scales the correction by 1/(1 + (Kn/damp_kn)^2)", "[gsis][damping]") {
  // 平板の熱伝導(Kn = 0.5)で 1 回の GSIS 反復の PBC 段の補正(pbc_change: W^{n+1} と W^{n+1/2} の相対変化)を比べる。
  // damp_kn ≫ Kn では減衰しない(ビット単位で同じ)、damp_kn ≪ Kn では補正がほぼ 0、damp_kn ≈ Kn では半分ほど
  auto first_change = [](const std::string& extra) {
    const SlabProblem p = make_slab_problem(8, 0.5, 0.9, 1.1, 0.0, "backend = \"cpu\"\nprecision = \"fp64\"\n",
                                            std::string(kGsisSection) + "filter_length = 0.0\n" + extra);
    const KineticParams kp = parse_kinetic_params(p.config);
    const MacroParams mp = parse_macro_params(p.config);
    GsisSolver g(p.config, p.mesh.mesh, p.mesh.geometry, kp, mp, GsisOptions::from_case(p.config, kp, mp));
    g.initialize();
    return g.iterate().pbc_change.epsilon;
  };
  const double none = first_change("");
  const double big = first_change("damp_kn = 1.0e9\n");
  const double small = first_change("damp_kn = 1.0e-9\n");
  const double mid = first_change("damp_kn = 0.5\n");
  std::printf("damp_kn: なし %.6e、1e9 %.6e、1e-9 %.3e、0.5 %.6e\n", none, big, small, mid);
  CHECK(none > 1e-6);
  CHECK(big == none);
  CHECK(small < 1e-12 * none);
  CHECK(mid > 0.25 * none);
  CHECK(mid < 0.75 * none);
}

TEST_CASE("gsis: gsis_hot_kn weakens the HoT by Kn^2/(Kn^2 + Kn_c^2)", "[gsis][hot]") {
  // フェーズ 5b([macro].gsis_hot_kn): 連続流の領域の HoT を弱める。平板(Kn = 0.5)の 1 回目の段の HoT の大きさ
  // (hot_sigma_rms・hot_q_rms、セルの値)で、Kn_c ≫ Kn ならほぼ 0、Kn_c ≪ Kn なら変わらず、Kn_c ≈ Kn なら半分ほど
  auto first_hot = [](const std::string& extra) {
    const SlabProblem p = make_slab_problem(8, 0.5, 0.9, 1.1, 0.0, "backend = \"cpu\"\nprecision = \"fp64\"\n",
                                            std::string(kGsisSection) + "[macro]\nhot_interpolation = \"face\"\n" + extra);
    const KineticParams kp = parse_kinetic_params(p.config);
    const MacroParams mp = parse_macro_params(p.config);
    GsisSolver g(p.config, p.mesh.mesh, p.mesh.geometry, kp, mp, GsisOptions::from_case(p.config, kp, mp));
    g.initialize();
    const GsisIterationInfo& it = g.iterate();
    return std::array<double, 2>{it.hot_sigma_rms, it.hot_q_rms};
  };
  const auto none = first_hot("");
  const auto tiny = first_hot("gsis_hot_kn = 1.0e-9\n");
  const auto huge = first_hot("gsis_hot_kn = 1.0e9\n");
  const auto mid = first_hot("gsis_hot_kn = 0.5\n");
  std::printf("gsis_hot_kn: HoT_q なし %.4e、1e-9 %.4e、1e9 %.3e、0.5 %.4e\n", none[1], tiny[1], huge[1], mid[1]);
  CHECK(none[1] > 0.0);
  CHECK(std::abs(tiny[1] / none[1] - 1.0) < 1e-12);
  CHECK(huge[1] < 1e-12 * none[1]);
  CHECK(mid[1] > 0.3 * none[1]);
  CHECK(mid[1] < 0.7 * none[1]);
}
