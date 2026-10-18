#include "core/json.hpp"

#include <charconv>
#include <cmath>
#include <format>
#include <system_error>

#include "core/error.hpp"

namespace gsis::json {

// ---------------------------------------------------------------------------
// Value
// ---------------------------------------------------------------------------

const char* Value::type_name(Type type) {
  switch (type) {
    case Type::Null:
      return "null";
    case Type::Bool:
      return "真偽値";
    case Type::Int:
      return "整数";
    case Type::Double:
      return "実数";
    case Type::String:
      return "文字列";
    case Type::Array:
      return "配列";
    case Type::Object:
      return "オブジェクト";
  }
  return "?";
}

namespace {
[[noreturn]] void type_error(Value::Type expected, Value::Type actual) {
  throw Error(std::format("JSON の値の型が違います(期待: {}、実際: {})", Value::type_name(expected),
                          Value::type_name(actual)));
}
}  // namespace

bool Value::as_bool() const {
  if (!is_bool()) type_error(Type::Bool, type());
  return std::get<bool>(data_);
}

std::int64_t Value::as_int() const {
  if (!is_int()) type_error(Type::Int, type());
  return std::get<std::int64_t>(data_);
}

double Value::as_double() const {
  if (is_int()) return static_cast<double>(std::get<std::int64_t>(data_));
  if (type() != Type::Double) type_error(Type::Double, type());
  return std::get<double>(data_);
}

const std::string& Value::as_string() const {
  if (!is_string()) type_error(Type::String, type());
  return std::get<std::string>(data_);
}

const Array& Value::as_array() const {
  if (!is_array()) type_error(Type::Array, type());
  return std::get<Array>(data_);
}

Array& Value::as_array() {
  if (!is_array()) type_error(Type::Array, type());
  return std::get<Array>(data_);
}

const Object& Value::as_object() const {
  if (!is_object()) type_error(Type::Object, type());
  return std::get<Object>(data_);
}

Object& Value::as_object() {
  if (!is_object()) type_error(Type::Object, type());
  return std::get<Object>(data_);
}

const Value* Value::find(std::string_view key) const {
  for (const auto& [k, v] : as_object()) {
    if (k == key) return &v;
  }
  return nullptr;
}

const Value& Value::at(std::string_view key) const {
  const Value* v = find(key);
  if (v == nullptr) {
    throw Error(std::format("JSON にキー \"{}\" がありません", key));
  }
  return *v;
}

Value& Value::set(std::string_view key, Value value) {
  if (is_null()) data_ = Object{};
  Object& obj = as_object();
  for (auto& [k, v] : obj) {
    if (k == key) {
      v = std::move(value);
      return v;
    }
  }
  obj.emplace_back(std::string(key), std::move(value));
  return obj.back().second;
}

void Value::push_back(Value value) {
  if (is_null()) data_ = Array{};
  as_array().push_back(std::move(value));
}

// ---------------------------------------------------------------------------
// 解析
// ---------------------------------------------------------------------------

namespace {

constexpr int kMaxDepth = 256;

class Parser {
 public:
  explicit Parser(std::string_view text) : s_(text) {}

  Value parse_document() {
    skip_ws();
    Value v = parse_value(0);
    skip_ws();
    if (pos_ != s_.size()) fail("値の後に余分な文字があります");
    return v;
  }

 private:
  std::string_view s_;
  std::size_t pos_ = 0;

  [[noreturn]] void fail(std::string_view what) const {
    std::size_t line = 1;
    std::size_t col = 1;
    for (std::size_t i = 0; i < pos_ && i < s_.size(); ++i) {
      if (s_[i] == '\n') {
        ++line;
        col = 1;
      } else {
        ++col;
      }
    }
    throw Error(std::format("JSON の解析に失敗しました({} 行 {} 列): {}", line, col, what));
  }

  bool at_end() const { return pos_ >= s_.size(); }
  char peek() const { return s_[pos_]; }

  void skip_ws() {
    while (!at_end()) {
      const char c = peek();
      if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
        ++pos_;
      } else {
        break;
      }
    }
  }

  void expect(char c) {
    if (at_end() || peek() != c) fail(std::format("'{}' が必要です", c));
    ++pos_;
  }

  void expect_literal(std::string_view lit) {
    if (s_.substr(pos_, lit.size()) != lit) fail(std::format("'{}' が必要です", lit));
    pos_ += lit.size();
  }

  Value parse_value(int depth) {
    if (depth > kMaxDepth) fail("入れ子が深すぎます");
    if (at_end()) fail("値がありません");
    const char c = peek();
    switch (c) {
      case '{':
        return parse_object(depth);
      case '[':
        return parse_array(depth);
      case '"':
        return Value(parse_string());
      case 't':
        expect_literal("true");
        return Value(true);
      case 'f':
        expect_literal("false");
        return Value(false);
      case 'n':
        expect_literal("null");
        return Value(nullptr);
      default:
        if (c == '-' || (c >= '0' && c <= '9')) return parse_number();
        fail(std::format("予期しない文字 '{}' があります", c));
    }
  }

  Value parse_object(int depth) {
    expect('{');
    Object obj;
    skip_ws();
    if (!at_end() && peek() == '}') {
      ++pos_;
      return Value(std::move(obj));
    }
    while (true) {
      skip_ws();
      if (at_end() || peek() != '"') fail("オブジェクトのキー(文字列)が必要です");
      std::string key = parse_string();
      for (const auto& kv : obj) {
        if (kv.first == key) fail(std::format("キー \"{}\" が重複しています", key));
      }
      skip_ws();
      expect(':');
      skip_ws();
      Value v = parse_value(depth + 1);
      obj.emplace_back(std::move(key), std::move(v));
      skip_ws();
      if (at_end()) fail("オブジェクトが閉じていません");
      if (peek() == ',') {
        ++pos_;
        continue;
      }
      if (peek() == '}') {
        ++pos_;
        break;
      }
      fail("',' か '}' が必要です");
    }
    return Value(std::move(obj));
  }

  Value parse_array(int depth) {
    expect('[');
    Array arr;
    skip_ws();
    if (!at_end() && peek() == ']') {
      ++pos_;
      return Value(std::move(arr));
    }
    while (true) {
      skip_ws();
      arr.push_back(parse_value(depth + 1));
      skip_ws();
      if (at_end()) fail("配列が閉じていません");
      if (peek() == ',') {
        ++pos_;
        continue;
      }
      if (peek() == ']') {
        ++pos_;
        break;
      }
      fail("',' か ']' が必要です");
    }
    return Value(std::move(arr));
  }

  unsigned parse_hex4() {
    if (pos_ + 4 > s_.size()) fail("\\u の後に 16 進 4 桁が必要です");
    unsigned v = 0;
    for (int i = 0; i < 4; ++i) {
      const char c = s_[pos_++];
      v <<= 4;
      if (c >= '0' && c <= '9') {
        v |= static_cast<unsigned>(c - '0');
      } else if (c >= 'a' && c <= 'f') {
        v |= static_cast<unsigned>(c - 'a' + 10);
      } else if (c >= 'A' && c <= 'F') {
        v |= static_cast<unsigned>(c - 'A' + 10);
      } else {
        fail("\\u の後に 16 進 4 桁が必要です");
      }
    }
    return v;
  }

  static void append_utf8(std::string& out, unsigned cp) {
    if (cp < 0x80) {
      out += static_cast<char>(cp);
    } else if (cp < 0x800) {
      out += static_cast<char>(0xC0 | (cp >> 6));
      out += static_cast<char>(0x80 | (cp & 0x3F));
    } else if (cp < 0x10000) {
      out += static_cast<char>(0xE0 | (cp >> 12));
      out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
      out += static_cast<char>(0x80 | (cp & 0x3F));
    } else {
      out += static_cast<char>(0xF0 | (cp >> 18));
      out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
      out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
      out += static_cast<char>(0x80 | (cp & 0x3F));
    }
  }

  std::string parse_string() {
    expect('"');
    std::string out;
    while (true) {
      if (at_end()) fail("文字列が閉じていません");
      const char c = s_[pos_++];
      if (c == '"') break;
      if (static_cast<unsigned char>(c) < 0x20) fail("文字列に制御文字があります");
      if (c != '\\') {
        out += c;
        continue;
      }
      if (at_end()) fail("文字列が閉じていません");
      const char e = s_[pos_++];
      switch (e) {
        case '"':
          out += '"';
          break;
        case '\\':
          out += '\\';
          break;
        case '/':
          out += '/';
          break;
        case 'b':
          out += '\b';
          break;
        case 'f':
          out += '\f';
          break;
        case 'n':
          out += '\n';
          break;
        case 'r':
          out += '\r';
          break;
        case 't':
          out += '\t';
          break;
        case 'u': {
          unsigned cp = parse_hex4();
          if (cp >= 0xD800 && cp <= 0xDBFF) {
            // サロゲートペア
            if (pos_ + 2 > s_.size() || s_[pos_] != '\\' || s_[pos_ + 1] != 'u') {
              fail("サロゲートペアの後半がありません");
            }
            pos_ += 2;
            const unsigned lo = parse_hex4();
            if (lo < 0xDC00 || lo > 0xDFFF) fail("サロゲートペアの後半が不正です");
            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
          } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
            fail("対になっていないサロゲートがあります");
          }
          append_utf8(out, cp);
          break;
        }
        default:
          fail(std::format("不正なエスケープ \\{} があります", e));
      }
    }
    return out;
  }

  Value parse_number() {
    const std::size_t start = pos_;
    bool is_integer = true;
    if (peek() == '-') ++pos_;
    if (at_end()) fail("数値が不正です");
    if (peek() == '0') {
      ++pos_;
    } else if (peek() >= '1' && peek() <= '9') {
      while (!at_end() && peek() >= '0' && peek() <= '9') ++pos_;
    } else {
      fail("数値が不正です");
    }
    if (!at_end() && peek() == '.') {
      is_integer = false;
      ++pos_;
      if (at_end() || peek() < '0' || peek() > '9') fail("小数点の後に数字が必要です");
      while (!at_end() && peek() >= '0' && peek() <= '9') ++pos_;
    }
    if (!at_end() && (peek() == 'e' || peek() == 'E')) {
      is_integer = false;
      ++pos_;
      if (!at_end() && (peek() == '+' || peek() == '-')) ++pos_;
      if (at_end() || peek() < '0' || peek() > '9') fail("指数に数字が必要です");
      while (!at_end() && peek() >= '0' && peek() <= '9') ++pos_;
    }
    const char* first = s_.data() + start;
    const char* last = s_.data() + pos_;
    if (is_integer) {
      std::int64_t v = 0;
      const auto [ptr, ec] = std::from_chars(first, last, v);
      if (ec == std::errc::result_out_of_range) fail("整数が 64 ビットの範囲を超えています");
      if (ec != std::errc() || ptr != last) fail("数値が不正です");
      return Value(v);
    }
    double d = 0.0;
    const auto [ptr, ec] = std::from_chars(first, last, d);
    if (ec == std::errc::result_out_of_range) fail("実数が範囲を超えています");
    if (ec != std::errc() || ptr != last) fail("数値が不正です");
    return Value(d);
  }
};

}  // namespace

Value parse(std::string_view text) {
  // UTF-8 の BOM は読み飛ばす
  if (text.size() >= 3 && static_cast<unsigned char>(text[0]) == 0xEF &&
      static_cast<unsigned char>(text[1]) == 0xBB && static_cast<unsigned char>(text[2]) == 0xBF) {
    text.remove_prefix(3);
  }
  return Parser(text).parse_document();
}

// ---------------------------------------------------------------------------
// 文字列化
// ---------------------------------------------------------------------------

namespace {

void dump_string(const std::string& s, std::string& out) {
  out += '"';
  for (const char c : s) {
    switch (c) {
      case '"':
        out += "\\\"";
        break;
      case '\\':
        out += "\\\\";
        break;
      case '\b':
        out += "\\b";
        break;
      case '\f':
        out += "\\f";
        break;
      case '\n':
        out += "\\n";
        break;
      case '\r':
        out += "\\r";
        break;
      case '\t':
        out += "\\t";
        break;
      default:
        if (static_cast<unsigned char>(c) < 0x20) {
          out += std::format("\\u{:04x}", static_cast<unsigned>(static_cast<unsigned char>(c)));
        } else {
          out += c;
        }
    }
  }
  out += '"';
}

void dump_double(double d, std::string& out) {
  if (!std::isfinite(d)) {
    throw Error("有限でない実数は JSON に書けません");
  }
  char buf[64];
  const auto [ptr, ec] = std::to_chars(buf, buf + sizeof(buf), d);
  GSIS_ASSERT(ec == std::errc());
  std::string_view text(buf, static_cast<std::size_t>(ptr - buf));
  out += text;
  // 整数の字句になったら実数だと分かるように ".0" を付ける
  if (text.find_first_of(".eE") == std::string_view::npos) out += ".0";
}

void newline(std::string& out, int indent, int level) {
  if (indent < 0) return;
  out += '\n';
  out.append(static_cast<std::size_t>(indent) * static_cast<std::size_t>(level), ' ');
}

void dump_impl(const Value& v, std::string& out, int indent, int level) {
  switch (v.type()) {
    case Value::Type::Null:
      out += "null";
      break;
    case Value::Type::Bool:
      out += v.as_bool() ? "true" : "false";
      break;
    case Value::Type::Int:
      out += std::to_string(v.as_int());
      break;
    case Value::Type::Double:
      dump_double(v.as_double(), out);
      break;
    case Value::Type::String:
      dump_string(v.as_string(), out);
      break;
    case Value::Type::Array: {
      const Array& arr = v.as_array();
      if (arr.empty()) {
        out += "[]";
        break;
      }
      // 数値だけの配列は 1 行に書く(shape などを読みやすくする)
      bool scalars = true;
      for (const Value& e : arr) {
        if (!e.is_number() && !e.is_bool() && !e.is_null()) scalars = false;
      }
      out += '[';
      for (std::size_t i = 0; i < arr.size(); ++i) {
        if (i > 0) out += (scalars && indent >= 0) ? ", " : ",";
        if (!scalars) newline(out, indent, level + 1);
        dump_impl(arr[i], out, indent, level + 1);
      }
      if (!scalars) newline(out, indent, level);
      out += ']';
      break;
    }
    case Value::Type::Object: {
      const Object& obj = v.as_object();
      if (obj.empty()) {
        out += "{}";
        break;
      }
      out += '{';
      for (std::size_t i = 0; i < obj.size(); ++i) {
        if (i > 0) out += ',';
        newline(out, indent, level + 1);
        dump_string(obj[i].first, out);
        out += indent < 0 ? ":" : ": ";
        dump_impl(obj[i].second, out, indent, level + 1);
      }
      newline(out, indent, level);
      out += '}';
      break;
    }
  }
}

}  // namespace

std::string dump(const Value& value, int indent) {
  std::string out;
  dump_impl(value, out, indent, 0);
  return out;
}

}  // namespace gsis::json
