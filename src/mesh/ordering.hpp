#pragma once
// LU-SGS のためのセルの並べ替え(RCM)と、連続区間でのブロック分割(spec §3.5)。
//
// 使い方の例:
//   auto order = rcm_ordering(mesh);                 // new_to_old
//   PolyMesh m2 = renumber_cells(mesh, order);       // 規約(上三角順など)を保って番号を付け替える
//   auto blocks = partition_contiguous(m2.n_cells, P);  // ブロック b は [blocks[b], blocks[b+1])

#include <span>
#include <vector>

#include "core/types.hpp"
#include "mesh/poly_mesh.hpp"

namespace gsis {

// セルの隣接(内部面でつながるセル)。重複なし・昇順。
struct CellAdjacency {
  std::vector<Offset> offsets;  // 長さ n_cells + 1
  std::vector<Index> cells;
  Index degree(Index c) const {
    return static_cast<Index>(offsets[static_cast<std::size_t>(c) + 1] -
                              offsets[static_cast<std::size_t>(c)]);
  }
};

CellAdjacency build_cell_adjacency(const PolyMesh& mesh);

// Reverse Cuthill–McKee の順序。返り値は new_to_old(新しい番号 i のセルの元の番号)。
// 連結成分ごとに擬似周辺点から始め、同順位は (次数, 番号) の小さい順で決める(結果は決定的)。
std::vector<Index> rcm_ordering(const PolyMesh& mesh);

// 置換の逆(new_to_old → old_to_new)。置換でなければ gsis::Error。
std::vector<Index> invert_permutation(std::span<const Index> perm);

// セル番号を付け替えたメッシュを作る(new_to_old で指定)。
// 内部面は owner < neighbour になるよう向きを直し((owner, neighbour) の順に並べ直す)、
// 境界面はパッチの中で新しい owner の順に並べる。節点とパッチの区間は変わらない。
PolyMesh renumber_cells(const PolyMesh& mesh, std::span<const Index> new_to_old);

// 行列の帯幅 max |owner − neighbour|(内部面)
Index matrix_bandwidth(const PolyMesh& mesh);

// [0, n_cells) を n_blocks 個の連続区間に分ける(大きさの差は 1 以内)。返り値の長さは n_blocks + 1。
std::vector<Index> partition_contiguous(Index n_cells, int n_blocks);

// 重み(例: セルの面数)の和がそろうように連続区間に分ける。返り値の長さは n_blocks + 1。
std::vector<Index> partition_contiguous_weighted(std::span<const double> weights, int n_blocks);

struct BlockPartitionStats {
  int n_blocks = 0;
  Index min_cells = 0;
  Index max_cells = 0;
  Index n_cut_faces = 0;  // 異なるブロックをつなぐ内部面の数(ブロック・ヤコビで結合される面)
};

BlockPartitionStats block_partition_stats(const PolyMesh& mesh, std::span<const Index> block_offsets);

// セルが属するブロック番号(block_offsets は partition_* の返り値)
std::vector<int> cell_block_ids(std::span<const Index> block_offsets);

}  // namespace gsis
