#include "core/error.hpp"

#include <string>
#include <utility>

namespace gsis {

Error::Error(const std::string& message, std::string location)
    : std::runtime_error(message), location_(std::move(location)) {}

namespace detail {
namespace {

std::string make_location(const char* file, int line) {
  // フルパスは長いので、ファイル名だけにする(文字コードに依存しないよう区切り文字で切る)
  const std::string path(file);
  const std::size_t pos = path.find_last_of("/\\");
  const std::string name = pos == std::string::npos ? path : path.substr(pos + 1);
  return name + ":" + std::to_string(line);
}

}  // namespace

void throw_check_failure(const char* condition, const std::string& message, const char* file,
                         int line) {
  std::string location = make_location(file, line);
  if (condition != nullptr) {
    location += " (";
    location += condition;
    location += ")";
  }
  throw Error(message, std::move(location));
}

void throw_assert_failure(const char* condition, const char* file, int line) {
  const std::string location = make_location(file, line);
  throw Error("内部エラー: 条件 " + std::string(condition) + " が成り立ちません", location);
}

}  // namespace detail
}  // namespace gsis
