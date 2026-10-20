#include "config/case_config.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <format>
#include <set>
#include <toml++/toml.hpp>
#include <utility>

#include "core/error.hpp"
#include "core/log.hpp"
#include "core/math.hpp"
#include "core/paths.hpp"

namespace gsis {

std::string_view to_string(SolverMode mode) {
  switch (mode) {
    case SolverMode::Gsis:
      return "gsis";
    case SolverMode::Cis:
      return "cis";
    case SolverMode::Nsf:
      return "nsf";
  }
  return "?";
}

std::string_view to_string(BoundaryType type) {
  switch (type) {
    case BoundaryType::MaxwellWall:
      return "maxwell_wall";
    case BoundaryType::MassFlowInlet:
      return "mass_flow_inlet";
    case BoundaryType::DiffuseWall:
      return "diffuse_wall";
    case BoundaryType::PressureInlet:
      return "pressure_inlet";
    case BoundaryType::PressureOutlet:
      return "pressure_outlet";
    case BoundaryType::Vacuum:
      return "vacuum";
    case BoundaryType::Symmetry:
      return "symmetry";
    case BoundaryType::PeriodicSlab:
      return "periodic_slab";
  }
  return "?";
}

// ---------------------------------------------------------------------------
// ModuleSection
// ---------------------------------------------------------------------------

const ParamValue* ModuleSection::find(std::string_view key) const {
  const auto it = values.find(key);
  if (it == values.end()) return nullptr;
  used_.emplace(key);
  return &it->second;
}

bool ModuleSection::contains(std::string_view key) const { return values.find(key) != values.end(); }

double ModuleSection::number(std::string_view key, double fallback) const {
  const ParamValue* v = find(key);
  if (v == nullptr) return fallback;
  if (const double* d = std::get_if<double>(v)) return *d;
  if (const long long* i = std::get_if<long long>(v)) return static_cast<double>(*i);
  GSIS_FAIL(std::format("[{}].{} は数値でなければなりません", name_, key));
}

long long ModuleSection::integer(std::string_view key, long long fallback) const {
  const ParamValue* v = find(key);
  if (v == nullptr) return fallback;
  if (const long long* i = std::get_if<long long>(v)) return *i;
  GSIS_FAIL(std::format("[{}].{} は整数でなければなりません", name_, key));
}

std::string ModuleSection::string(std::string_view key, std::string fallback) const {
  const ParamValue* v = find(key);
  if (v == nullptr) return fallback;
  if (const std::string* s = std::get_if<std::string>(v)) return *s;
  GSIS_FAIL(std::format("[{}].{} は文字列でなければなりません", name_, key));
}

bool ModuleSection::boolean(std::string_view key, bool fallback) const {
  const ParamValue* v = find(key);
  if (v == nullptr) return fallback;
  if (const bool* b = std::get_if<bool>(v)) return *b;
  GSIS_FAIL(std::format("[{}].{} は真偽値でなければなりません", name_, key));
}

void ModuleSection::finish() const {
  std::string unknown;
  for (const auto& [k, v] : values) {
    if (!used_.contains(k)) unknown += (unknown.empty() ? "" : ", ") + k;
  }
  GSIS_CHECK(unknown.empty(), std::format("[{}] に知らないキーがあります: {}", name_, unknown));
}

const ModuleSection& CaseConfig::module_section(std::string_view name) const {
  static const ModuleSection kEmpty;
  const auto it = module_sections.find(name);
  return it == module_sections.end() ? kEmpty : it->second;
}

double GasSection::specific_gas_constant() const { return kBoltzmann / molecular_mass; }

double GasSection::viscosity(double T) const { return mu_ref * std::pow(T / T_ref, omega); }

std::filesystem::path CaseConfig::mesh_path() const {
  if (case_info.mesh.is_absolute() || source.empty()) return case_info.mesh;
  return source.parent_path() / case_info.mesh;
}

const BoundarySpec* CaseConfig::find_boundary(std::string_view patch) const {
  for (const BoundarySpec& b : boundaries) {
    if (b.patch == patch) return &b;
  }
  return nullptr;
}

namespace {

// ---------------------------------------------------------------------------
// TOML の値を読む補助
// ---------------------------------------------------------------------------

std::string where(const toml::node& node) {
  const toml::source_region& src = node.source();
  if (src.begin.line == 0) return "位置不明";
  return std::format("{} 行 {} 列", src.begin.line, src.begin.column);
}

std::string_view type_label(const toml::node& node) {
  switch (node.type()) {
    case toml::node_type::string:
      return "文字列";
    case toml::node_type::integer:
      return "整数";
    case toml::node_type::floating_point:
      return "実数";
    case toml::node_type::boolean:
      return "真偽値";
    case toml::node_type::array:
      return "配列";
    case toml::node_type::table:
      return "表";
    default:
      return "日付・時刻";
  }
}

// 1 つの表を読む。読んだキーを覚えておき、finish() で知らないキーをエラーにする。
class TableReader {
 public:
  TableReader(const toml::table& table, std::string name) : table_(table), name_(std::move(name)) {}

  const toml::node* find(std::string_view key) {
    known_.insert(std::string(key));
    return table_.get(key);
  }

  const toml::node& require(std::string_view key) {
    const toml::node* node = find(key);
    GSIS_CHECK(node != nullptr, std::format("[{}] に {} がありません", name_, key));
    return *node;
  }

  std::string get_string(std::string_view key) { return to_string_value(require(key), key); }

  std::optional<std::string> opt_string(std::string_view key) {
    const toml::node* node = find(key);
    if (node == nullptr) return std::nullopt;
    return to_string_value(*node, key);
  }

  double get_number(std::string_view key) { return to_number(require(key), key); }

  std::optional<double> opt_number(std::string_view key) {
    const toml::node* node = find(key);
    if (node == nullptr) return std::nullopt;
    return to_number(*node, key);
  }

  std::optional<long long> opt_integer(std::string_view key) {
    const toml::node* node = find(key);
    if (node == nullptr) return std::nullopt;
    const auto* v = node->as_integer();
    GSIS_CHECK(v != nullptr, type_error(key, *node, "整数"));
    return v->get();
  }

  std::optional<bool> opt_boolean(std::string_view key) {
    const toml::node* node = find(key);
    if (node == nullptr) return std::nullopt;
    const auto* v = node->as_boolean();
    GSIS_CHECK(v != nullptr, type_error(key, *node, "真偽値"));
    return v->get();
  }

  // 要素 3 個の数値の配列
  Vec3 get_vec3(std::string_view key) { return to_vec3(require(key), key); }

  std::optional<Vec3> opt_vec3(std::string_view key) {
    const toml::node* node = find(key);
    if (node == nullptr) return std::nullopt;
    return to_vec3(*node, key);
  }

  // 要素 3 個の整数の配列
  std::array<int, 3> get_int3(std::string_view key) {
    const toml::node& node = require(key);
    const toml::array* arr = node.as_array();
    GSIS_CHECK(arr != nullptr && arr->size() == 3, type_error(key, node, "整数 3 個の配列"));
    std::array<int, 3> out{};
    for (std::size_t i = 0; i < 3; ++i) {
      const auto* v = (*arr)[i].as_integer();
      GSIS_CHECK(v != nullptr, type_error(key, node, "整数 3 個の配列"));
      const long long x = v->get();
      GSIS_CHECK(x >= 1 && x <= 100000,
                 std::format("{} の要素 {} が範囲外です({})", full(key), x, where(node)));
      out[i] = static_cast<int>(x);
    }
    return out;
  }

  // [[lo, hi], [lo, hi], [lo, hi]]
  std::array<std::array<double, 2>, 3> get_range3(std::string_view key) {
    const toml::node& node = require(key);
    const std::string expected = "数値 2 個の配列を 3 個並べた配列 [[lo, hi], [lo, hi], [lo, hi]]";
    const toml::array* arr = node.as_array();
    GSIS_CHECK(arr != nullptr && arr->size() == 3, type_error(key, node, expected));
    std::array<std::array<double, 2>, 3> out{};
    for (std::size_t i = 0; i < 3; ++i) {
      const toml::array* pair = (*arr)[i].as_array();
      GSIS_CHECK(pair != nullptr && pair->size() == 2, type_error(key, node, expected));
      for (std::size_t j = 0; j < 2; ++j) {
        out[i][j] = to_number((*pair)[j], key);
      }
    }
    return out;
  }

  // 読まなかったキーがあればエラーにする
  void finish() const {
    for (auto&& [k, v] : table_) {
      const std::string key(k.str());
      if (!known_.contains(key)) {
        std::string allowed;
        for (const std::string& a : known_) {
          if (!allowed.empty()) allowed += ", ";
          allowed += a;
        }
        throw Error(std::format("[{}] に知らないキー {} があります({})。使えるキー: {}", name_, key,
                                where(v), allowed));
      }
    }
  }

  std::string full(std::string_view key) const { return name_ + "." + std::string(key); }

 private:
  const toml::table& table_;
  std::string name_;
  std::set<std::string> known_;

  std::string type_error(std::string_view key, const toml::node& node,
                         std::string_view expected) const {
    return std::format("{} は{}でなければなりません(実際は{}、{})", full(key), expected,
                       type_label(node), where(node));
  }

  std::string to_string_value(const toml::node& node, std::string_view key) const {
    const auto* s = node.as_string();
    GSIS_CHECK(s != nullptr, type_error(key, node, "文字列"));
    return s->get();
  }

  double to_number(const toml::node& node, std::string_view key) const {
    if (const auto* i = node.as_integer()) return static_cast<double>(i->get());
    const auto* f = node.as_floating_point();
    GSIS_CHECK(f != nullptr, type_error(key, node, "数値"));
    const double x = f->get();
    GSIS_CHECK(std::isfinite(x), std::format("{} が有限の値ではありません({})", full(key), where(node)));
    return x;
  }

  Vec3 to_vec3(const toml::node& node, std::string_view key) const {
    const toml::array* arr = node.as_array();
    GSIS_CHECK(arr != nullptr && arr->size() == 3, type_error(key, node, "数値 3 個の配列"));
    return Vec3{to_number((*arr)[0], key), to_number((*arr)[1], key), to_number((*arr)[2], key)};
  }
};

const toml::table& as_table(const toml::node& node, std::string_view name) {
  const toml::table* t = node.as_table();
  GSIS_CHECK(t != nullptr, std::format("[{}] は表でなければなりません({})", name, where(node)));
  return *t;
}

void require_positive(double value, const std::string& name) {
  GSIS_CHECK(value > 0.0, std::format("{} は正でなければなりません(値: {})", name, value));
}

// ---------------------------------------------------------------------------
// 節ごとの解析
// ---------------------------------------------------------------------------

void parse_case_section(const toml::node& node, CaseConfig& cfg) {
  TableReader r(as_table(node, "case"), "case");
  cfg.case_info.name = r.get_string("name");
  GSIS_CHECK(!cfg.case_info.name.empty(), "case.name が空です");
  const std::string mesh = r.get_string("mesh");
  GSIS_CHECK(!mesh.empty(), "case.mesh が空です");
  cfg.case_info.mesh = path_from_utf8(mesh);
  const std::string mode = r.opt_string("mode").value_or("gsis");
  if (mode == "gsis") {
    cfg.case_info.mode = SolverMode::Gsis;
  } else if (mode == "cis") {
    cfg.case_info.mode = SolverMode::Cis;
  } else if (mode == "nsf") {
    cfg.case_info.mode = SolverMode::Nsf;
  } else {
    GSIS_FAIL(std::format("case.mode は \"gsis\"・\"cis\"・\"nsf\" です(値: \"{}\")", mode));
  }
  r.finish();
}

void parse_gas_section(const toml::node& node, CaseConfig& cfg) {
  TableReader r(as_table(node, "gas"), "gas");
  GasSection& g = cfg.gas;
  g.molecular_mass = r.get_number("molecular_mass");
  g.mu_ref = r.get_number("mu_ref");
  g.T_ref = r.get_number("T_ref");
  g.omega = r.get_number("omega");
  r.finish();
  require_positive(g.molecular_mass, "gas.molecular_mass");
  require_positive(g.mu_ref, "gas.mu_ref");
  require_positive(g.T_ref, "gas.T_ref");
  require_positive(g.omega, "gas.omega");
  GSIS_CHECK(g.molecular_mass < 1e-20,
             std::format("gas.molecular_mass = {} kg は分子の質量として大きすぎます(単位は kg)",
                         g.molecular_mass));
  if (g.omega < 0.5 || g.omega > 1.0) {
    log_warn("gas.omega = {} は通常の範囲 [0.5, 1] の外です", g.omega);
  }
}

void parse_conditions_section(const toml::node& node, CaseConfig& cfg) {
  TableReader r(as_table(node, "conditions"), "conditions");
  ConditionsSection& c = cfg.conditions;
  c.L_ref = r.get_number("L_ref");
  c.T0 = r.get_number("T0");
  c.delta_rp = r.opt_number("delta_rp");
  c.Kn = r.opt_number("Kn");
  c.p_ref = r.opt_number("p_ref");
  r.finish();
  require_positive(c.L_ref, "conditions.L_ref");
  require_positive(c.T0, "conditions.T0");
  const int given = int(c.delta_rp.has_value()) + int(c.Kn.has_value()) + int(c.p_ref.has_value());
  GSIS_CHECK(given == 1, "conditions には delta_rp・Kn・p_ref のどれか 1 つだけを書いてください");
  if (c.delta_rp) require_positive(*c.delta_rp, "conditions.delta_rp");
  if (c.Kn) require_positive(*c.Kn, "conditions.Kn");
  if (c.p_ref) require_positive(*c.p_ref, "conditions.p_ref");
}

BoundaryType parse_boundary_type(const std::string& s, const std::string& name) {
  if (s == "maxwell_wall") return BoundaryType::MaxwellWall;
  if (s == "mass_flow_inlet") return BoundaryType::MassFlowInlet;
  if (s == "diffuse_wall") return BoundaryType::DiffuseWall;
  if (s == "pressure_inlet") return BoundaryType::PressureInlet;
  if (s == "pressure_outlet") return BoundaryType::PressureOutlet;
  if (s == "vacuum") return BoundaryType::Vacuum;
  if (s == "symmetry") return BoundaryType::Symmetry;
  if (s == "periodic_slab") return BoundaryType::PeriodicSlab;
  GSIS_FAIL(std::format("{}.type = \"{}\" は知らない境界条件です(diffuse_wall, pressure_inlet, "
                        "pressure_outlet, vacuum, symmetry, periodic_slab のいずれか)",
                        name, s));
}

void parse_boundary_array(const toml::node& node, CaseConfig& cfg) {
  const toml::array* arr = node.as_array();
  GSIS_CHECK(arr != nullptr && arr->is_array_of_tables(),
             std::format("boundary は表の配列 [[boundary]] でなければなりません({})", where(node)));
  for (std::size_t i = 0; i < arr->size(); ++i) {
    const std::string name = std::format("boundary[{}]", i);
    TableReader r(*(*arr)[i].as_table(), name);
    BoundarySpec b;
    b.patch = r.get_string("patch");
    GSIS_CHECK(!b.patch.empty(), std::format("{}.patch が空です", name));
    b.type = parse_boundary_type(r.get_string("type"), name);
    switch (b.type) {
      case BoundaryType::MaxwellWall:
        b.accommodation = r.get_number("accommodation");
        GSIS_CHECK(b.accommodation > 0.0 && b.accommodation <= 1.0, name + ".accommodation は 0 より大きく 1 以下です");
        [[fallthrough]];
      case BoundaryType::DiffuseWall:
        b.temperature = r.get_number("temperature");
        b.velocity = r.opt_vec3("velocity");
        break;
      case BoundaryType::MassFlowInlet:
      case BoundaryType::PressureInlet: {
        b.temperature = r.get_number("temperature");
        if (b.type == BoundaryType::MassFlowInlet) {
          b.mass_flow = r.get_number("mass_flow");
          require_positive(*b.mass_flow, name + ".mass_flow");
        } else {
          b.pressure = r.opt_number("pressure");
        }
        const std::string tv = r.opt_string("tangential_velocity").value_or("extrapolate");
        GSIS_CHECK(tv == "extrapolate" || tv == "zero",
                   std::format("{}.tangential_velocity は \"extrapolate\" か \"zero\" です(値: \"{}\")", name, tv));
        b.inlet_zero_tangential = tv == "zero";
        break;
      }
      case BoundaryType::PressureOutlet:
        b.pressure = r.get_number("pressure");
        break;
      case BoundaryType::Vacuum:
      case BoundaryType::Symmetry:
      case BoundaryType::PeriodicSlab:
        break;
    }
    r.finish();
    if (b.temperature) require_positive(*b.temperature, name + ".temperature");
    if (b.pressure) {
      if (b.type == BoundaryType::PressureOutlet) {
        GSIS_CHECK(*b.pressure >= 0.0,
                   std::format("{}.pressure は 0 以上でなければなりません", name));
      } else {
        require_positive(*b.pressure, name + ".pressure");
      }
    }
    GSIS_CHECK(cfg.find_boundary(b.patch) == nullptr,
               std::format("パッチ \"{}\" の境界条件が重複しています", b.patch));
    cfg.boundaries.push_back(std::move(b));
  }
  GSIS_CHECK(!cfg.boundaries.empty(), "[[boundary]] が 1 つもありません");
}

void parse_velocity_grid_section(const toml::node& node, CaseConfig& cfg) {
  TableReader r(as_table(node, "velocity_grid"), "velocity_grid");
  VelocityGridSection& v = cfg.velocity_grid;
  v.automatic = r.opt_boolean("auto").value_or(false);
  if (v.automatic) {
    GSIS_CHECK(r.find("n") == nullptr && r.find("range") == nullptr,
               "velocity_grid.auto = true と n・range は併用できません");
    r.finish();
    return;
  }
  v.n = r.get_int3("n");
  const auto range = r.get_range3("range");
  r.finish();
  long long total = 1;
  for (int a = 0; a < 3; ++a) {
    v.lo[a] = range[a][0];
    v.hi[a] = range[a][1];
    GSIS_CHECK(v.lo[a] < v.hi[a], std::format("velocity_grid.range の軸 {} で下限 {} < 上限 {} "
                                              "になっていません",
                                              a, v.lo[a], v.hi[a]));
    total *= v.n[a];
  }
  GSIS_CHECK(total <= 100'000'000,
             std::format("速度点が多すぎます({} 点)", total));
}

void parse_gsis_section(const toml::node& node, CaseConfig& cfg) {
  TableReader r(as_table(node, "gsis"), "gsis");
  GsisSection& g = cfg.gsis;
  if (auto v = r.opt_integer("n_cis")) {
    GSIS_CHECK(*v >= 1 && *v <= 1'000'000, std::format("gsis.n_cis = {} が範囲外です", *v));
    g.n_cis = static_cast<int>(*v);
  }
  if (auto v = r.opt_integer("max_pbc")) {
    GSIS_CHECK(*v >= 1 && *v <= 1'000'000, std::format("gsis.max_pbc = {} が範囲外です", *v));
    g.max_pbc = static_cast<int>(*v);
  }
  if (auto v = r.opt_number("cfl_pbc")) g.cfl_pbc = *v;
  if (auto v = r.opt_number("tolerance")) g.tolerance = *v;
  if (auto v = r.opt_integer("max_iterations")) {
    GSIS_CHECK(*v >= 1 && *v <= 1'000'000'000, std::format("gsis.max_iterations = {} が範囲外です", *v));
    g.max_iterations = *v;
  }
  if (auto v = r.opt_number("filter_length")) g.filter_length = *v;
  if (auto v = r.opt_number("filter_max_length")) g.filter_max_length = *v;
  if (auto v = r.opt_number("filter_min_cells")) g.filter_min_cells = *v;
  if (auto v = r.opt_number("damp_kn")) g.damp_kn = *v;
  if (auto v = r.opt_number("mass_balance_tolerance")) g.mass_balance_tolerance = *v;
  if (auto v = r.opt_boolean("mass_balance_required")) g.mass_balance_required = *v;
  r.finish();
  require_positive(g.mass_balance_tolerance, "gsis.mass_balance_tolerance");
  GSIS_CHECK(g.damp_kn >= 0.0 && std::isfinite(g.damp_kn), std::format("gsis.damp_kn = {} は 0 以上の数値です", g.damp_kn));
  require_positive(g.cfl_pbc, "gsis.cfl_pbc");
  GSIS_CHECK(g.filter_length >= 0.0 && std::isfinite(g.filter_length),
             std::format("gsis.filter_length = {} は 0 以上の数値です", g.filter_length));
  GSIS_CHECK(g.filter_max_length >= 0.0 && std::isfinite(g.filter_max_length),
             std::format("gsis.filter_max_length = {} は 0 以上の数値です", g.filter_max_length));
  GSIS_CHECK(g.filter_min_cells >= 0.0 && std::isfinite(g.filter_min_cells),
             std::format("gsis.filter_min_cells = {} は 0 以上の数値です", g.filter_min_cells));
  require_positive(g.tolerance, "gsis.tolerance");
}

// モジュールが解釈する節: スカラーの値を集めるだけにする
void parse_module_section(const toml::node& node, CaseConfig& cfg, std::string_view name) {
  const toml::table& t = as_table(node, name);
  ModuleSection sec{std::string(name)};
  for (auto&& [k, v] : t) {
    const std::string key(k.str());
    if (const auto* b = v.as_boolean()) {
      sec.values.emplace(key, b->get());
    } else if (const auto* i = v.as_integer()) {
      sec.values.emplace(key, static_cast<long long>(i->get()));
    } else if (const auto* d = v.as_floating_point()) {
      sec.values.emplace(key, d->get());
    } else if (const auto* s = v.as_string()) {
      sec.values.emplace(key, s->get());
    } else {
      GSIS_FAIL(std::format("[{}].{} はスカラー(真偽値・整数・実数・文字列)でなければなりません({})", name,
                            key, where(v)));
    }
  }
  cfg.module_sections.insert_or_assign(std::string(name), std::move(sec));
}

void parse_kinetic_section(const toml::node& node, CaseConfig& cfg) {
  parse_module_section(node, cfg, "kinetic");
}
void parse_macro_section(const toml::node& node, CaseConfig& cfg) {
  parse_module_section(node, cfg, "macro");
}
void parse_output_section(const toml::node& node, CaseConfig& cfg) {
  parse_module_section(node, cfg, "output");
}

// 節の一覧。新しい節はここに 1 行足す。
struct SectionDef {
  std::string_view name;
  bool required;
  void (*parse)(const toml::node&, CaseConfig&);
};

constexpr SectionDef kSections[] = {
    {"case", true, parse_case_section},
    {"gas", true, parse_gas_section},
    {"conditions", true, parse_conditions_section},
    {"boundary", true, parse_boundary_array},
    {"velocity_grid", true, parse_velocity_grid_section},
    {"gsis", false, parse_gsis_section},
    {"kinetic", false, parse_kinetic_section},
    {"macro", false, parse_macro_section},
    {"output", false, parse_output_section},
};

}  // namespace

CaseConfig parse_case_toml(std::string_view text, const std::filesystem::path& source) {
  const std::string source_name = source.empty() ? std::string("<文字列>") : path_to_utf8(source);
  toml::table root;
  try {
    root = toml::parse(text, source_name);
  } catch (const toml::parse_error& e) {
    throw Error(std::format("TOML の解析に失敗しました({} {} 行 {} 列): {}", source_name,
                            e.source().begin.line, e.source().begin.column, e.description()));
  }

  CaseConfig cfg;
  cfg.source = source;
  std::set<std::string_view> seen;
  for (auto&& [k, v] : root) {
    const std::string_view key = k.str();
    const SectionDef* def = nullptr;
    for (const SectionDef& d : kSections) {
      if (d.name == key) def = &d;
    }
    if (def == nullptr) {
      log_warn("{}: 知らない節 [{}] を読み飛ばします({})", source_name, key, where(v));
      cfg.ignored_sections.emplace_back(key);
      continue;
    }
    seen.insert(def->name);
    def->parse(v, cfg);
  }
  for (const SectionDef& d : kSections) {
    GSIS_CHECK(!d.required || seen.contains(d.name),
               std::format("{}: 必須の節 [{}] がありません", source_name, d.name));
  }
  if (cfg.velocity_grid.automatic) {
    auto& v = cfg.velocity_grid;
    v.T_min = std::numeric_limits<double>::infinity();
    for (const auto& b : cfg.boundaries) {
      if (b.temperature) {
        v.T_min = std::min(v.T_min, *b.temperature);
        v.T_max = std::max(v.T_max, *b.temperature);
      }
      if (b.velocity) v.u_max = std::max(v.u_max, norm(*b.velocity));
    }
    if (v.T_max == 0.0) v.T_min = v.T_max = cfg.conditions.T0;
    const double R = cfg.gas.specific_gas_constant();
    const double limit = v.u_max + 5.0 * std::sqrt(R * v.T_max);
    const double n = std::max(2.0, 2.0 * std::ceil(limit / (0.7 * std::sqrt(R * v.T_min))));
    GSIS_CHECK(std::isfinite(n) && n * n * n <= 100'000'000.0,
               "自動速度格子の速度点が多すぎます(1 億点以下)");
    const double limit_nd = limit / std::sqrt(R * cfg.conditions.T0);
    GSIS_CHECK(std::isfinite(limit_nd), "自動速度格子の範囲が有限ではありません");
    v.n.fill(static_cast<int>(n));
    v.lo.fill(-limit_nd);
    v.hi.fill(limit_nd);
  }
  // 圧力入口の pressure を省略できるのは、p_in が決まる delta_rp を与えたときだけ
  for (const BoundarySpec& b : cfg.boundaries) {
    GSIS_CHECK(b.type != BoundaryType::PressureInlet || b.pressure.has_value() ||
                   cfg.conditions.delta_rp.has_value(),
               std::format("{}: 圧力入口 \"{}\" の pressure を省略できるのは conditions.delta_rp を与えたときだけです",
                           source_name, b.patch));
  }
  return cfg;
}

CaseConfig load_case(const std::filesystem::path& path) {
  const std::string text = read_file(path);
  return parse_case_toml(text, path);
}

DerivedQuantities derive_quantities(const CaseConfig& config) {
  const GasSection& gas = config.gas;
  const ConditionsSection& c = config.conditions;
  DerivedQuantities d;
  d.R = gas.specific_gas_constant();
  d.mu0 = gas.viscosity(c.T0);
  d.most_probable_speed = std::sqrt(2.0 * d.R * c.T0);
  // Kn の定義(式 (8)): Kn = μ0 / (p L_ref) sqrt(π R T0 / 2)
  const double kn_factor = d.mu0 / c.L_ref * std::sqrt(kPi * d.R * c.T0 / 2.0);
  if (c.delta_rp) {
    d.p_ref = *c.delta_rp * d.mu0 * d.most_probable_speed / c.L_ref;
    d.has_p_in = true;
  } else if (c.Kn) {
    d.p_ref = kn_factor / *c.Kn;
  } else {
    GSIS_CHECK(c.p_ref.has_value(), "conditions に delta_rp・Kn・p_ref のどれもありません");
    d.p_ref = *c.p_ref;
  }
  d.p_in = d.p_ref;
  d.rho_in = d.p_in / (d.R * c.T0);
  d.Kn = kn_factor / d.p_ref;
  d.delta_from_Kn = kSqrtPi / (2.0 * d.Kn);
  d.mean_free_path = d.Kn * c.L_ref;

  ReferenceScales& ref = d.ref;
  ref.length = c.L_ref;
  ref.temperature = c.T0;
  ref.pressure = d.p_ref;
  ref.density = ref.pressure / (d.R * ref.temperature);
  ref.velocity = std::sqrt(d.R * ref.temperature);
  ref.time = ref.length / ref.velocity;
  ref.distribution = ref.density / (ref.velocity * ref.velocity * ref.velocity);
  ref.viscosity = ref.pressure * ref.length / ref.velocity;
  ref.heat_flux = ref.pressure * ref.velocity;
  ref.mass_flux = ref.density * ref.velocity;

  d.mu_hat0 = d.mu0 / ref.viscosity;
  d.mass_flow_scale = kSqrtPi * c.L_ref * c.L_ref * d.p_in / d.most_probable_speed;
  return d;
}

}  // namespace gsis
