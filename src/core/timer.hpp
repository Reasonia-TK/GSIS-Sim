#pragma once
// 壁時計の計時。Timer は単純なストップウォッチ、TimingTable は名前ごとの合計(時間内訳用)。

#include <chrono>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace gsis {

class Timer {
 public:
  using Clock = std::chrono::steady_clock;

  Timer() : start_(Clock::now()) {}
  void reset() { start_ = Clock::now(); }
  double seconds() const { return std::chrono::duration<double>(Clock::now() - start_).count(); }
  double milliseconds() const { return 1.0e3 * seconds(); }

 private:
  Clock::time_point start_;
};

// 名前ごとに所要時間と回数を合計する(最初に追加した順を保つ)。スレッド安全ではない。
class TimingTable {
 public:
  struct Entry {
    std::string name;
    double seconds = 0.0;
    long long calls = 0;
  };

  void add(std::string_view name, double seconds);
  double total(std::string_view name) const;  // 無ければ 0
  const std::vector<Entry>& entries() const { return entries_; }
  void clear() { entries_.clear(); }
  // 表の文字列(名前、合計秒、回数、割合)
  std::string format() const;

 private:
  std::vector<Entry> entries_;
};

// 有効範囲を抜けるときに経過時間を TimingTable に足す
class ScopedTimer {
 public:
  ScopedTimer(TimingTable& table, std::string name) : table_(table), name_(std::move(name)) {}
  ~ScopedTimer() { table_.add(name_, timer_.seconds()); }
  ScopedTimer(const ScopedTimer&) = delete;
  ScopedTimer& operator=(const ScopedTimer&) = delete;

 private:
  TimingTable& table_;
  std::string name_;
  Timer timer_;
};

}  // namespace gsis
