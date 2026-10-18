#pragma once
// CSV の書き出しの補助(履歴・中心線分布など)。
// - UTF-8、改行は LF、区切りはカンマ
// - 実数は往復で値が変わらない最短の表記(std::format の "{}")
// - 文字列はカンマ・引用符・改行を含むときだけ引用符で囲む
// - autoflush を有効にすると 1 行ごとにフラッシュする(実行中に GUI などが読む履歴用)

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <span>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace gsis {

using CsvValue = std::variant<std::string, double, std::int64_t>;

// 1 つの値を CSV の欄の文字列にする
std::string csv_field(const CsvValue& value);
std::string csv_escape(std::string_view text);

class CsvWriter {
 public:
  // ファイルを作り、見出し行を書く(親ディレクトリは作る)
  CsvWriter(const std::filesystem::path& path, std::vector<std::string> header);

  void set_autoflush(bool on) { autoflush_ = on; }
  std::size_t n_columns() const { return header_.size(); }

  // 1 行を書く(欄の数は見出しと同じでなければ gsis::Error)
  void write_row(std::span<const CsvValue> values);
  void write_row(std::initializer_list<CsvValue> values) {
    write_row(std::span<const CsvValue>(values.begin(), values.size()));
  }
  void write_row(std::span<const double> values);
  void flush();

 private:
  std::filesystem::path path_;
  std::vector<std::string> header_;
  std::ofstream out_;
  bool autoflush_ = false;

  void write_line(const std::string& line);
};

}  // namespace gsis
