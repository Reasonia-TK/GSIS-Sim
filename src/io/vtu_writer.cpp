#include "io/vtu_writer.hpp"

#include <algorithm>
#include <cstring>
#include <format>
#include <fstream>
#include <unordered_map>

#include "core/error.hpp"
#include "core/paths.hpp"

namespace gsis {
namespace {

constexpr std::uint8_t kVtkPolygon = 7;
constexpr std::uint8_t kVtkPolyhedron = 42;

template <class T>
std::vector<unsigned char> to_bytes(std::span<const T> values) {
  std::vector<unsigned char> b(values.size() * sizeof(T));
  if (!b.empty()) std::memcpy(b.data(), values.data(), b.size());
  return b;
}

}  // namespace

VtuWriter VtuWriter::polyhedra(const PolyMesh& mesh) {
  VtuWriter w;
  w.points_ = mesh.points;
  const auto n_cells = static_cast<std::size_t>(mesh.n_cells);

  // セル → 面(owner なら向きそのまま、neighbour なら逆向きにして外向きにする)
  std::vector<std::vector<std::pair<Index, bool>>> cell_faces(n_cells);
  for (Index f = 0; f < mesh.n_faces(); ++f) {
    cell_faces[static_cast<std::size_t>(mesh.owner[static_cast<std::size_t>(f)])].emplace_back(f, false);
    if (mesh.is_internal(f)) {
      cell_faces[static_cast<std::size_t>(mesh.neighbour[static_cast<std::size_t>(f)])].emplace_back(f, true);
    }
  }

  w.offsets_.reserve(n_cells);
  w.cell_types_.assign(n_cells, kVtkPolyhedron);
  w.faceoffsets_.reserve(n_cells);
  std::vector<Index> unique_points;
  for (std::size_t c = 0; c < n_cells; ++c) {
    // セルの節点(重複なし、出てきた順)
    unique_points.clear();
    for (const auto& [f, flip] : cell_faces[c]) {
      for (const Index p : mesh.face(f)) {
        if (std::find(unique_points.begin(), unique_points.end(), p) == unique_points.end()) {
          unique_points.push_back(p);
        }
      }
    }
    w.connectivity_.insert(w.connectivity_.end(), unique_points.begin(), unique_points.end());
    w.offsets_.push_back(static_cast<std::int64_t>(w.connectivity_.size()));

    // 面の並び: [面の数, 節点数, 節点..., 節点数, 節点..., ...](節点は全体の番号)
    w.faces_.push_back(static_cast<std::int64_t>(cell_faces[c].size()));
    for (const auto& [f, flip] : cell_faces[c]) {
      const std::span<const Index> nodes = mesh.face(f);
      w.faces_.push_back(static_cast<std::int64_t>(nodes.size()));
      if (!flip) {
        for (const Index p : nodes) w.faces_.push_back(p);
      } else {
        for (std::size_t k = nodes.size(); k-- > 0;) w.faces_.push_back(nodes[k]);
      }
    }
    // faceoffsets は各セルの面の並びの「終わり」の位置
    w.faceoffsets_.push_back(static_cast<std::int64_t>(w.faces_.size()));
  }
  return w;
}

VtuWriter VtuWriter::boundary_polygons(const PolyMesh& mesh, std::span<const int> patch_ids) {
  std::vector<int> selected;
  if (patch_ids.empty()) {
    for (std::size_t p = 0; p < mesh.patches.size(); ++p) selected.push_back(static_cast<int>(p));
  } else {
    selected.assign(patch_ids.begin(), patch_ids.end());
  }

  VtuWriter w;
  // 境界面が使う節点だけを詰め直す
  std::unordered_map<Index, std::int64_t> remap;
  std::vector<std::int32_t> patch_of_cell;
  std::vector<std::int32_t> face_of_cell;
  for (const int p : selected) {
    GSIS_CHECK(p >= 0 && static_cast<std::size_t>(p) < mesh.patches.size(),
               std::format("パッチ番号 {} が範囲外です", p));
    const Patch& patch = mesh.patches[static_cast<std::size_t>(p)];
    for (Index f = patch.start; f < patch.end(); ++f) {
      for (const Index node : mesh.face(f)) {
        auto [it, inserted] = remap.try_emplace(node, static_cast<std::int64_t>(w.points_.size()));
        if (inserted) w.points_.push_back(mesh.points[static_cast<std::size_t>(node)]);
        w.connectivity_.push_back(it->second);
      }
      w.offsets_.push_back(static_cast<std::int64_t>(w.connectivity_.size()));
      w.cell_types_.push_back(kVtkPolygon);
      patch_of_cell.push_back(p);
      face_of_cell.push_back(f);
    }
  }
  w.add_cell_scalar("patch_id", std::span<const std::int32_t>(patch_of_cell));
  w.add_cell_scalar("face_id", std::span<const std::int32_t>(face_of_cell));
  return w;
}

template <class T>
VtuWriter::DataArray VtuWriter::make_array(const std::string& name, DataType type, int n_components,
                                           std::span<const T> values) {
  DataArray a;
  a.name = name;
  a.type = type;
  a.n_components = n_components;
  a.bytes = to_bytes(values);
  a.count = values.size();
  return a;
}

void VtuWriter::check_length(const std::string& name, std::size_t length, int n_components) const {
  GSIS_CHECK(n_components >= 1, std::format("セル値 \"{}\" の成分数が不正です", name));
  GSIS_CHECK(length == static_cast<std::size_t>(n_cells()) * static_cast<std::size_t>(n_components),
             std::format("セル値 \"{}\" の長さ {} がセル数 {} × 成分数 {} と違います", name, length,
                         n_cells(), n_components));
  for (const DataArray& a : cell_data_) {
    GSIS_CHECK(a.name != name, std::format("セル値の名前 \"{}\" が重複しています", name));
  }
}

void VtuWriter::add_cell_scalar(const std::string& name, std::span<const double> values) {
  check_length(name, values.size(), 1);
  cell_data_.push_back(make_array(name, DataType::Float64, 1, values));
}

void VtuWriter::add_cell_scalar(const std::string& name, std::span<const float> values) {
  check_length(name, values.size(), 1);
  cell_data_.push_back(make_array(name, DataType::Float32, 1, values));
}

void VtuWriter::add_cell_scalar(const std::string& name, std::span<const std::int32_t> values) {
  check_length(name, values.size(), 1);
  cell_data_.push_back(make_array(name, DataType::Int32, 1, values));
}

void VtuWriter::add_cell_vector(const std::string& name, std::span<const Vec3> values) {
  std::vector<double> flat(3 * values.size());
  for (std::size_t i = 0; i < values.size(); ++i) {
    flat[3 * i] = values[i].x;
    flat[3 * i + 1] = values[i].y;
    flat[3 * i + 2] = values[i].z;
  }
  check_length(name, flat.size(), 3);
  cell_data_.push_back(make_array(name, DataType::Float64, 3, std::span<const double>(flat)));
}

void VtuWriter::add_cell_field(const std::string& name, int n_components, std::span<const double> values) {
  check_length(name, values.size(), n_components);
  cell_data_.push_back(make_array(name, DataType::Float64, n_components, values));
}

void VtuWriter::write(const std::filesystem::path& path, VtuEncoding encoding) const {
  // 書く配列をまとめる
  struct Out {
    std::string name;
    std::string type;
    int n_components;
    const unsigned char* data;
    std::size_t nbytes;
    std::size_t count;
    DataType dtype;
  };
  auto type_name = [](DataType t) {
    switch (t) {
      case DataType::Float64:
        return "Float64";
      case DataType::Float32:
        return "Float32";
      case DataType::Int32:
        return "Int32";
      case DataType::Int64:
        return "Int64";
      case DataType::UInt8:
        return "UInt8";
    }
    return "?";
  };

  std::vector<double> xyz(3 * points_.size());
  for (std::size_t i = 0; i < points_.size(); ++i) {
    xyz[3 * i] = points_[i].x;
    xyz[3 * i + 1] = points_[i].y;
    xyz[3 * i + 2] = points_[i].z;
  }
  auto make_out = [&](std::string name, DataType t, int nc, const void* data, std::size_t count,
                      std::size_t elem) {
    return Out{std::move(name), type_name(t), nc, static_cast<const unsigned char*>(data),
               count * elem, count, t};
  };
  const Out points_out = make_out("Points", DataType::Float64, 3, xyz.data(), xyz.size(), 8);
  std::vector<Out> cells_out = {
      make_out("connectivity", DataType::Int64, 1, connectivity_.data(), connectivity_.size(), 8),
      make_out("offsets", DataType::Int64, 1, offsets_.data(), offsets_.size(), 8),
      make_out("types", DataType::UInt8, 1, cell_types_.data(), cell_types_.size(), 1),
  };
  if (!faceoffsets_.empty()) {
    cells_out.push_back(make_out("faces", DataType::Int64, 1, faces_.data(), faces_.size(), 8));
    cells_out.push_back(
        make_out("faceoffsets", DataType::Int64, 1, faceoffsets_.data(), faceoffsets_.size(), 8));
  }
  std::vector<Out> data_out;
  for (const DataArray& a : cell_data_) {
    data_out.push_back(Out{a.name, type_name(a.type), a.n_components, a.bytes.data(), a.bytes.size(),
                           a.count, a.type});
  }

  const bool ascii = encoding == VtuEncoding::Ascii;
  std::string xml;
  std::string appended;
  auto ascii_values = [](const Out& o) {
    std::string s;
    for (std::size_t i = 0; i < o.count; ++i) {
      if (i > 0) s += (i % 9 == 0) ? '\n' : ' ';
      switch (o.dtype) {
        case DataType::Float64: {
          double v;
          std::memcpy(&v, o.data + 8 * i, 8);
          s += std::format("{}", v);
          break;
        }
        case DataType::Float32: {
          float v;
          std::memcpy(&v, o.data + 4 * i, 4);
          s += std::format("{}", v);
          break;
        }
        case DataType::Int32: {
          std::int32_t v;
          std::memcpy(&v, o.data + 4 * i, 4);
          s += std::format("{}", v);
          break;
        }
        case DataType::Int64: {
          std::int64_t v;
          std::memcpy(&v, o.data + 8 * i, 8);
          s += std::format("{}", v);
          break;
        }
        case DataType::UInt8:
          s += std::format("{}", static_cast<unsigned>(o.data[i]));
          break;
      }
    }
    return s;
  };
  auto data_array = [&](const Out& o, const std::string& indent) {
    std::string head = std::format("{}<DataArray type=\"{}\" Name=\"{}\"", indent, o.type, o.name);
    if (o.n_components != 1) head += std::format(" NumberOfComponents=\"{}\"", o.n_components);
    if (ascii) {
      xml += head + " format=\"ascii\">\n" + ascii_values(o) + "\n" + indent + "</DataArray>\n";
    } else {
      xml += head + std::format(" format=\"appended\" offset=\"{}\"/>\n", appended.size());
      const std::uint64_t n = o.nbytes;
      appended.append(reinterpret_cast<const char*>(&n), 8);
      appended.append(reinterpret_cast<const char*>(o.data), o.nbytes);
    }
  };

  xml += "<?xml version=\"1.0\"?>\n";
  xml += "<VTKFile type=\"UnstructuredGrid\" version=\"1.0\" byte_order=\"LittleEndian\" "
         "header_type=\"UInt64\">\n";
  xml += "  <UnstructuredGrid>\n";
  xml += std::format("    <Piece NumberOfPoints=\"{}\" NumberOfCells=\"{}\">\n", points_.size(),
                     cell_types_.size());
  xml += "      <Points>\n";
  data_array(points_out, "        ");
  xml += "      </Points>\n";
  xml += "      <Cells>\n";
  for (const Out& o : cells_out) data_array(o, "        ");
  xml += "      </Cells>\n";
  if (!data_out.empty()) {
    xml += "      <CellData>\n";
    for (const Out& o : data_out) data_array(o, "        ");
    xml += "      </CellData>\n";
  }
  xml += "    </Piece>\n";
  xml += "  </UnstructuredGrid>\n";
  if (!ascii) {
    xml += "  <AppendedData encoding=\"raw\">\n   _";
  }

  ensure_parent_directory(path);
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  GSIS_CHECK(out.good(), std::format("VTU ファイルを作れません: {}", path_to_utf8(path)));
  out.write(xml.data(), static_cast<std::streamsize>(xml.size()));
  if (!ascii) {
    out.write(appended.data(), static_cast<std::streamsize>(appended.size()));
    const std::string tail = "\n  </AppendedData>\n</VTKFile>\n";
    out.write(tail.data(), static_cast<std::streamsize>(tail.size()));
  } else {
    const std::string tail = "</VTKFile>\n";
    out.write(tail.data(), static_cast<std::streamsize>(tail.size()));
  }
  out.flush();
  GSIS_CHECK(out.good(), std::format("VTU ファイルに書けません: {}", path_to_utf8(path)));
}

}  // namespace gsis
