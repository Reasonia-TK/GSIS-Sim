#pragma once
// physics の __host__ __device__ 関数(Maxwell・Shakhov・半空間流束とヤコビアン)を GPU で評価し、
// CPU と同じ結果になることを確かめるための関数(テストと gpu-info の自己診断用)。
// このヘッダは CUDA のヘッダを含まない。

#include <span>
#include <vector>

#include "core/vec3.hpp"
#include "physics/half_space_flux.hpp"

namespace gsis::gpu {

struct PhysicsSample {
  double p;
  Vec3 u;
  double T;
  Vec3 q;   // Shakhov の熱流束
  Vec3 xi;  // 分布関数を評価する速度
  Vec3 n;   // 半空間流束の単位法線
};

struct PhysicsResult {
  double f_eq;
  double f_shakhov;
  Vec5 flux_plus;
  Vec5 flux_minus;
  Mat5 jacobian_plus;
  Mat5 jacobian_minus;
};

std::vector<PhysicsResult> evaluate_physics_gpu(std::span<const PhysicsSample> samples, double R, double Pr);
std::vector<PhysicsResult> evaluate_physics_cpu(std::span<const PhysicsSample> samples, double R, double Pr);

}  // namespace gsis::gpu
