#include <cstdio>
#include <filesystem>
#include <format>
#include <string>
#include <system_error>

#include "app/cli.hpp"

namespace gsis::cli {

std::string format_bytes(double bytes) {
  constexpr double kiB = 1024.0;
  if (bytes >= 1e9) return std::format("{:.3g} GB({:.3g} GiB)", bytes / 1e9, bytes / (kiB * kiB * kiB));
  if (bytes >= 1e6) return std::format("{:.3g} MB({:.3g} MiB)", bytes / 1e6, bytes / (kiB * kiB));
  return std::format("{:.3g} kB({:.3g} KiB)", bytes / 1e3, bytes / kiB);
}

std::string format_count(long long n) {
  std::string digits = std::to_string(n < 0 ? -n : n);
  std::string out;
  int count = 0;
  for (auto it = digits.rbegin(); it != digits.rend(); ++it) {
    if (count > 0 && count % 3 == 0) out.insert(out.begin(), ',');
    out.insert(out.begin(), *it);
    ++count;
  }
  return n < 0 ? "-" + out : out;
}

void print_heading(std::string_view title) {
  const std::string line = std::format("\n{}\n", title);
  std::fwrite(line.data(), 1, line.size(), stdout);
}

void print_item(std::string_view label, std::string_view value) {
  // std::format の幅は全角文字を 2 桁として数える(UTF-8 の場合)。見出しが長くても 1 文字は空ける。
  const std::string line = std::format("  {:<25} {}\n", label, value);
  std::fwrite(line.data(), 1, line.size(), stdout);
}

bool stop_requested(const std::filesystem::path& out_dir) {
  std::error_code ec;
  return std::filesystem::exists(out_dir / kStopFileName, ec);
}

void clear_stop_request(const std::filesystem::path& out_dir) {
  std::error_code ec;
  std::filesystem::remove(out_dir / kStopFileName, ec);
}

}  // namespace gsis::cli
