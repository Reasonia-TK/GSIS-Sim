// S3: 混合反射の保存性、ヤコビアン、既存の拡散壁との同値性。
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include "gsis_test_cases.hpp"
#include "kinetic/kinetic_output.hpp"
#include "macro/boundary_flux.hpp"

using namespace gsis;

namespace {
void maxwell(CaseConfig& c, double alpha) {
  for (auto& b : c.boundaries) if (b.type == BoundaryType::DiffuseWall) {
    b.type = BoundaryType::MaxwellWall;
    b.accommodation = alpha;
  }
}
}

TEST_CASE("S3: alpha one preserves CIS and GSIS distributions", "[s3]") {
  auto p = test::make_slab_problem(8,0.5,0.95,1.05,0.1);
  auto c = p.config;
  maxwell(c,1.0);
  KineticSolver a(p.config,p.mesh.mesh,p.mesh.geometry,parse_kinetic_params(p.config));
  KineticSolver b(c,p.mesh.mesh,p.mesh.geometry,parse_kinetic_params(c));
  a.initialize_uniform(1.0,{},1.0); b.initialize_uniform(1.0,{},1.0);
  for (int k = 0; k < 5; ++k) { a.sweep(); b.sweep(); }
  CHECK(a.distribution() == b.distribution());
  const auto kp = parse_kinetic_params(c);
  auto mp = parse_macro_params(c); mp.init_iterations = 0;
  auto opt = GsisOptions::from_case(c,kp,mp);
  GsisSolver ga(p.config,p.mesh.mesh,p.mesh.geometry,kp,mp,opt);
  GsisSolver gb(c,p.mesh.mesh,p.mesh.geometry,kp,mp,opt);
  ga.initialize(nullptr,false); gb.initialize(nullptr,false);
  for (int k = 0; k < 3; ++k) { ga.iterate(); gb.iterate(); }
  CHECK(ga.kinetic().distribution() == gb.kinetic().distribution());
}

TEST_CASE("S3: Maxwell wall preserves mass for rotated normals and chunks", "[s3]") {
  for (const double angle : {0.0,0.37}) {
    auto p = test::make_slab_problem(8,0.5,0.95,1.05,0.1);
    maxwell(p.config,0.5);
    for (auto& x : p.mesh.mesh.points) {
      const double xx = std::cos(angle)*x.x-std::sin(angle)*x.y;
      x.y = std::sin(angle)*x.x+std::cos(angle)*x.y; x.x = xx;
    }
    p.mesh.geometry = compute_geometry(p.mesh.mesh);
    for (auto& b : p.config.boundaries) if (b.velocity) {
      const auto u = *b.velocity;
      b.velocity = Vec3{std::cos(angle)*u.x-std::sin(angle)*u.y,std::sin(angle)*u.x+std::cos(angle)*u.y,u.z};
    }
    auto kp = parse_kinetic_params(p.config); kp.velocity_chunk = 37;
    KineticSolver a(p.config,p.mesh.mesh,p.mesh.geometry,kp);
    a.initialize_uniform(1.0,{},1.0);
    for (int k = 0; k < 6; ++k) a.sweep();
    const auto flux = a.boundary_fluxes();
    for (std::size_t b = 0; b < flux.F_plus.size(); ++b) {
      const auto original = a.setup().bface_old_to_new[b];
      if (a.setup().patches[a.setup().bface_patch[original]].kind == kinetic::kBcMaxwellWall)
        CHECK(std::abs(flux.F_plus[b][0]+flux.F_minus[b][0]) < 2e-15);
    }
    const auto f = a.distribution();
    for (double value : f) CHECK(std::isfinite(value));
    // 固定fのprepareを繰り返しても圧力・分布は変わらない。
    a.prepare(); a.prepare(); CHECK(f == a.distribution());
    kp.velocity_chunk = 0;
    KineticSolver full(p.config,p.mesh.mesh,p.mesh.geometry,kp);
    full.initialize_uniform(1.0,{},1.0);
    for (int k = 0; k < 6; ++k) full.sweep();
    full.prepare();
    CHECK(f == full.distribution());
    for (auto& wall : p.config.boundaries) if (wall.type == BoundaryType::MaxwellWall) {
      wall.temperature = p.config.conditions.T0;
      wall.velocity.reset();
    }
    p.config.velocity_grid.n = {24,24,24};
    p.config.velocity_grid.lo.fill(-8.0); p.config.velocity_grid.hi.fill(8.0);
    KineticSolver equilibrium(p.config,p.mesh.mesh,p.mesh.geometry,kp);
    equilibrium.initialize_uniform(1.0,{},1.0);
    for (int k = 0; k < 5; ++k) equilibrium.sweep();
    const auto moments = equilibrium.moments();
    for (std::size_t c = 0; c < moments.state.size(); ++c) {
      CHECK(std::abs(moments.state.T[c] - 1.0) < 2e-10);
      CHECK(std::abs(moments.state.rho[c] - 1.0) < 2e-10);
    }
  }
}

TEST_CASE("S3: Maxwell macro Jacobian and consistency", "[s3]") {
  using namespace gsis::macro;
  MacroBoundaryCondition bc; bc.accommodation=0.35; bc.temperature=1.1; bc.velocity={0.0,0.0,0.2};
  const Vec3 n{0.6,0.8,0.0};
  const Vec5 W{{1.2,0.1,-0.03,0.12,0.9}};
  const auto J = boundary_flux_jacobian(bc,{},W,n,0.7);
  const Vec5 zero{};
  for (int c=0;c<5;++c) {
    auto lo=W,hi=W; lo[c]-=1e-6; hi[c]+=1e-6;
    const auto a=boundary_flux(bc,{},lo,n,0.7,zero,zero).total();
    const auto b=boundary_flux(bc,{},hi,n,0.7,zero,zero).total();
    for (int r=0;r<5;++r) CHECK(J(r,c) == Catch::Approx((b[r]-a[r])/2e-6).margin(3e-10));
  }
  const Vec5 plus{{0.4,0.2,0.3,0.1,0.9}},minus{{-0.4,-0.05,-0.03,0.04,-0.2}};
  const auto C=consistency_correction(bc,{},W,n,0.7,plus,minus);
  const auto F=boundary_flux(bc,{},W,n,0.7,C.C_plus,C.C_minus);
  for (int k=0;k<5;++k) { CHECK(F.F_plus[k] == Catch::Approx(plus[k]).margin(2e-16)); CHECK(F.F_minus[k] == Catch::Approx(minus[k]).margin(2e-16)); }
}

TEST_CASE("S3: mass flow pressure updates only on sweeps", "[s3]") {
  auto p=test::make_tube_box_problem({4,2,2},0.1,"backend=\"cpu\"\nprecision=\"fp64\"","");
  const auto ref=derive_quantities(p.config).ref;
  for (auto& b:p.config.boundaries) if (b.type==BoundaryType::PressureInlet) {
    b.type=BoundaryType::MassFlowInlet; b.mass_flow=0.1*ref.mass_flux*ref.length*ref.length; b.pressure.reset();
  }
  KineticSolver k(p.config,p.mesh.mesh,p.mesh.geometry,parse_kinetic_params(p.config));
  k.initialize_uniform(1.0,{},1.0);
  k.prepare(); const double old=k.setup().patches[0].p; k.prepare();
  CHECK(k.setup().patches[0].p == old);
  CHECK_FALSE(mass_flow_targets_met(k.setup(),k.prepare().patch_mass_flow));
  k.sweep(); CHECK(k.setup().patches[0].p != old);
  for (int i=0;i<500;++i) k.sweep();
  CHECK(mass_flow_targets_met(k.setup(),k.prepare().patch_mass_flow));
}
