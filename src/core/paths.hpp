#pragma once
// パスと UTF-8 文字列の変換、ファイルの読み書きの補助。
// 文字列は UTF-8 で持ち、パスへの変換は Windows の ANSI コードページに依存しないようにする。

#include <filesystem>
#include <string>
#include <string_view>

namespace gsis {

std::filesystem::path path_from_utf8(std::string_view utf8);
std::string path_to_utf8(const std::filesystem::path& path);

// ファイル全体をバイト列として読む(失敗したら gsis::Error)
std::string read_file(const std::filesystem::path& path);

// ファイルにバイト列を書く(親ディレクトリは作らない。失敗したら gsis::Error)
void write_file(const std::filesystem::path& path, std::string_view bytes);

// 親ディレクトリが無ければ作る
void ensure_parent_directory(const std::filesystem::path& path);

}  // namespace gsis
