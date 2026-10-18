#pragma once
// 簡単なロガー。標準エラーに「[経過時間] 種類: 本文」の形で書く。
// .cu ファイルからは読まない(<format> を使うため)。

#include <cstddef>
#include <format>
#include <functional>
#include <string>
#include <string_view>
#include <utility>

namespace gsis {

enum class LogLevel { Debug = 0, Info = 1, Warn = 2, Error = 3, Off = 4 };

void set_log_level(LogLevel level);
LogLevel log_level();
bool log_enabled(LogLevel level);

// 1 行を書く(改行は不要)。複数スレッドから呼んでよい。
void log_message(LogLevel level, std::string_view message);

// 出力先を差し替える(テスト用)。空の関数を渡すと標準エラーに戻す。
using LogSink = std::function<void(LogLevel, std::string_view)>;
void set_log_sink(LogSink sink);

// これまでに出た警告の数(出力を抑えていても数える)
std::size_t warning_count();

template <class... Args>
void log_debug(std::format_string<Args...> fmt, Args&&... args) {
  if (log_enabled(LogLevel::Debug)) {
    log_message(LogLevel::Debug, std::format(fmt, std::forward<Args>(args)...));
  }
}
template <class... Args>
void log_info(std::format_string<Args...> fmt, Args&&... args) {
  if (log_enabled(LogLevel::Info)) {
    log_message(LogLevel::Info, std::format(fmt, std::forward<Args>(args)...));
  }
}
template <class... Args>
void log_warn(std::format_string<Args...> fmt, Args&&... args) {
  log_message(LogLevel::Warn, std::format(fmt, std::forward<Args>(args)...));
}
template <class... Args>
void log_error(std::format_string<Args...> fmt, Args&&... args) {
  log_message(LogLevel::Error, std::format(fmt, std::forward<Args>(args)...));
}

}  // namespace gsis
