#pragma once
// ケース定義(TOML、docs/spec.md §4.6)の読み込み・検証と、導出量の計算。
//
// 節を追加するときは、CaseConfig にメンバーを足し、case_config.cpp の kSections に
// 解析関数を 1 行登録する。知らない節は警告して読み飛ばす(ignored_sections に残る)。
// 既知の節の中の知らないキーは、綴りの誤りを見逃さないようエラーにする。
// [kinetic]・[macro]・[output] はモジュールが解釈する節で、値を ModuleSection に集めるだけにする。

#include <array>
#include <filesystem>
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

#include "core/vec3.hpp"

namespace gsis {

enum class SolverMode { Gsis, Cis, Nsf };

enum class BoundaryType {
  DiffuseWall,     // 静止(または移動)完全拡散壁: temperature が必須、velocity は任意
  PressureInlet,   // 圧力入口: temperature が必須、pressure は任意(省略時は p_in。delta_rp が必要)
  PressureOutlet,  // 圧力出口: pressure が必須
  Vacuum,          // 真空(流入分布 0。p_out = 0 の圧力出口と等価、仮定 A6)
  Symmetry,        // 対称面(速度格子の軸に揃った鏡映面)
  PeriodicSlab,    // 並進周期(1 セル厚の方向の対面。流束が打ち消し合うので 2 面とも流束の和から除く)
};

std::string_view to_string(SolverMode mode);
std::string_view to_string(BoundaryType type);

// [case]
struct CaseSection {
  std::string name;
  std::filesystem::path mesh;  // TOML に書かれたとおり(相対パスは TOML の場所から)
  SolverMode mode = SolverMode::Gsis;
};

// [gas]
struct GasSection {
  double molecular_mass = 0.0;  // 分子質量 m [kg]
  double mu_ref = 0.0;          // 基準温度での粘性係数 [Pa s]
  double T_ref = 0.0;           // 粘性則の基準温度 [K]
  double omega = 0.0;           // 粘性のべき指数(式 (11))
  double Pr = 2.0 / 3.0;        // Prandtl 数(Shakhov、単原子。固定)
  double gamma = 5.0 / 3.0;     // 比熱比(単原子。固定)

  double specific_gas_constant() const;  // R = k_B / m [J/(kg K)]
  double viscosity(double T) const;      // μ(T) = μ_ref (T / T_ref)^ω [Pa s]
};

// [conditions]
// 基準圧力 p_ref は delta_rp・Kn・p_ref のどれか 1 つで与える(spec §3.2)。
//   delta_rp: 管ケース。p_ref = p_in = δ_rp μ0 sqrt(2 R T0) / L_ref
//   Kn:       式 (8) を (p_ref, T0) で評価した値。平板の検証ケースなど(p_ref = 平均の圧力)
//   p_ref:    基準圧力を直接与える [Pa]
struct ConditionsSection {
  double L_ref = 0.0;               // 基準長さ [m](管の場合は管半径)
  double T0 = 0.0;                  // 基準温度 [K]
  std::optional<double> delta_rp;   // 希薄度 δ_rp
  std::optional<double> Kn;         // Knudsen 数(式 (8))
  std::optional<double> p_ref;      // 基準圧力 [Pa]
};

// [[boundary]]
struct BoundarySpec {
  std::string patch;
  BoundaryType type = BoundaryType::DiffuseWall;
  std::optional<double> temperature;  // [K]
  std::optional<double> pressure;     // [Pa]
  std::optional<Vec3> velocity;       // 壁速度 [m/s](任意。クエット流の検証用)
  // 圧力入口の流入の速度(フェーズ 5a。TOML の tangential_velocity): false(既定、"extrapolate")は論文の式 (65)
  // u_b = u_{i,b} + Δu n(接線成分は内側の値)、true("zero")は接線成分を 0 にする u_b = (u_{i,b}·n + Δu) n
  // (静止した大きな槽から流れ込む入口。入口を通る旋回・循環を境界で拘束する)
  bool inlet_zero_tangential = false;
};

// [velocity_grid]
struct VelocityGridSection {
  bool automatic = false;
  double T_min = 0.0, T_max = 0.0, u_max = 0.0;  // 自動決定に使った境界の値(SI)
  std::array<int, 3> n{};       // 各軸の点数
  std::array<double, 3> lo{};   // 各軸の下限(単位 sqrt(R T0))
  std::array<double, 3> hi{};   // 各軸の上限(単位 sqrt(R T0))
};

// [gsis](省略時は spec §3.7 の管ケースの値)
struct GsisSection {
  int n_cis = 5;            // GSIS 1 反復あたりの CIS 反復数
  int max_pbc = 10;         // GSIS 1 反復あたりの PBC 反復の上限 M
  double cfl_pbc = 500.0;   // PBC の擬似時間の CFL 数
  double tolerance = 1e-5;  // 収束判定 (59) の ε
  long long max_iterations = 1000;  // GSIS 反復の上限(gsis-solver run。CIS 単独は [kinetic].max_iterations)
  // 補正の低域フィルタ(フェーズ 4 で追加。論文にはない。docs/design_gsis.md §3a): ℓ = filter_length × 局所平均自由行程。
  // 0 なら使わない(論文どおり)。既定 1(平板の Kn ≥ 1 と管で、フィルタなしでは GSIS が発散した。docs/phase4_report.md)
  double filter_length = 1.0;
  double filter_max_length = 0.0;   // ℓ の上限(L_ref 単位)。0 なら領域の対角線
  // ℓ の下限(セルの大きさ h = V^{1/3} の倍数、フェーズ 5b)。0 なら使わない。連続流の領域で PBC 段が格子規模の
  // モードを増幅するのを抑える(管 δ = 1000、docs/phase5b_d1000_report.md)
  double filter_min_cells = 0.0;
  // 局所 Kn による補正の減衰(フェーズ 5b)。正なら、PBC 段の補正 W^{n+1} − W^{n+1/2}(ln ρ・u・ln T)を
  // w = 1/(1 + (Kn/damp_kn)²) 倍にしてから式 (14) に使う(Kn は W^{n+1/2} の局所 Kn、式 (8))。0 なら使わない。
  // 凍結(gsis_freeze_kn)をなめらかにしたもの。収束した解は変わらない(補正 0)
  double damp_kn = 0.0;
  // 質量の釣り合いの許容値(フェーズ 5c、gsis-solver run・cis-run)。境界の入口と出口の質量流量の相対差
  // (outflow − inflow)/max(inflow, outflow)と、断面・入口・出口の流量のばらつきがこれを超えたら、式 (59) の判定を
  // 満たしても警告する。GSIS では PBC の境界フラックスの釣り合いも見る
  double mass_balance_tolerance = 2e-3;
  // true なら、収束の判定に質量の釣り合いも加える(式 (59) の ε < tolerance かつ、運動論の分布(GSIS は CIS の後の
  // f^{n+1/2})の境界の釣り合いが mass_balance_tolerance 以内)。既定 false(論文どおり ε だけで判定し、警告だけ出す)
  bool mass_balance_required = false;
};

// モジュールが自分で解釈する節([kinetic]、[macro]、[output])。値は TOML のスカラーだけ。
// config は値を集めるだけで意味は解釈しない。各モジュールは読み終えたら finish() を呼び、
// 読まれなかったキー(綴りの誤り)をエラーにする。
using ParamValue = std::variant<bool, long long, double, std::string>;

class ModuleSection {
 public:
  ModuleSection() = default;
  explicit ModuleSection(std::string name) : name_(std::move(name)) {}

  const std::string& name() const { return name_; }
  bool contains(std::string_view key) const;
  // 値を読む(無ければ fallback)。number は整数も受け付ける。型が違えばエラー。
  double number(std::string_view key, double fallback) const;
  long long integer(std::string_view key, long long fallback) const;
  std::string string(std::string_view key, std::string fallback) const;
  bool boolean(std::string_view key, bool fallback) const;
  // 読まれなかったキーがあればエラーにする
  void finish() const;

  std::map<std::string, ParamValue, std::less<>> values;

 private:
  const ParamValue* find(std::string_view key) const;
  std::string name_;
  mutable std::set<std::string, std::less<>> used_;
};

struct CaseConfig {
  std::filesystem::path source;  // 読み込んだ TOML(相対パスの基準)
  CaseSection case_info;
  GasSection gas;
  ConditionsSection conditions;
  std::vector<BoundarySpec> boundaries;
  VelocityGridSection velocity_grid;
  GsisSection gsis;
  std::map<std::string, ModuleSection, std::less<>> module_sections;  // [kinetic]・[macro]・[output]
  std::vector<std::string> ignored_sections;  // 知らない節の名前

  // メッシュのパス(相対パスなら TOML のディレクトリを基準に解決する)
  std::filesystem::path mesh_path() const;
  // パッチ名に対する境界条件(無ければ nullptr)
  const BoundarySpec* find_boundary(std::string_view patch) const;
  // モジュールの節(無ければ空の節。空の節は finish() を呼んでもエラーにならない)
  const ModuleSection& module_section(std::string_view name) const;
};

// TOML の文字列を解析・検証する。source は相対パスの基準とエラー表示に使う。
CaseConfig parse_case_toml(std::string_view text, const std::filesystem::path& source = {});
// TOML ファイルを読む
CaseConfig load_case(const std::filesystem::path& path);

// 無次元化の基準量(spec §3.2)。SI の値を基準量で割ると無次元量になる。
struct ReferenceScales {
  double length = 0.0;        // L_ref [m]
  double temperature = 0.0;   // T_ref = T0 [K]
  double pressure = 0.0;      // p_ref = p_in [Pa]
  double density = 0.0;       // ρ_ref = p_ref / (R T_ref) [kg/m^3]
  double velocity = 0.0;      // sqrt(R T_ref) [m/s]
  double time = 0.0;          // L_ref / sqrt(R T_ref) [s]
  double distribution = 0.0;  // ρ_ref / (R T_ref)^{3/2} [kg s^3/m^6]
  double viscosity = 0.0;     // p_ref L_ref / sqrt(R T_ref) [Pa s]
  double heat_flux = 0.0;     // p_ref sqrt(R T_ref) [W/m^2]
  double mass_flux = 0.0;     // ρ_ref sqrt(R T_ref) [kg/(m^2 s)]
};

struct DerivedQuantities {
  double R = 0.0;                    // 比気体定数 k_B / m [J/(kg K)]
  double mu0 = 0.0;                  // μ(T0) [Pa s]
  double most_probable_speed = 0.0;  // sqrt(2 R T0) [m/s]
  double p_ref = 0.0;                // 基準圧力 [Pa](ConditionsSection の注釈を参照)
  bool has_p_in = false;             // delta_rp が与えられ、入口圧力 p_in が決まっているか
  double p_in = 0.0;                 // δ_rp μ0 sqrt(2 R T0) / L_ref [Pa](has_p_in のとき。それ以外は p_ref)
  double rho_in = 0.0;               // p_in / (R T0) [kg/m^3]
  double Kn = 0.0;                   // 式 (8) を (p_ref, T0) で評価
  double delta_from_Kn = 0.0;        // sqrt(π) / (2 Kn)(delta_rp を与えたときは δ_rp と一致する)
  double mean_free_path = 0.0;       // λ = Kn L_ref [m](式 (8) の定義に対応する平均自由行程)
  double mu_hat0 = 0.0;              // 無次元の μ(T0)(delta_rp を与えたときは 1 / (sqrt(2) δ_rp))
  double mass_flow_scale = 0.0;      // 無次元質量流量の分母 sqrt(π) L_ref^2 p_in / sqrt(2 R T0) [kg/s]
  ReferenceScales ref;
};

DerivedQuantities derive_quantities(const CaseConfig& config);

}  // namespace gsis
