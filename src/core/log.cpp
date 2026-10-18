#include "core/log.hpp"

#include <chrono>
#include <cstdio>
#include <mutex>

namespace gsis {
namespace {

// 経過時間の起点(プログラムの開始時。名前空間の静的変数なので main より前に初期化される)
const std::chrono::steady_clock::time_point g_process_start = std::chrono::steady_clock::now();

struct LoggerState {
  std::mutex mutex;
  LogLevel level = LogLevel::Info;
  LogSink sink;
  std::size_t warnings = 0;
  std::chrono::steady_clock::time_point start = g_process_start;
};

LoggerState& state() {
  static LoggerState s;
  return s;
}

const char* level_label(LogLevel level) {
  switch (level) {
    case LogLevel::Debug:
      return "詳細";
    case LogLevel::Info:
      return "情報";
    case LogLevel::Warn:
      return "警告";
    case LogLevel::Error:
      return "エラー";
    case LogLevel::Off:
      break;
  }
  return "";
}

}  // namespace

void set_log_level(LogLevel level) {
  std::lock_guard lock(state().mutex);
  state().level = level;
}

LogLevel log_level() {
  std::lock_guard lock(state().mutex);
  return state().level;
}

bool log_enabled(LogLevel level) {
  return level != LogLevel::Off && static_cast<int>(level) >= static_cast<int>(log_level());
}

void log_message(LogLevel level, std::string_view message) {
  LoggerState& s = state();
  std::lock_guard lock(s.mutex);
  if (level == LogLevel::Warn) {
    ++s.warnings;
  }
  if (level == LogLevel::Off || static_cast<int>(level) < static_cast<int>(s.level)) {
    return;
  }
  if (s.sink) {
    s.sink(level, message);
    return;
  }
  const double t =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - s.start).count();
  const std::string line = std::format("[{:9.3f} s] {}: {}\n", t, level_label(level), message);
  std::fwrite(line.data(), 1, line.size(), stderr);
  std::fflush(stderr);
}

void set_log_sink(LogSink sink) {
  std::lock_guard lock(state().mutex);
  state().sink = std::move(sink);
}

std::size_t warning_count() {
  std::lock_guard lock(state().mutex);
  return state().warnings;
}

}  // namespace gsis
