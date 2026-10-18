#pragma once
// 断面を通る流量の集計(フェーズ 5c、docs/design_gsis.md §6a)。
//
// 軸 axis に垂直な断面 X = position を、セル中心がその手前(x_c < X)と先(x_c ≥ X)に分かれる 2 つのセルの間の
// 内部面の集まりで表す(一般のメッシュでは階段状の面、軸にそろった構造格子では平面)。この面は領域を
// 「x_c < X のセルの集まり」とその残りに分けるので、定常で保存的な離散化なら、面を通る離散的な流束の和は
// 入口・出口を通る流量と同じになる(壁は流束 0)。管なら X = 0〜10 の断面は管の中の面の層になる。
// 面の流束は owner → neighbour の向き(面積ベクトルの向き)で与え、sign を掛けて軸の正の向きの流量にする。

#include <span>
#include <string>
#include <string_view>
#include <vector>

#include "core/types.hpp"
#include "mesh/geometry.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

// 断面の指定(位置は L_ref 単位)
struct SectionSpec {
  int axis = 0;                   // 0 = x、1 = y、2 = z
  std::vector<double> positions;  // 並びはそのまま(重複も許す)
  bool empty() const { return positions.empty(); }
  bool operator==(const SectionSpec&) const = default;
};

// "0, 2.5, 5" の形の位置の並び(カンマ・セミコロン・空白で区切る)と軸の名前("x"・"y"・"z")から作る。
// 空の文字列なら断面なし。数値でない・有限でない値は gsis::Error
SectionSpec parse_section_spec(std::string_view positions, std::string_view axis = "x");
// 軸の名前("x"・"y"・"z")
const char* section_axis_name(int axis);

struct SectionCut {
  double position = 0.0;          // 指定した位置(L_ref 単位)
  int axis = 0;
  std::vector<Index> faces;       // 内部面(このメッシュの番号)
  std::vector<signed char> sign;  // +1: 面の向き(owner → neighbour)が手前 → 先(owner の中心が X より手前)、−1: 逆
  double area = 0.0;              // 面の面積の和(L_ref² 単位。階段状なら投影面積より大きい)
  double projected_area = 0.0;    // 軸の向きの面積ベクトルの成分の和 Σ sign S_axis(L_ref² 単位)
  double face_position = 0.0;     // 面の中心の軸の座標の面積平均(L_ref 単位。面がなければ position)
};

// geometry は SI(長さ m)、length_scale は L_ref [m]
std::vector<SectionCut> build_section_cuts(const PolyMesh& mesh, const MeshGeometry& geometry, double length_scale,
                                           const SectionSpec& spec);

// 断面を通る流量 Σ sign_k F_k(face_flux は内部面の番号順の owner → neighbour の向きの流束)
double section_flow(const SectionCut& cut, std::span<const double> face_flux);

}  // namespace gsis
