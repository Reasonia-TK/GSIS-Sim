#pragma once
// 最小限の JSON(.pmesh のヘッダ、要約 JSON の出力用)。
// - 数値は整数(int64)と実数(double)を区別して持つ。整数の字句(小数点・指数なし)は Int になる。
// - オブジェクトはキーの挿入順を保つ。同じキーが重複したらエラーにする。
// - 文字列は UTF-8 として扱う(\uXXXX のエスケープは UTF-8 に変換する)。

#include <cstdint>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

namespace gsis::json {

class Value;
using Array = std::vector<Value>;
using Object = std::vector<std::pair<std::string, Value>>;

class Value {
 public:
  enum class Type { Null, Bool, Int, Double, String, Array, Object };

  Value() = default;
  Value(std::nullptr_t) {}
  Value(bool b) : data_(b) {}
  template <class I>
    requires(std::is_integral_v<I> && !std::is_same_v<I, bool>)
  Value(I i) : data_(static_cast<std::int64_t>(i)) {}
  Value(double d) : data_(d) {}
  Value(const char* s) : data_(std::string(s)) {}
  Value(std::string s) : data_(std::move(s)) {}
  Value(std::string_view s) : data_(std::string(s)) {}
  Value(Array a) : data_(std::move(a)) {}
  Value(Object o) : data_(std::move(o)) {}

  Type type() const { return static_cast<Type>(data_.index()); }
  bool is_null() const { return type() == Type::Null; }
  bool is_bool() const { return type() == Type::Bool; }
  bool is_int() const { return type() == Type::Int; }
  bool is_number() const { return type() == Type::Int || type() == Type::Double; }
  bool is_string() const { return type() == Type::String; }
  bool is_array() const { return type() == Type::Array; }
  bool is_object() const { return type() == Type::Object; }

  // 型が違えば gsis::Error を投げる。as_double は整数も受け付ける。
  bool as_bool() const;
  std::int64_t as_int() const;
  double as_double() const;
  const std::string& as_string() const;
  const Array& as_array() const;
  Array& as_array();
  const Object& as_object() const;
  Object& as_object();

  // オブジェクトのキーを探す(無ければ nullptr)
  const Value* find(std::string_view key) const;
  // オブジェクトのキー(無ければ gsis::Error)
  const Value& at(std::string_view key) const;
  // オブジェクトに値を設定する(無ければ末尾に追加)。null ならオブジェクトにしてから設定する。
  Value& set(std::string_view key, Value value);
  // 配列に追加する。null なら配列にしてから追加する。
  void push_back(Value value);

  static const char* type_name(Type type);

 private:
  std::variant<std::monostate, bool, std::int64_t, double, std::string, Array, Object> data_;
};

// JSON 文書を解析する。誤りは行・列つきの gsis::Error になる。
Value parse(std::string_view text);

// 文字列にする。indent < 0 なら 1 行、0 以上なら改行と字下げを入れる。
// 実数は往復で値が変わらない最短の表記にする(整数値の実数には ".0" を付ける)。
// 有限でない実数は JSON で表せないので gsis::Error になる。
std::string dump(const Value& value, int indent = -1);

}  // namespace gsis::json
