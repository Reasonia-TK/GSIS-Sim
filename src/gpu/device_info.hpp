#pragma once
// GPU の情報とメモリ量の取得。CUDA のヘッダを含まない(CUDA なしのコードからも読める)。

#include <cstddef>
#include <string>
#include <vector>

namespace gsis::gpu {

struct DeviceInfo {
  int index = 0;
  std::string name;
  int cc_major = 0;
  int cc_minor = 0;
  int multiprocessors = 0;
  std::size_t total_memory = 0;       // バイト
  std::size_t free_memory = 0;        // バイト(取得した時点)
  int clock_khz = 0;
  int memory_clock_khz = 0;
  int memory_bus_width = 0;           // ビット
  int l2_cache_bytes = 0;
  std::size_t shared_per_block = 0;
  std::size_t shared_per_block_optin = 0;
  std::size_t shared_per_multiprocessor = 0;
  int registers_per_block = 0;
  int max_threads_per_block = 0;
  int max_threads_per_multiprocessor = 0;
  int warp_size = 0;
  bool kernel_timeout = false;        // カーネルの実行時間に上限がある(WDDM の TDR)
  bool tcc_driver = false;
  bool integrated = false;
  bool ecc_enabled = false;
  int fp32_to_fp64_ratio = 0;         // 単精度と倍精度の性能比
  std::string pci_bus_id;
};

struct RuntimeVersions {
  int driver = 0;    // ドライバが対応する CUDA の版(例: 13030 は 13.3)
  int runtime = 0;   // cudaRuntimeGetVersion が返す版
  int compiled = 0;  // ビルドに使った CUDA ランタイムの版(CUDART_VERSION)
};

// CUDA デバイスの数。ドライバがない・デバイスがないときは 0(例外を投げない)
int device_count();

// 全デバイスの情報(free_memory を得るためにコンテキストを作る)
std::vector<DeviceInfo> query_devices();

RuntimeVersions runtime_versions();

struct MemoryInfo {
  std::size_t free = 0;
  std::size_t total = 0;
};
MemoryInfo memory_info(int device = 0);

// 版番号 13040 → "13.4"
std::string version_to_string(int version);

}  // namespace gsis::gpu
