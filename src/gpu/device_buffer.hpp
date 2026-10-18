#pragma once
// GPU 上の配列(cudaMalloc / cudaFree の RAII)。フェーズ 2 以降の分布関数・幾何量の置き場にも使う。

#include <cstddef>
#include <span>
#include <utility>

#include "core/error.hpp"
#include "gpu/cuda_check.hpp"

namespace gsis::gpu {

template <class T>
class DeviceBuffer {
 public:
  DeviceBuffer() = default;
  explicit DeviceBuffer(std::size_t count) { allocate(count); }
  explicit DeviceBuffer(std::span<const T> host) {
    allocate(host.size());
    upload(host);
  }
  ~DeviceBuffer() { release(); }

  DeviceBuffer(const DeviceBuffer&) = delete;
  DeviceBuffer& operator=(const DeviceBuffer&) = delete;
  DeviceBuffer(DeviceBuffer&& other) noexcept
      : ptr_(std::exchange(other.ptr_, nullptr)), count_(std::exchange(other.count_, 0)) {}
  DeviceBuffer& operator=(DeviceBuffer&& other) noexcept {
    if (this != &other) {
      release();
      ptr_ = std::exchange(other.ptr_, nullptr);
      count_ = std::exchange(other.count_, 0);
    }
    return *this;
  }

  void allocate(std::size_t count) {
    release();
    if (count > 0) {
      GSIS_CUDA_CHECK(cudaMalloc(reinterpret_cast<void**>(&ptr_), count * sizeof(T)));
    }
    count_ = count;
  }

  void upload(std::span<const T> host) {
    if (host.size() != count_) allocate(host.size());
    if (count_ > 0) {
      GSIS_CUDA_CHECK(cudaMemcpy(ptr_, host.data(), count_ * sizeof(T), cudaMemcpyHostToDevice));
    }
  }

  void download(std::span<T> host) const {
    GSIS_CHECK(host.size() == count_, "GPU から写す先の長さが配列の長さと違います");
    if (count_ > 0) {
      GSIS_CUDA_CHECK(cudaMemcpy(host.data(), ptr_, count_ * sizeof(T), cudaMemcpyDeviceToHost));
    }
  }

  T* data() { return ptr_; }
  const T* data() const { return ptr_; }
  std::size_t size() const { return count_; }
  std::size_t bytes() const { return count_ * sizeof(T); }

 private:
  T* ptr_ = nullptr;
  std::size_t count_ = 0;

  void release() noexcept {
    if (ptr_ != nullptr) {
      cudaFree(ptr_);  // 解放の失敗は無視する(デストラクタから例外を投げない)
      ptr_ = nullptr;
    }
    count_ = 0;
  }
};

}  // namespace gsis::gpu
