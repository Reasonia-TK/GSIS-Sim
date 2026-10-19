// gsis-solver: GSIS-PBC ソルバーの CLI。
//
//   gsis-solver [共通オプション] <サブコマンド> [引数...]
//
// サブコマンド: run(GSIS・CIS 単独)、mesh-info、check-case、gpu-info、cis-run(CIS 単独)、pbc-run(PBC 単独)

#include <cstdio>
#include <exception>
#include <format>
#include <span>
#include <string>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>
#endif

#include "app/cli.hpp"
#include "core/build_info.hpp"
#include "core/error.hpp"
#include "core/log.hpp"

namespace {

using gsis::cli::Command;

constexpr Command kCommands[] = {
    {"run", "ケースの mode(gsis / cis)に応じて GSIS か CIS 単独で定常解を求める",
     "run <case.toml> [--out <dir>] [--mode gsis|cis|nsf] [--max-iter N] [--min-iter N] [--tol X]\n"
     "                [--backend cpu|cuda] [--precision fp32|fp64] [--set [kinetic.|macro.|gsis.]key=value]...\n"
     "                [--threads N] [--print-every N] [--vtu-every N] [--dump-every N] [--no-vtu] [--cells-csv]\n"
     "                [--init-from <cells.csv>] [--save-init <cells.csv>] [--sections <位置の並び>]",
     gsis::cli::cmd_run},
    {"mesh-info", "メッシュ(.pmesh)を読み、検査結果と統計を表示する",
     "mesh-info <file.pmesh> [--vtu <out.vtu>] [--boundary-vtu <out.vtu>] [--ascii]",
     gsis::cli::cmd_mesh_info},
    {"check-case", "ケース定義(TOML)とメッシュを読み、導出量とメモリ量を表示する",
     "check-case <case.toml> [--json [--no-mesh] [--free-vram-bytes N]]", gsis::cli::cmd_check_case},
    {"gpu-info", "GPU の情報を表示する(--smoke で GPU と CPU のモーメント計算を比べる)",
     "gpu-info [--smoke] [--cells <N>]", gsis::cli::cmd_gpu_info},
    {"cis-run", "運動論ソルバー(CIS)だけで定常解を求める",
     "cis-run <case.toml> [--out <dir>] [--max-iter N] [--min-iter N] [--tol X]\n"
     "                [--backend cpu|cuda] [--precision fp32|fp64] [--set key=value]...\n"
     "                [--print-every N] [--vtu-every N] [--no-vtu] [--cells-csv] [--init-from <cells.csv>]\n"
     "                [--sections <位置の並び>]",
     gsis::cli::cmd_cis_run},
    {"pbc-run", "PBC マクロソルバーだけ(NSF のみの単独モード)で、一様な状態から定常解を求める",
     "pbc-run <case.toml> [--out <dir>] [--iterations N] [--cfl X] [--cfl-start X] [--cfl-growth G]\n"
     "                [--tolerance X] [--advection upwind1|upwind2] [--threads N] [--print-every N]\n"
     "                [--no-vtu] [--cells-csv] [--restart <cells.csv>] [--continuity-pseudo-time]",
     gsis::cli::cmd_pbc_run},
};

void print_usage() {
  std::string s = std::format("{}\n\n", gsis::build_description());
  s += "使い方: gsis-solver [共通オプション] <サブコマンド> [引数...]\n\nサブコマンド:\n";
  for (const Command& c : kCommands) {
    s += std::format("  {:<12}{}\n", c.name, c.summary);
    s += std::format("  {:<12}  {}\n", "", c.usage);
  }
  s += "\n共通オプション:\n";
  s += "  -v, --verbose   詳細なログを出す\n";
  s += "  -q, --quiet     警告とエラーだけを出す\n";
  s += "  -h, --help      この説明を表示する\n";
  s += "      --version   版を表示する\n";
  std::fwrite(s.data(), 1, s.size(), stdout);
}

#ifdef _WIN32
// 出力を UTF-8 にし、終了時に元のコードページに戻す
class ConsoleUtf8Guard {
 public:
  ConsoleUtf8Guard() : previous_(GetConsoleOutputCP()) { SetConsoleOutputCP(CP_UTF8); }
  ~ConsoleUtf8Guard() {
    if (previous_ != 0) SetConsoleOutputCP(previous_);
  }
  ConsoleUtf8Guard(const ConsoleUtf8Guard&) = delete;
  ConsoleUtf8Guard& operator=(const ConsoleUtf8Guard&) = delete;

 private:
  UINT previous_;
};

// コマンドライン引数を UTF-16 で取り直し、UTF-8 にする(コードページに依存しない)
std::vector<std::string> utf8_arguments(int argc, char** argv) {
  int wargc = 0;
  LPWSTR* wargv = CommandLineToArgvW(GetCommandLineW(), &wargc);
  if (wargv == nullptr) return std::vector<std::string>(argv, argv + argc);
  std::vector<std::string> out;
  for (int i = 0; i < wargc; ++i) {
    const int n = WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<std::size_t>(n > 0 ? n - 1 : 0), '\0');
    if (n > 1) WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, s.data(), n, nullptr, nullptr);
    out.push_back(std::move(s));
  }
  LocalFree(wargv);
  return out;
}
#else
std::vector<std::string> utf8_arguments(int argc, char** argv) {
  return std::vector<std::string>(argv, argv + argc);
}
#endif

int run(const std::vector<std::string>& args) {
  std::size_t i = 1;
  for (; i < args.size(); ++i) {
    const std::string& a = args[i];
    if (a == "-v" || a == "--verbose") {
      gsis::set_log_level(gsis::LogLevel::Debug);
    } else if (a == "-q" || a == "--quiet") {
      gsis::set_log_level(gsis::LogLevel::Warn);
    } else if (a == "-h" || a == "--help") {
      print_usage();
      return 0;
    } else if (a == "--version") {
      const std::string s = gsis::build_description() + "\n";
      std::fwrite(s.data(), 1, s.size(), stdout);
      return 0;
    } else if (!a.empty() && a[0] == '-') {
      throw gsis::cli::UsageError{std::format("知らないオプション {} です", a)};
    } else {
      break;
    }
  }
  if (i >= args.size()) {
    print_usage();
    return 2;
  }
  const std::string& name = args[i];
  for (const Command& c : kCommands) {
    if (c.name == name) {
      const std::span<const std::string> rest(args.data() + i + 1, args.size() - i - 1);
      try {
        return c.run(rest);
      } catch (const gsis::cli::UsageError& e) {
        std::fprintf(stderr, "引数の誤り: %s\n使い方: gsis-solver %s\n", e.message.c_str(),
                     std::string(c.usage).c_str());
        return 2;
      }
    }
  }
  throw gsis::cli::UsageError{std::format("知らないサブコマンド {} です", name)};
}

}  // namespace

int main(int argc, char** argv) {
#ifdef _WIN32
  ConsoleUtf8Guard console_guard;
#endif
  const std::vector<std::string> args = utf8_arguments(argc, argv);
  try {
    return run(args);
  } catch (const gsis::cli::UsageError& e) {
    std::fprintf(stderr, "引数の誤り: %s(gsis-solver --help で使い方を表示)\n", e.message.c_str());
    return 2;
  } catch (const gsis::Error& e) {
    std::fprintf(stderr, "エラー: %s\n", e.what());
    if (gsis::log_enabled(gsis::LogLevel::Debug) && !e.location().empty()) {
      std::fprintf(stderr, "  発生箇所: %s\n", e.location().c_str());
    }
    return 1;
  } catch (const std::exception& e) {
    std::fprintf(stderr, "エラー: %s\n", e.what());
    return 1;
  }
}
