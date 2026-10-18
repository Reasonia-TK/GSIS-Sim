#include "gpu/device_info.hpp"

#include <cuda_runtime.h>

#include <format>

#include "gpu/cuda_check.hpp"

namespace gsis::gpu {

int device_count() {
  int n = 0;
  const cudaError_t err = cudaGetDeviceCount(&n);
  if (err != cudaSuccess) {
    cudaGetLastError();  // 誤りの状態を消す
    return 0;
  }
  return n;
}

namespace {
int attribute(cudaDeviceAttr attr, int device) {
  int v = 0;
  if (cudaDeviceGetAttribute(&v, attr, device) != cudaSuccess) {
    cudaGetLastError();
    return 0;
  }
  return v;
}
}  // namespace

std::vector<DeviceInfo> query_devices() {
  std::vector<DeviceInfo> out;
  const int n = device_count();
  for (int d = 0; d < n; ++d) {
    cudaDeviceProp prop{};
    GSIS_CUDA_CHECK(cudaGetDeviceProperties(&prop, d));
    DeviceInfo info;
    info.index = d;
    info.name = prop.name;
    info.cc_major = prop.major;
    info.cc_minor = prop.minor;
    info.multiprocessors = prop.multiProcessorCount;
    info.total_memory = prop.totalGlobalMem;
    info.memory_bus_width = prop.memoryBusWidth;
    info.l2_cache_bytes = prop.l2CacheSize;
    info.shared_per_block = prop.sharedMemPerBlock;
    info.shared_per_block_optin = prop.sharedMemPerBlockOptin;
    info.shared_per_multiprocessor = prop.sharedMemPerMultiprocessor;
    info.registers_per_block = prop.regsPerBlock;
    info.max_threads_per_block = prop.maxThreadsPerBlock;
    info.max_threads_per_multiprocessor = prop.maxThreadsPerMultiProcessor;
    info.warp_size = prop.warpSize;
    info.tcc_driver = prop.tccDriver != 0;
    info.integrated = prop.integrated != 0;
    info.ecc_enabled = prop.ECCEnabled != 0;
    info.pci_bus_id = std::format("{:04x}:{:02x}:{:02x}", prop.pciDomainID, prop.pciBusID, prop.pciDeviceID);
    // CUDA 13 で cudaDeviceProp から削除された項目は属性 API で取る
    info.clock_khz = attribute(cudaDevAttrClockRate, d);
    info.memory_clock_khz = attribute(cudaDevAttrMemoryClockRate, d);
    info.kernel_timeout = attribute(cudaDevAttrKernelExecTimeout, d) != 0;
    info.fp32_to_fp64_ratio = attribute(cudaDevAttrSingleToDoublePrecisionPerfRatio, d);
    const MemoryInfo mem = memory_info(d);
    info.free_memory = mem.free;
    out.push_back(std::move(info));
  }
  return out;
}

RuntimeVersions runtime_versions() {
  RuntimeVersions v;
  v.compiled = CUDART_VERSION;
  if (cudaDriverGetVersion(&v.driver) != cudaSuccess) {
    cudaGetLastError();
    v.driver = 0;
  }
  if (cudaRuntimeGetVersion(&v.runtime) != cudaSuccess) {
    cudaGetLastError();
    v.runtime = 0;
  }
  return v;
}

MemoryInfo memory_info(int device) {
  int current = 0;
  GSIS_CUDA_CHECK(cudaGetDevice(&current));
  // 例外で抜けても元のデバイスに戻す
  struct RestoreDevice {
    int device;
    ~RestoreDevice() { cudaSetDevice(device); }
  } restore{current};
  GSIS_CUDA_CHECK(cudaSetDevice(device));
  MemoryInfo m;
  GSIS_CUDA_CHECK(cudaMemGetInfo(&m.free, &m.total));
  return m;
}

std::string version_to_string(int version) {
  if (version <= 0) return "不明";
  return std::format("{}.{}", version / 1000, (version % 1000) / 10);
}

}  // namespace gsis::gpu
