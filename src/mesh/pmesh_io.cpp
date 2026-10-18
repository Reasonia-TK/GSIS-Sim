#include "mesh/pmesh_io.hpp"

#include <algorithm>
#include <bit>
#include <cstdint>
#include <cstring>
#include <format>
#include <fstream>
#include <limits>
#include <optional>
#include <string>
#include <vector>

#include "core/error.hpp"
#include "core/json.hpp"
#include "core/log.hpp"
#include "core/paths.hpp"

static_assert(std::endian::native == std::endian::little,
              ".pmesh の読み書きはリトルエンディアンの計算機を前提にしている");

namespace gsis {
namespace {

constexpr char kMagic[8] = {'P', 'M', 'E', 'S', 'H', '\0', '\0', '\0'};
constexpr std::uint64_t kAlign = 64;
constexpr std::uint64_t kMaxHeaderBytes = std::uint64_t{64} << 20;  // ヘッダは 64 MiB まで

enum class DType { Float64, Int64, Int32 };

std::optional<DType> parse_dtype(std::string_view s) {
  if (s == "float64") return DType::Float64;
  if (s == "int64") return DType::Int64;
  if (s == "int32") return DType::Int32;
  return std::nullopt;
}

std::uint64_t dtype_size(DType d) { return d == DType::Int32 ? 4 : 8; }

std::uint64_t align_up(std::uint64_t x) { return (x + kAlign - 1) / kAlign * kAlign; }

// ---------------------------------------------------------------------------
// 読み込み
// ---------------------------------------------------------------------------

struct ArrayInfo {
  std::string name;
  DType dtype = DType::Float64;
  std::vector<std::int64_t> shape;
  std::uint64_t offset = 0;
  std::uint64_t count = 0;   // 要素数(shape の積)
  std::uint64_t nbytes = 0;
};

class PmeshReader {
 public:
  explicit PmeshReader(const std::filesystem::path& path) : path_(path), name_(path_to_utf8(path)) {}

  PolyMesh read() {
    in_.open(path_, std::ios::binary);
    GSIS_CHECK(in_.good(), std::format("メッシュファイルを開けません: {}", name_));
    in_.seekg(0, std::ios::end);
    file_size_ = static_cast<std::uint64_t>(in_.tellg());
    in_.seekg(0, std::ios::beg);

    // マジックとヘッダの長さ
    check(file_size_ >= 16, "ファイルが短すぎます(16 バイト未満)");
    char magic[8];
    in_.read(magic, 8);
    check(in_.good() && std::memcmp(magic, kMagic, 8) == 0,
          "先頭のマジックが \"PMESH\\0\\0\\0\" ではありません(.pmesh ではない)");
    std::uint64_t header_bytes = 0;
    in_.read(reinterpret_cast<char*>(&header_bytes), 8);
    check(in_.good(), "ヘッダの長さを読めません");
    check(header_bytes > 0 && header_bytes <= kMaxHeaderBytes,
          std::format("ヘッダの長さ {} バイトが不正です", header_bytes));
    check(16 + header_bytes <= file_size_, "ヘッダがファイルの外まで続いています");
    header_end_ = 16 + header_bytes;
    std::string header_text(static_cast<std::size_t>(header_bytes), '\0');
    in_.read(header_text.data(), static_cast<std::streamsize>(header_bytes));
    check(in_.good(), "ヘッダを読めません");

    json::Value header;
    try {
      header = json::parse(header_text);
    } catch (const Error& e) {
      fail(std::format("JSON ヘッダ: {}", e.what()));
    }
    check(header.is_object(), "JSON ヘッダがオブジェクトではありません");
    return read_body(header);
  }

 private:
  std::filesystem::path path_;
  std::string name_;
  std::ifstream in_;
  std::uint64_t file_size_ = 0;
  std::uint64_t header_end_ = 0;

  [[noreturn]] void fail(const std::string& what) const {
    throw Error(std::format("{}: {}", name_, what));
  }
  void check(bool cond, const std::string& what) const {
    if (!cond) fail(what);
  }

  const json::Value& require_key(const json::Value& obj, std::string_view key,
                                 std::string_view where) const {
    const json::Value* v = obj.find(key);
    check(v != nullptr, std::format("{}に \"{}\" がありません", where, key));
    return *v;
  }

  std::int64_t require_int(const json::Value& obj, std::string_view key, std::string_view where,
                           std::int64_t lo, std::int64_t hi) const {
    const json::Value& v = require_key(obj, key, where);
    check(v.is_int(), std::format("{}の \"{}\" が整数ではありません", where, key));
    const std::int64_t x = v.as_int();
    check(x >= lo && x <= hi,
          std::format("{}の \"{}\" = {} が範囲 [{}, {}] の外です", where, key, x, lo, hi));
    return x;
  }

  std::string require_string(const json::Value& obj, std::string_view key,
                             std::string_view where) const {
    const json::Value& v = require_key(obj, key, where);
    check(v.is_string(), std::format("{}の \"{}\" が文字列ではありません", where, key));
    return v.as_string();
  }

  ArrayInfo array_info(const json::Value& arrays, std::string_view name) const {
    const std::string where = std::format("配列 \"{}\" ", name);
    const json::Value& a = require_key(arrays, name, "\"arrays\" ");
    check(a.is_object(), std::format("{}の記述がオブジェクトではありません", where));
    ArrayInfo info;
    info.name = std::string(name);
    const std::string dtype = require_string(a, "dtype", where);
    const std::optional<DType> d = parse_dtype(dtype);
    check(d.has_value(),
          std::format("{}の dtype \"{}\" は未知です(float64・int64・int32 のみ)", where, dtype));
    info.dtype = *d;
    const json::Value& shape = require_key(a, "shape", where);
    check(shape.is_array() && !shape.as_array().empty(),
          std::format("{}の shape が整数の配列ではありません", where));
    info.count = 1;
    for (const json::Value& s : shape.as_array()) {
      check(s.is_int() && s.as_int() >= 0, std::format("{}の shape が不正です", where));
      const auto n = static_cast<std::uint64_t>(s.as_int());
      // 要素数の桁あふれと、ファイルより大きい配列を先に弾く
      check(n == 0 || info.count <= file_size_ / n,
            std::format("{}の shape がファイルの大きさに対して大きすぎます", where));
      info.shape.push_back(s.as_int());
      info.count *= n;
    }
    check(info.count <= file_size_ / dtype_size(info.dtype),
          std::format("{}の shape がファイルの大きさに対して大きすぎます", where));
    info.offset = static_cast<std::uint64_t>(
        require_int(a, "offset", where, 0, std::numeric_limits<std::int64_t>::max()));
    info.nbytes = info.count * dtype_size(info.dtype);
    check(info.offset % kAlign == 0,
          std::format("{}の offset {} が 64 の倍数ではありません", where, info.offset));
    check(info.offset >= header_end_,
          std::format("{}の offset {} がヘッダ(終わり {})と重なっています", where, info.offset,
                      header_end_));
    check(info.offset <= file_size_ && info.nbytes <= file_size_ - info.offset,
          std::format("{}がファイルの外まで続いています(offset {}、{} バイト、ファイル {} バイト)",
                      where, info.offset, info.nbytes, file_size_));
    return info;
  }

  void expect_shape(const ArrayInfo& a, std::vector<std::int64_t> expected) const {
    check(a.shape == expected, std::format("配列 \"{}\" の shape が期待と違います", a.name));
  }

  std::vector<char> read_bytes(const ArrayInfo& a) {
    std::vector<char> buf(static_cast<std::size_t>(a.nbytes));
    if (a.nbytes == 0) return buf;
    in_.seekg(static_cast<std::streamoff>(a.offset));
    in_.read(buf.data(), static_cast<std::streamsize>(a.nbytes));
    check(in_.good(), std::format("配列 \"{}\" を読めません", a.name));
    return buf;
  }

  std::vector<double> read_float64(const ArrayInfo& a) {
    check(a.dtype == DType::Float64, std::format("配列 \"{}\" の dtype は float64 です", a.name));
    const std::vector<char> buf = read_bytes(a);
    std::vector<double> out(static_cast<std::size_t>(a.count));
    if (!out.empty()) std::memcpy(out.data(), buf.data(), buf.size());
    return out;
  }

  std::vector<std::int64_t> read_int64(const ArrayInfo& a) {
    check(a.dtype == DType::Int64 || a.dtype == DType::Int32,
          std::format("配列 \"{}\" の dtype は整数(int64 か int32)です", a.name));
    const std::vector<char> buf = read_bytes(a);
    std::vector<std::int64_t> out(static_cast<std::size_t>(a.count));
    if (a.dtype == DType::Int64) {
      if (!out.empty()) std::memcpy(out.data(), buf.data(), buf.size());
    } else {
      for (std::size_t i = 0; i < out.size(); ++i) {
        std::int32_t v;
        std::memcpy(&v, buf.data() + 4 * i, 4);
        out[i] = v;
      }
    }
    return out;
  }

  std::vector<Index> read_index(const ArrayInfo& a) {
    check(a.dtype == DType::Int64 || a.dtype == DType::Int32,
          std::format("配列 \"{}\" の dtype は整数(int32 か int64)です", a.name));
    if (a.dtype == DType::Int32) {
      const std::vector<char> buf = read_bytes(a);
      std::vector<Index> out(static_cast<std::size_t>(a.count));
      if (!out.empty()) std::memcpy(out.data(), buf.data(), buf.size());
      return out;
    }
    const std::vector<std::int64_t> wide = read_int64(a);
    std::vector<Index> out(wide.size());
    for (std::size_t i = 0; i < wide.size(); ++i) {
      check(wide[i] >= std::numeric_limits<Index>::min() &&
                wide[i] <= std::numeric_limits<Index>::max(),
            std::format("配列 \"{}\" の値 {} が int32 の範囲を超えています", a.name, wide[i]));
      out[i] = static_cast<Index>(wide[i]);
    }
    return out;
  }

  PolyMesh read_body(const json::Value& h) {
    const std::string format = require_string(h, "format", "ヘッダ");
    check(format == "pmesh", std::format("format \"{}\" は \"pmesh\" ではありません", format));
    const json::Value& version = require_key(h, "version", "ヘッダ");
    check(version.is_int() && version.as_int() == 1,
          std::format("version {} には対応していません(対応する版は 1)", json::dump(version)));

    PolyMesh mesh;
    mesh.length_unit = require_string(h, "length_unit", "ヘッダ");
    check(mesh.length_unit == "m",
          std::format("length_unit \"{}\" には対応していません(v1 は \"m\" のみ)", mesh.length_unit));
    constexpr std::int64_t kMaxIndex = std::numeric_limits<Index>::max() - 1;
    const std::int64_t n_points = require_int(h, "n_points", "ヘッダ", 1, kMaxIndex);
    const std::int64_t n_faces = require_int(h, "n_faces", "ヘッダ", 1, kMaxIndex);
    const std::int64_t n_internal = require_int(h, "n_internal_faces", "ヘッダ", 0, n_faces);
    const std::int64_t n_cells = require_int(h, "n_cells", "ヘッダ", 1, kMaxIndex);
    mesh.n_cells = static_cast<Index>(n_cells);

    // パッチ
    const json::Value& patches = require_key(h, "patches", "ヘッダ");
    check(patches.is_array(), "\"patches\" が配列ではありません");
    for (std::size_t i = 0; i < patches.as_array().size(); ++i) {
      const json::Value& p = patches.as_array()[i];
      const std::string where = std::format("patches[{}] ", i);
      check(p.is_object(), std::format("{}がオブジェクトではありません", where));
      Patch patch;
      patch.name = require_string(p, "name", where);
      patch.kind = require_string(p, "kind", where);
      patch.start = static_cast<Index>(require_int(p, "start", where, 0, n_faces));
      patch.count = static_cast<Index>(require_int(p, "count", where, 0, n_faces));
      mesh.patches.push_back(std::move(patch));
    }

    // 配列の記述
    const json::Value& arrays = require_key(h, "arrays", "ヘッダ");
    check(arrays.is_object(), "\"arrays\" がオブジェクトではありません");
    const ArrayInfo points = array_info(arrays, "points");
    const ArrayInfo face_offsets = array_info(arrays, "face_offsets");
    const ArrayInfo face_nodes = array_info(arrays, "face_nodes");
    const ArrayInfo owner = array_info(arrays, "owner");
    const ArrayInfo neighbour = array_info(arrays, "neighbour");

    // 配列同士が重ならないこと(長さ 0 の配列はどこにも重ならないので除く)
    std::vector<const ArrayInfo*> sorted;
    for (const ArrayInfo* a : {&points, &face_offsets, &face_nodes, &owner, &neighbour}) {
      if (a->nbytes > 0) sorted.push_back(a);
    }
    std::sort(sorted.begin(), sorted.end(), [](const ArrayInfo* a, const ArrayInfo* b) {
      return a->offset != b->offset ? a->offset < b->offset : a->nbytes < b->nbytes;
    });
    for (std::size_t i = 0; i + 1 < sorted.size(); ++i) {
      check(sorted[i]->offset + sorted[i]->nbytes <= sorted[i + 1]->offset,
            std::format("配列 \"{}\" と \"{}\" が重なっています", sorted[i]->name,
                        sorted[i + 1]->name));
    }

    expect_shape(points, {n_points, 3});
    expect_shape(face_offsets, {n_faces + 1});
    expect_shape(owner, {n_faces});
    expect_shape(neighbour, {n_internal});

    const std::vector<double> xyz = read_float64(points);
    mesh.points.resize(static_cast<std::size_t>(n_points));
    for (std::size_t i = 0; i < mesh.points.size(); ++i) {
      mesh.points[i] = Vec3{xyz[3 * i], xyz[3 * i + 1], xyz[3 * i + 2]};
    }
    mesh.face_offsets = read_int64(face_offsets);
    check(!mesh.face_offsets.empty() && mesh.face_offsets.front() == 0, "face_offsets[0] が 0 ではありません");
    for (std::size_t i = 0; i + 1 < mesh.face_offsets.size(); ++i) {
      check(mesh.face_offsets[i] <= mesh.face_offsets[i + 1],
            std::format("face_offsets が減少しています(面 {})", i));
    }
    expect_shape(face_nodes, {mesh.face_offsets.back()});
    mesh.face_nodes = read_index(face_nodes);
    mesh.owner = read_index(owner);
    mesh.neighbour = read_index(neighbour);

    // metadata(任意。文字列以外の値は読み飛ばす)
    if (const json::Value* md = h.find("metadata")) {
      check(md->is_object(), "\"metadata\" がオブジェクトではありません");
      for (const auto& [k, v] : md->as_object()) {
        if (v.is_string()) {
          mesh.metadata.emplace_back(k, v.as_string());
        } else {
          log_warn("{}: metadata の \"{}\" が文字列ではないので読み飛ばします", name_, k);
        }
      }
    }

    try {
      validate_topology(mesh);
    } catch (const Error& e) {
      fail(e.what());
    }
    // count が 0 のパッチは許すが、警告する(pmesh_format.md §3 の 7)
    for (const Patch& p : mesh.patches) {
      if (p.count == 0) log_warn("{}: パッチ \"{}\" の面が 0 です", name_, p.name);
    }
    return mesh;
  }
};

}  // namespace

PolyMesh read_pmesh(const std::filesystem::path& path) { return PmeshReader(path).read(); }

LoadedMesh validate_mesh(PolyMesh mesh) {
  validate_topology(mesh);
  LoadedMesh out;
  out.geometry = compute_geometry(mesh);
  out.report = check_geometry(mesh, out.geometry);
  if (!out.report.ok()) {
    std::string msg = "メッシュの幾何の検査に失敗しました:";
    for (const std::string& e : out.report.errors) msg += "\n  " + e;
    throw Error(msg);
  }
  for (const std::string& w : out.report.warnings) log_warn("メッシュ: {}", w);
  out.mesh = std::move(mesh);
  return out;
}

LoadedMesh load_pmesh(const std::filesystem::path& path) {
  PolyMesh mesh = read_pmesh(path);
  try {
    return validate_mesh(std::move(mesh));
  } catch (const Error& e) {
    throw Error(std::format("{}: {}", path_to_utf8(path), e.what()));
  }
}

// ---------------------------------------------------------------------------
// 書き出し
// ---------------------------------------------------------------------------

void write_pmesh(const std::filesystem::path& path, const PolyMesh& mesh) {
  validate_topology(mesh);

  struct Blob {
    std::string name;
    std::string dtype;
    std::vector<std::int64_t> shape;
    const char* data;
    std::uint64_t nbytes;
  };
  std::vector<double> xyz(3 * mesh.points.size());
  for (std::size_t i = 0; i < mesh.points.size(); ++i) {
    xyz[3 * i] = mesh.points[i].x;
    xyz[3 * i + 1] = mesh.points[i].y;
    xyz[3 * i + 2] = mesh.points[i].z;
  }
  const std::vector<Blob> blobs = {
      {"points", "float64", {mesh.n_points(), 3}, reinterpret_cast<const char*>(xyz.data()),
       xyz.size() * sizeof(double)},
      {"face_offsets", "int64", {static_cast<std::int64_t>(mesh.face_offsets.size())},
       reinterpret_cast<const char*>(mesh.face_offsets.data()),
       mesh.face_offsets.size() * sizeof(Offset)},
      {"face_nodes", "int32", {static_cast<std::int64_t>(mesh.face_nodes.size())},
       reinterpret_cast<const char*>(mesh.face_nodes.data()), mesh.face_nodes.size() * sizeof(Index)},
      {"owner", "int32", {static_cast<std::int64_t>(mesh.owner.size())},
       reinterpret_cast<const char*>(mesh.owner.data()), mesh.owner.size() * sizeof(Index)},
      {"neighbour", "int32", {static_cast<std::int64_t>(mesh.neighbour.size())},
       reinterpret_cast<const char*>(mesh.neighbour.data()), mesh.neighbour.size() * sizeof(Index)},
  };

  auto make_header = [&](const std::vector<std::uint64_t>& offsets) {
    json::Value h;
    h.set("format", "pmesh");
    h.set("version", 1);
    h.set("length_unit", mesh.length_unit);
    h.set("n_points", mesh.n_points());
    h.set("n_faces", mesh.n_faces());
    h.set("n_internal_faces", mesh.n_internal_faces());
    h.set("n_cells", mesh.n_cells);
    json::Value patches = json::Array{};
    for (const Patch& p : mesh.patches) {
      json::Value jp;
      jp.set("name", p.name);
      jp.set("kind", p.kind);
      jp.set("start", p.start);
      jp.set("count", p.count);
      patches.push_back(std::move(jp));
    }
    h.set("patches", std::move(patches));
    json::Value arrays = json::Object{};
    for (std::size_t i = 0; i < blobs.size(); ++i) {
      json::Value a;
      a.set("dtype", blobs[i].dtype);
      json::Value shape = json::Array{};
      for (const std::int64_t s : blobs[i].shape) shape.push_back(s);
      a.set("shape", std::move(shape));
      a.set("offset", static_cast<std::int64_t>(offsets[i]));
      arrays.set(blobs[i].name, std::move(a));
    }
    h.set("arrays", std::move(arrays));
    json::Value md = json::Object{};
    for (const auto& [k, v] : mesh.metadata) md.set(k, v);
    h.set("metadata", std::move(md));
    return json::dump(h, 2);
  };

  // ヘッダの長さとオフセットが落ち着くまで繰り返す(オフセットの桁数がヘッダの長さに効くため)
  std::uint64_t base = 1024;
  std::vector<std::uint64_t> offsets(blobs.size());
  std::string header;
  for (int iter = 0;; ++iter) {
    GSIS_ASSERT(iter < 16);
    std::uint64_t pos = base;
    for (std::size_t i = 0; i < blobs.size(); ++i) {
      offsets[i] = pos;
      pos = align_up(pos + blobs[i].nbytes);
    }
    header = make_header(offsets);
    const std::uint64_t needed = align_up(16 + header.size());
    if (needed <= base) break;
    base = needed;
  }

  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  GSIS_CHECK(out.good(), std::format("メッシュファイルを作れません: {}", path_to_utf8(path)));
  std::uint64_t written = 0;
  auto write = [&](const char* data, std::uint64_t n) {
    if (n > 0) out.write(data, static_cast<std::streamsize>(n));
    written += n;
  };
  auto pad_to = [&](std::uint64_t pos) {
    GSIS_ASSERT(pos >= written);
    const std::string zeros(static_cast<std::size_t>(pos - written), '\0');
    write(zeros.data(), zeros.size());
  };
  const std::uint64_t header_bytes = header.size();
  write(kMagic, 8);
  write(reinterpret_cast<const char*>(&header_bytes), 8);
  write(header.data(), header.size());
  for (std::size_t i = 0; i < blobs.size(); ++i) {
    pad_to(offsets[i]);
    write(blobs[i].data, blobs[i].nbytes);
  }
  out.flush();
  GSIS_CHECK(out.good(), std::format("メッシュファイルに書けません: {}", path_to_utf8(path)));
}

}  // namespace gsis
