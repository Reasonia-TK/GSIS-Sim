// S3: 一般法線の反射キャッシュを含むCPU/CUDAの一致と追加メモリ。
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include "gpu/device_info.hpp"
#include "gsis_test_cases.hpp"
#include "kinetic/memory_estimate.hpp"

using namespace gsis;

TEST_CASE("S3: Maxwell CPU CUDA consistency and memory", "[s3][gpu]") {
  if (gpu::device_count() == 0) SKIP("CUDAデバイスがありません");
  for (const std::string precision : {"fp32","fp64"}) {
    for (double angle : {0.0,0.37}) {
      auto p=test::make_slab_problem(8,0.5,0.95,1.05,0.0);
      for (auto& b:p.config.boundaries) if (b.type==BoundaryType::DiffuseWall) {
        b.type=BoundaryType::MaxwellWall; b.accommodation=0.5;
      }
      for (auto& x:p.mesh.mesh.points) {
        const double xx=std::cos(angle)*x.x-std::sin(angle)*x.y;
        x.y=std::sin(angle)*x.x+std::cos(angle)*x.y; x.x=xx;
      }
      p.mesh.geometry=compute_geometry(p.mesh.mesh);
      auto kp=parse_kinetic_params(p.config); kp.precision=precision; kp.velocity_chunk=37;
      KineticSolver cpu(p.config,p.mesh.mesh,p.mesh.geometry,kp);
      kp.backend="cuda";
      KineticSolver cuda(p.config,p.mesh.mesh,p.mesh.geometry,kp);
      cpu.initialize_uniform(1.0,{},1.0); cuda.initialize_uniform(1.0,{},1.0);
      for (int i=0;i<5;++i) { cpu.sweep(); cuda.sweep(); }
      const auto a=cpu.moments(),b=cuda.moments();
      for (std::size_t i=0;i<a.state.size();++i) {
        CHECK(std::abs(a.state.T[i]-b.state.T[i]) < (precision=="fp64" ? 2e-12 : 4e-6));
        CHECK(std::abs(a.state.rho[i]-b.state.rho[i]) < (precision=="fp64" ? 2e-12 : 4e-6));
      }
      const auto estimate=kinetic::estimate_device_memory(cuda.setup(),8LL*1024*1024*1024);
      CHECK(estimate.at("total").as_int() == static_cast<std::int64_t>(cuda.backend().memory_bytes()));
    }
  }
}
