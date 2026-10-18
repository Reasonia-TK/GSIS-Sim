#pragma once
// gsis-solver の CLI の共通部分(サブコマンドの登録と、表示の補助)。

#include <cstddef>
#include <filesystem>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace gsis::cli {

// サブコマンドの関数。args はサブコマンド名より後の引数。返り値は終了コード。
using CommandFn = int (*)(std::span<const std::string> args);

struct Command {
  std::string_view name;
  std::string_view summary;
  std::string_view usage;
  CommandFn run;
};

int cmd_mesh_info(std::span<const std::string> args);
int cmd_check_case(std::span<const std::string> args);
int cmd_gpu_info(std::span<const std::string> args);
int cmd_cis_run(std::span<const std::string> args);  // フェーズ 2(app/cmd_cis_run.cpp)
int cmd_pbc_run(std::span<const std::string> args);  // フェーズ 3(app/cmd_pbc_run.cpp)
int cmd_run(std::span<const std::string> args);      // フェーズ 4(app/cmd_run.cpp。GSIS と CIS 単独)

// 引数の誤り(使い方を表示して終了コード 2 にする)
struct UsageError {
  std::string message;
};

// 表示の補助
std::string format_bytes(double bytes);           // 例: "9.33 GB(8.69 GiB)"、1 GB 未満は MB・kB
std::string format_count(long long n);            // 例: "135,000"
void print_heading(std::string_view title);       // 見出し行
void print_item(std::string_view label, std::string_view value);  // 「  label  value」(幅をそろえる)

// 穏やかな停止(GUI 向け): 出力先に STOP という名前のファイルができたら、run・cis-run・pbc-run はその反復の
// 終わりで結果(summary.json・VTU など)を書いて正常に終える(summary.json の "stopped" が true)。
// 古い STOP は実行の初めに消す
inline constexpr const char* kStopFileName = "STOP";
bool stop_requested(const std::filesystem::path& out_dir);
void clear_stop_request(const std::filesystem::path& out_dir);

}  // namespace gsis::cli
