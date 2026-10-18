#pragma once
// VTK XML 非構造格子(.vtu)の書き出し。
// - 体積メッシュ: 全セルを VTK_POLYHEDRON(42)として書く(任意多面体。面は外向きの順)
// - 境界面: 境界面を VTK_POLYGON(7)として書く
// - セル値(スカラー・ベクトル・テンソル・整数)を付けられる
// - 形式は appended の raw バイナリ(既定、header_type="UInt64")か ASCII
// ParaView と VTK の XML 読み込み(vtkXMLUnstructuredGridReader)で読める形式にする。
// 多面体の面は "faces" / "faceoffsets" 配列で表す(VTK の XML 形式 1.0 の書き方)。

#include <cstdint>
#include <filesystem>
#include <span>
#include <string>
#include <vector>

#include "core/vec3.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

enum class VtuEncoding { AppendedRaw, Ascii };

class VtuWriter {
 public:
  // 体積メッシュ(全セルを多面体として)
  static VtuWriter polyhedra(const PolyMesh& mesh);
  // 境界面(全パッチ、または patch_ids に挙げたパッチだけ)を多角形として。
  // セル値として patch_id(パッチ番号)と face_id(元の面番号)を自動で付ける。
  static VtuWriter boundary_polygons(const PolyMesh& mesh, std::span<const int> patch_ids = {});

  Index n_cells() const { return static_cast<Index>(cell_types_.size()); }

  // セル値を加える。名前は ASCII にする(ParaView の表示名)。長さは n_cells() × 成分数。
  void add_cell_scalar(const std::string& name, std::span<const double> values);
  void add_cell_scalar(const std::string& name, std::span<const float> values);
  void add_cell_scalar(const std::string& name, std::span<const std::int32_t> values);
  void add_cell_vector(const std::string& name, std::span<const Vec3> values);
  // 成分数を指定する(6 なら対称テンソル (xx, yy, zz, xy, yz, xz) として扱われる)
  void add_cell_field(const std::string& name, int n_components, std::span<const double> values);

  // 書き出し(親ディレクトリは作る)
  void write(const std::filesystem::path& path, VtuEncoding encoding = VtuEncoding::AppendedRaw) const;

 private:
  enum class DataType { Float64, Float32, Int32, Int64, UInt8 };
  struct DataArray {
    std::string name;
    DataType type = DataType::Float64;
    int n_components = 1;
    std::vector<unsigned char> bytes;  // リトルエンディアンの生データ
    std::size_t count = 0;             // 要素数(成分を含む)
  };

  std::vector<Vec3> points_;
  std::vector<std::int64_t> connectivity_;
  std::vector<std::int64_t> offsets_;
  std::vector<std::uint8_t> cell_types_;
  std::vector<std::int64_t> faces_;        // 多面体の面の並び(多面体があるときだけ)
  std::vector<std::int64_t> faceoffsets_;
  std::vector<DataArray> cell_data_;

  template <class T>
  static DataArray make_array(const std::string& name, DataType type, int n_components,
                              std::span<const T> values);
  void check_length(const std::string& name, std::size_t length, int n_components) const;
};

}  // namespace gsis
