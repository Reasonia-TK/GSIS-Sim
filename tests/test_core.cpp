// core: Vec3、JSON、パス、ロガー、エラー

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_string.hpp>
#include <filesystem>
#include <limits>
#include <string>
#include <vector>

#include "core/error.hpp"
#include "core/json.hpp"
#include "core/log.hpp"
#include "core/paths.hpp"
#include "core/timer.hpp"
#include "core/vec3.hpp"
#include "support/test_paths.hpp"

using namespace gsis;
using Catch::Approx;

TEST_CASE("Vec3 basic operations", "[core]") {
  const Vec3 a{1.0, 2.0, 3.0};
  const Vec3 b{-2.0, 0.5, 4.0};
  CHECK(dot(a, b) == Approx(-2.0 + 1.0 + 12.0));
  const Vec3 c = cross(a, b);
  CHECK(c.x == Approx(2.0 * 4.0 - 3.0 * 0.5));
  CHECK(c.y == Approx(3.0 * -2.0 - 1.0 * 4.0));
  CHECK(c.z == Approx(1.0 * 0.5 - 2.0 * -2.0));
  CHECK(dot(c, a) == Approx(0.0).margin(1e-14));
  CHECK(norm(Vec3{3.0, 4.0, 12.0}) == Approx(13.0));
  CHECK((a + b) == Vec3{-1.0, 2.5, 7.0});
  CHECK((2.0 * a) == Vec3{2.0, 4.0, 6.0});
  CHECK((a / 2.0) == Vec3{0.5, 1.0, 1.5});
  CHECK(a[0] == 1.0);
  CHECK(a[2] == 3.0);
  const Vec3f af = vec_cast<float>(a);
  CHECK(af.y == 2.0f);
  CHECK((2.0 * af).z == 6.0f);
  CHECK(norm(normalized(Vec3{0.0, 0.0, 5.0}) - Vec3{0.0, 0.0, 1.0}) < 1e-15);
  CHECK(normalized(Vec3{}) == Vec3{});
}

TEST_CASE("JSON parse and dump round trip", "[core][json]") {
  // raw \u6587\u5b57\u5217\u306e\u4e2d\u306e \uXXXX \u3092 MSVC 19.44 \u304c\u30e6\u30cb\u30d0\u30fc\u30b5\u30eb\u6587\u5b57\u540d\u3068\u3057\u3066\u6271\u3046\u306e\u3067\u3001\u901a\u5e38\u306e\u6587\u5b57\u5217\u3067\u66f8\u304f
  const std::string text =
      "{\n"
      "  \"format\": \"pmesh\", \"version\": 1, \"big\": 9007199254740993,\n"
      "  \"x\": -1.5e-3, \"flag\": true, \"none\": null,\n"
      "  \"arr\": [1, 2.0, \"s\\\"q\\\\\\n\\u00e9\\u65e5\"], \"obj\": {\"a\": [], \"b\": {}}\n"
      "}";
  const json::Value v = json::parse(text);
  REQUIRE(v.is_object());
  CHECK(v.at("format").as_string() == "pmesh");
  CHECK(v.at("version").is_int());
  CHECK(v.at("version").as_int() == 1);
  CHECK(v.at("big").as_int() == 9007199254740993LL);  // double にすると丸まる値
  CHECK(v.at("x").as_double() == -1.5e-3);
  CHECK(v.at("flag").as_bool());
  CHECK(v.at("none").is_null());
  const json::Array& arr = v.at("arr").as_array();
  REQUIRE(arr.size() == 3);
  CHECK(arr[0].is_int());
  CHECK(arr[1].type() == json::Value::Type::Double);
  CHECK(arr[2].as_string() == "s\"q\\\n\xC3\xA9\xE6\x97\xA5");  // é と 日 は UTF-8 になる
  CHECK(v.find("missing") == nullptr);
  CHECK_THROWS_AS(v.at("missing"), Error);
  CHECK_THROWS_AS(v.at("x").as_int(), Error);

  // 書いて読み直すと同じ値になる(1 行・字下げの両方)
  for (const int indent : {-1, 2}) {
    const json::Value w = json::parse(json::dump(v, indent));
    CHECK(json::dump(w) == json::dump(v));
  }
  CHECK(json::dump(json::Value(2.0)) == "2.0");
  CHECK(json::dump(json::Value(0.1)) == "0.1");
  CHECK(json::dump(json::Value(std::int64_t{-7})) == "-7");
}

TEST_CASE("JSON surrogate pairs and errors", "[core][json]") {
  CHECK(json::parse("\"\\ud83d\\ude00\"").as_string() == "\xF0\x9F\x98\x80");
  CHECK_THROWS_AS(json::parse("{\"a\": 1,}"), Error);
  CHECK_THROWS_AS(json::parse("{\"a\": 1} x"), Error);
  CHECK_THROWS_AS(json::parse("{\"a\": 1, \"a\": 2}"), Error);  // キーの重複
  CHECK_THROWS_AS(json::parse("[01]"), Error);
  CHECK_THROWS_AS(json::parse("[1.]"), Error);
  CHECK_THROWS_AS(json::parse("\"\\ud83d\""), Error);
  CHECK_THROWS_AS(json::parse("99999999999999999999"), Error);
  CHECK_THROWS_AS(json::dump(json::Value(std::numeric_limits<double>::infinity())), Error);
  try {
    json::parse("{\n  \"a\": tru\n}");
    FAIL("例外が出ませんでした");
  } catch (const Error& e) {
    CHECK_THAT(e.what(), Catch::Matchers::ContainsSubstring("2 行"));
  }
}

TEST_CASE("UTF-8 paths round trip", "[core]") {
  const std::string name = "日本語のディレクトリ/ファイル α.txt";
  const std::filesystem::path p = path_from_utf8(name);
  CHECK(path_to_utf8(p) == name);
  const std::filesystem::path file = test::output_dir() / path_from_utf8("日本語パス/テスト.bin");
  ensure_parent_directory(file);
  const std::string bytes("a\0b\xff", 4);
  write_file(file, bytes);
  CHECK(read_file(file) == bytes);
  CHECK_THROWS_AS(read_file(test::output_dir() / "存在しない.txt"), Error);
}

TEST_CASE("error macros carry message and location", "[core]") {
  // 条件を実行時の値にする(定数の条件は /W4 で C4127 になる)
  volatile int runtime_one = 1;
  const int one = runtime_one;
  try {
    GSIS_CHECK(one + one == 3, std::string("計算が合いません"));
    FAIL("例外が出ませんでした");
  } catch (const Error& e) {
    CHECK(std::string(e.what()) == "計算が合いません");
    CHECK_THAT(e.location(), Catch::Matchers::ContainsSubstring("test_core.cpp"));
  }
  CHECK_THROWS_AS([&] { GSIS_ASSERT(one == 2); }(), Error);
  CHECK_NOTHROW([&] { GSIS_CHECK(one == 1, "使われない"); }());
}

TEST_CASE("logger sink, levels and warning count", "[core]") {
  std::vector<std::string> lines;
  set_log_sink([&](LogLevel, std::string_view msg) { lines.emplace_back(msg); });
  const LogLevel old = log_level();
  set_log_level(LogLevel::Info);
  const std::size_t warnings_before = warning_count();
  log_debug("見えない {}", 1);
  log_info("情報 {}", 2);
  log_warn("警告 {}", 3);
  set_log_level(LogLevel::Error);
  log_warn("数えるが出さない");
  set_log_level(old);
  set_log_sink({});
  REQUIRE(lines.size() == 2);
  CHECK(lines[0] == "情報 2");
  CHECK(lines[1] == "警告 3");
  CHECK(warning_count() == warnings_before + 2);
}

TEST_CASE("timing table accumulates", "[core]") {
  TimingTable t;
  t.add("a", 1.0);
  t.add("b", 0.5);
  t.add("a", 2.0);
  CHECK(t.total("a") == Approx(3.0));
  CHECK(t.total("c") == 0.0);
  REQUIRE(t.entries().size() == 2);
  CHECK(t.entries()[0].calls == 2);
  {
    ScopedTimer s(t, "scoped");
  }
  CHECK(t.entries().size() == 3);
  CHECK(t.total("scoped") >= 0.0);
  Timer timer;
  CHECK(timer.seconds() >= 0.0);
}
