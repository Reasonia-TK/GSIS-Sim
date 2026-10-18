#pragma once
// テスト用のメッシュ生成: 構造格子の箱(一様・等比・歪み・四面体分割)と、
// 「セル = 外向きの面の集まり」から pmesh の規約どおりの面ベースメッシュを組み立てる関数。

#include <array>
#include <functional>
#include <string>
#include <utility>
#include <vector>

#include "core/vec3.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis::test {

struct BoxMeshOptions {
  std::array<int, 3> n{4, 4, 4};                 // 各軸のセル数
  Vec3 lo{0.0, 0.0, 0.0};
  Vec3 hi{1.0, 1.0, 1.0};
  std::array<double, 3> grading{1.0, 1.0, 1.0};  // 隣り合う格子間隔の比(1 なら一様)
  double distortion = 0.0;  // 節点を乱数で動かす量(局所の格子間隔に対する割合、0.3 以下)
  unsigned seed = 1;
  bool tetrahedra = false;  // 各六面体を 6 個の四面体に分ける(Kuhn 分割。隣とつながる)
  // 6 つの側面(x−, x+, y−, y+, z−, z+)のパッチ名と種類。同じ名前の側面は 1 つのパッチにまとめる。
  std::array<std::string, 6> patch_names{"xmin", "xmax", "ymin", "ymax", "zmin", "zmax"};
  std::array<std::string, 6> patch_kinds{"patch", "patch", "patch", "patch", "patch", "patch"};
};

// 箱のメッシュ。歪ませるときは内部の節点を全方向に、側面上の節点を面内だけで動かす
// (箱の形は変わらないので、総体積と側面の面積は厳密に保たれる)。
PolyMesh make_box_mesh(const BoxMeshOptions& options);

// 管に似せた名前付け(x− が inlet、x+ が outlet、それ以外が wall)
BoxMeshOptions tube_like_box(std::array<int, 3> n, Vec3 lo, Vec3 hi);

// セルの一覧から面ベースのメッシュを組み立てる。
//   cells[c] はセル c の面の一覧、各面は外向き(右ねじ)の順の節点番号。
//   patches はパッチの (名前, 種類) の一覧、classify は境界面の中心からパッチの番号を返す
//   (範囲外を返したら gsis::Error)。
// 内部面は (owner, neighbour) の順、境界面はパッチの順(パッチの中は owner の順)に並べる。
PolyMesh build_poly_mesh(std::vector<Vec3> points,
                         const std::vector<std::vector<std::vector<Index>>>& cells,
                         const std::vector<std::pair<std::string, std::string>>& patches,
                         const std::function<int(const Vec3&)>& classify);

}  // namespace gsis::test
