#pragma once
// エラー処理: 例外 gsis::Error と検査マクロ。
//
//   GSIS_CHECK(cond, msg)  入力や外部状態の検査。偽なら msg を持つ gsis::Error を投げる。
//                          msg は失敗したときだけ評価される(std::format を書いてよい)。
//   GSIS_ASSERT(cond)      内部の不変条件。偽ならプログラムの誤りとして gsis::Error を投げる。
//   GSIS_FAIL(msg)         無条件に gsis::Error を投げる。
//
// what() は利用者向けの文、location() は発生箇所(ファイル:行)。

#include <stdexcept>
#include <string>

namespace gsis {

class Error : public std::runtime_error {
 public:
  explicit Error(const std::string& message, std::string location = {});
  const std::string& location() const noexcept { return location_; }

 private:
  std::string location_;
};

namespace detail {
[[noreturn]] void throw_check_failure(const char* condition, const std::string& message,
                                      const char* file, int line);
[[noreturn]] void throw_assert_failure(const char* condition, const char* file, int line);
}  // namespace detail

}  // namespace gsis

#define GSIS_CHECK(cond, msg)                                                       \
  do {                                                                              \
    if (!(cond)) [[unlikely]] {                                                     \
      ::gsis::detail::throw_check_failure(#cond, (msg), __FILE__, __LINE__);        \
    }                                                                               \
  } while (false)

#define GSIS_ASSERT(cond)                                                           \
  do {                                                                              \
    if (!(cond)) [[unlikely]] {                                                     \
      ::gsis::detail::throw_assert_failure(#cond, __FILE__, __LINE__);              \
    }                                                                               \
  } while (false)

#define GSIS_FAIL(msg) ::gsis::detail::throw_check_failure(nullptr, (msg), __FILE__, __LINE__)
