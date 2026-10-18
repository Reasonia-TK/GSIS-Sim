#include "core/paths.hpp"

#include <format>
#include <fstream>
#include <system_error>

#include "core/error.hpp"

namespace gsis {

std::filesystem::path path_from_utf8(std::string_view utf8) {
  std::u8string u8;
  u8.reserve(utf8.size());
  for (const char c : utf8) u8.push_back(static_cast<char8_t>(c));
  return std::filesystem::path(u8);
}

std::string path_to_utf8(const std::filesystem::path& path) {
  const std::u8string u8 = path.u8string();
  std::string out;
  out.reserve(u8.size());
  for (const char8_t c : u8) out.push_back(static_cast<char>(c));
  return out;
}

std::string read_file(const std::filesystem::path& path) {
  std::ifstream in(path, std::ios::binary);
  GSIS_CHECK(in.good(), std::format("ファイルを開けません: {}", path_to_utf8(path)));
  in.seekg(0, std::ios::end);
  const std::streamoff size = in.tellg();
  GSIS_CHECK(size >= 0, std::format("ファイルの大きさを取得できません: {}", path_to_utf8(path)));
  in.seekg(0, std::ios::beg);
  std::string bytes(static_cast<std::size_t>(size), '\0');
  if (size > 0) {
    in.read(bytes.data(), size);
  }
  // 途中で読み終わった場合(eof でも)は誤りにする
  GSIS_CHECK(in.gcount() == size, std::format("ファイルを最後まで読めません: {}", path_to_utf8(path)));
  return bytes;
}

void write_file(const std::filesystem::path& path, std::string_view bytes) {
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  GSIS_CHECK(out.good(), std::format("ファイルを作れません: {}", path_to_utf8(path)));
  out.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
  out.flush();
  GSIS_CHECK(out.good(), std::format("ファイルに書けません: {}", path_to_utf8(path)));
}

void ensure_parent_directory(const std::filesystem::path& path) {
  const std::filesystem::path parent = path.parent_path();
  if (parent.empty()) return;
  std::error_code ec;
  std::filesystem::create_directories(parent, ec);
  GSIS_CHECK(!ec, std::format("ディレクトリを作れません: {}({})", path_to_utf8(parent),
                              ec.message()));
}

}  // namespace gsis
