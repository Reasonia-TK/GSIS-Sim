#include "io/csv_writer.hpp"

#include <cmath>
#include <format>

#include "core/error.hpp"
#include "core/paths.hpp"

namespace gsis {

std::string csv_escape(std::string_view text) {
  const bool needs_quotes = text.find_first_of(",\"\r\n") != std::string_view::npos;
  if (!needs_quotes) return std::string(text);
  std::string out = "\"";
  for (const char c : text) {
    if (c == '"') out += '"';
    out += c;
  }
  out += '"';
  return out;
}

std::string csv_field(const CsvValue& value) {
  if (const auto* s = std::get_if<std::string>(&value)) return csv_escape(*s);
  if (const auto* i = std::get_if<std::int64_t>(&value)) return std::format("{}", *i);
  const double d = std::get<double>(value);
  if (std::isnan(d)) return "nan";
  if (std::isinf(d)) return d > 0 ? "inf" : "-inf";
  return std::format("{}", d);
}

CsvWriter::CsvWriter(const std::filesystem::path& path, std::vector<std::string> header)
    : path_(path), header_(std::move(header)) {
  GSIS_CHECK(!header_.empty(), "CSV の見出しが空です");
  ensure_parent_directory(path_);
  out_.open(path_, std::ios::binary | std::ios::trunc);
  GSIS_CHECK(out_.good(), std::format("CSV ファイルを作れません: {}", path_to_utf8(path_)));
  std::string line;
  for (std::size_t i = 0; i < header_.size(); ++i) {
    if (i > 0) line += ',';
    line += csv_escape(header_[i]);
  }
  write_line(line);
}

void CsvWriter::write_row(std::span<const CsvValue> values) {
  GSIS_CHECK(values.size() == header_.size(),
             std::format("CSV の欄の数 {} が見出しの数 {} と違います({})", values.size(),
                         header_.size(), path_to_utf8(path_)));
  std::string line;
  for (std::size_t i = 0; i < values.size(); ++i) {
    if (i > 0) line += ',';
    line += csv_field(values[i]);
  }
  write_line(line);
}

void CsvWriter::write_row(std::span<const double> values) {
  GSIS_CHECK(values.size() == header_.size(),
             std::format("CSV の欄の数 {} が見出しの数 {} と違います({})", values.size(),
                         header_.size(), path_to_utf8(path_)));
  std::string line;
  for (std::size_t i = 0; i < values.size(); ++i) {
    if (i > 0) line += ',';
    line += csv_field(values[i]);
  }
  write_line(line);
}

void CsvWriter::flush() { out_.flush(); }

void CsvWriter::write_line(const std::string& line) {
  out_ << line << '\n';
  if (autoflush_) out_.flush();
  GSIS_CHECK(out_.good(), std::format("CSV ファイルに書けません: {}", path_to_utf8(path_)));
}

}  // namespace gsis
