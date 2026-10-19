#pragma once
// CUDA の配列構成から見積もる。確保はせず、チャンクは安全側に丸める。
#include <cstdint>

#include "core/json.hpp"
#include "kinetic/kinetic_setup.hpp"

namespace gsis::kinetic {
json::Value estimate_device_memory(const KineticSetup& setup, std::int64_t free_bytes);
}  // namespace gsis::kinetic
