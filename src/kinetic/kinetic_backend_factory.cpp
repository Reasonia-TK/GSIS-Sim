// CUDA なしでビルドしたときの make_cuda_backend(CUDA ありでは kinetic_backend_cuda.cpp が定義する)

#include "core/error.hpp"
#include "kinetic/kinetic_backend.hpp"

namespace gsis::kinetic {

#if !GSIS_WITH_CUDA
std::unique_ptr<KineticBackend> make_cuda_backend(const KineticSetup& /*setup*/) {
  GSIS_FAIL("CUDA なしでビルドされているので、運動論ソルバーの GPU 版は使えません(kinetic.backend = \"cpu\" を使ってください)");
}
#endif

}  // namespace gsis::kinetic
