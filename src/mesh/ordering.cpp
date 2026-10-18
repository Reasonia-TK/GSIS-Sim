#include "mesh/ordering.hpp"

#include <algorithm>
#include <cstdlib>
#include <format>
#include <numeric>

#include "core/error.hpp"

namespace gsis {

CellAdjacency build_cell_adjacency(const PolyMesh& mesh) {
  const auto n_cells = static_cast<std::size_t>(mesh.n_cells);
  const Index n_internal = mesh.n_internal_faces();
  CellAdjacency adj;
  adj.offsets.assign(n_cells + 1, 0);
  for (Index f = 0; f < n_internal; ++f) {
    ++adj.offsets[static_cast<std::size_t>(mesh.owner[static_cast<std::size_t>(f)]) + 1];
    ++adj.offsets[static_cast<std::size_t>(mesh.neighbour[static_cast<std::size_t>(f)]) + 1];
  }
  for (std::size_t c = 0; c < n_cells; ++c) adj.offsets[c + 1] += adj.offsets[c];
  std::vector<Index> raw(static_cast<std::size_t>(adj.offsets.back()));
  std::vector<Offset> fill(adj.offsets.begin(), adj.offsets.end() - 1);
  for (Index f = 0; f < n_internal; ++f) {
    const Index o = mesh.owner[static_cast<std::size_t>(f)];
    const Index n = mesh.neighbour[static_cast<std::size_t>(f)];
    raw[static_cast<std::size_t>(fill[static_cast<std::size_t>(o)]++)] = n;
    raw[static_cast<std::size_t>(fill[static_cast<std::size_t>(n)]++)] = o;
  }
  // セルごとに昇順・重複なしにする(同じ 2 セルが複数の面を共有することがある)
  CellAdjacency out;
  out.offsets.assign(n_cells + 1, 0);
  out.cells.reserve(raw.size());
  for (std::size_t c = 0; c < n_cells; ++c) {
    auto b = raw.begin() + adj.offsets[c];
    auto e = raw.begin() + adj.offsets[c + 1];
    std::sort(b, e);
    e = std::unique(b, e);
    out.cells.insert(out.cells.end(), b, e);
    out.offsets[c + 1] = static_cast<Offset>(out.cells.size());
  }
  return out;
}

namespace {

// start から幅優先探索した各段(レベル)。探索のたびに stamp を変え、前回の印と区別する。
std::vector<std::vector<Index>> bfs_levels(const CellAdjacency& adj, Index start,
                                           std::vector<long long>& visited, long long stamp) {
  std::vector<std::vector<Index>> levels;
  levels.push_back({start});
  visited[static_cast<std::size_t>(start)] = stamp;
  while (true) {
    std::vector<Index> next;
    for (const Index c : levels.back()) {
      for (Offset k = adj.offsets[static_cast<std::size_t>(c)];
           k < adj.offsets[static_cast<std::size_t>(c) + 1]; ++k) {
        const Index nb = adj.cells[static_cast<std::size_t>(k)];
        if (visited[static_cast<std::size_t>(nb)] != stamp) {
          visited[static_cast<std::size_t>(nb)] = stamp;
          next.push_back(nb);
        }
      }
    }
    if (next.empty()) break;
    levels.push_back(std::move(next));
  }
  return levels;
}

}  // namespace

std::vector<Index> rcm_ordering(const PolyMesh& mesh) {
  const CellAdjacency adj = build_cell_adjacency(mesh);
  const Index n = mesh.n_cells;
  std::vector<Index> order;
  order.reserve(static_cast<std::size_t>(n));
  std::vector<char> placed(static_cast<std::size_t>(n), 0);
  // 擬似周辺点の探索用の印(探索ごとに stamp を 1 つ進める)
  std::vector<long long> visited(static_cast<std::size_t>(n), -1);
  long long stamp = 0;

  // 次数の小さい順(同じなら番号の小さい順)に、未処理の成分の開始候補を探す
  std::vector<Index> by_degree(static_cast<std::size_t>(n));
  std::iota(by_degree.begin(), by_degree.end(), 0);
  std::stable_sort(by_degree.begin(), by_degree.end(),
                   [&](Index a, Index b) { return adj.degree(a) < adj.degree(b); });

  for (const Index seed : by_degree) {
    if (placed[static_cast<std::size_t>(seed)]) continue;

    // 擬似周辺点(George–Liu): 最後の段で次数最小の点へ移り、離心率が増えなくなるまで繰り返す
    Index start = seed;
    auto levels = bfs_levels(adj, start, visited, ++stamp);
    for (int iter = 0; iter < 16; ++iter) {
      const std::vector<Index>& last = levels.back();
      Index cand = last.front();
      for (const Index c : last) {
        if (adj.degree(c) < adj.degree(cand) || (adj.degree(c) == adj.degree(cand) && c < cand)) {
          cand = c;
        }
      }
      auto cand_levels = bfs_levels(adj, cand, visited, ++stamp);
      if (cand_levels.size() <= levels.size()) break;
      start = cand;
      levels = std::move(cand_levels);
    }

    // Cuthill–McKee: 幅優先で、各点の未処理の隣接点を (次数, 番号) の昇順に加える
    const std::size_t first = order.size();
    order.push_back(start);
    placed[static_cast<std::size_t>(start)] = 1;
    std::vector<Index> nbrs;
    for (std::size_t head = first; head < order.size(); ++head) {
      const Index c = order[head];
      nbrs.clear();
      for (Offset k = adj.offsets[static_cast<std::size_t>(c)];
           k < adj.offsets[static_cast<std::size_t>(c) + 1]; ++k) {
        const Index nb = adj.cells[static_cast<std::size_t>(k)];
        if (!placed[static_cast<std::size_t>(nb)]) {
          placed[static_cast<std::size_t>(nb)] = 1;
          nbrs.push_back(nb);
        }
      }
      std::sort(nbrs.begin(), nbrs.end(), [&](Index a, Index b) {
        return adj.degree(a) != adj.degree(b) ? adj.degree(a) < adj.degree(b) : a < b;
      });
      order.insert(order.end(), nbrs.begin(), nbrs.end());
    }
  }
  GSIS_ASSERT(order.size() == static_cast<std::size_t>(n));
  std::reverse(order.begin(), order.end());
  return order;
}

std::vector<Index> invert_permutation(std::span<const Index> perm) {
  const auto n = perm.size();
  std::vector<Index> inv(n, kInvalidIndex);
  for (std::size_t i = 0; i < n; ++i) {
    const Index p = perm[i];
    GSIS_CHECK(p >= 0 && static_cast<std::size_t>(p) < n && inv[static_cast<std::size_t>(p)] == kInvalidIndex,
               std::format("置換ではありません(位置 {} の値 {})", i, p));
    inv[static_cast<std::size_t>(p)] = static_cast<Index>(i);
  }
  return inv;
}

PolyMesh renumber_cells(const PolyMesh& mesh, std::span<const Index> new_to_old) {
  GSIS_CHECK(new_to_old.size() == static_cast<std::size_t>(mesh.n_cells),
             std::format("置換の長さ {} がセル数 {} と違います", new_to_old.size(), mesh.n_cells));
  const std::vector<Index> old_to_new = invert_permutation(new_to_old);
  const Index n_faces = mesh.n_faces();
  const Index n_internal = mesh.n_internal_faces();

  struct FaceRef {
    Index old_face;
    Index owner;
    Index neighbour;
    bool flip;
  };
  std::vector<FaceRef> internal(static_cast<std::size_t>(n_internal));
  for (Index f = 0; f < n_internal; ++f) {
    Index o = old_to_new[static_cast<std::size_t>(mesh.owner[static_cast<std::size_t>(f)])];
    Index n = old_to_new[static_cast<std::size_t>(mesh.neighbour[static_cast<std::size_t>(f)])];
    const bool flip = o > n;
    if (flip) std::swap(o, n);
    internal[static_cast<std::size_t>(f)] = {f, o, n, flip};
  }
  std::stable_sort(internal.begin(), internal.end(), [](const FaceRef& a, const FaceRef& b) {
    return a.owner != b.owner ? a.owner < b.owner : a.neighbour < b.neighbour;
  });

  PolyMesh out;
  out.points = mesh.points;
  out.patches = mesh.patches;
  out.n_cells = mesh.n_cells;
  out.length_unit = mesh.length_unit;
  out.metadata = mesh.metadata;
  out.face_offsets.assign(1, 0);
  out.face_offsets.reserve(static_cast<std::size_t>(n_faces) + 1);
  out.face_nodes.reserve(mesh.face_nodes.size());
  out.owner.reserve(static_cast<std::size_t>(n_faces));
  out.neighbour.reserve(static_cast<std::size_t>(n_internal));

  auto append_face = [&](Index old_face, bool flip) {
    const std::span<const Index> nodes = mesh.face(old_face);
    if (!flip) {
      out.face_nodes.insert(out.face_nodes.end(), nodes.begin(), nodes.end());
    } else {
      // 先頭の節点を保ったまま向きを逆にする: [p0, p_{n-1}, ..., p1]
      out.face_nodes.push_back(nodes[0]);
      for (std::size_t k = nodes.size() - 1; k >= 1; --k) out.face_nodes.push_back(nodes[k]);
    }
    out.face_offsets.push_back(static_cast<Offset>(out.face_nodes.size()));
  };

  for (const FaceRef& r : internal) {
    append_face(r.old_face, r.flip);
    out.owner.push_back(r.owner);
    out.neighbour.push_back(r.neighbour);
  }
  for (const Patch& p : mesh.patches) {
    std::vector<std::pair<Index, Index>> faces;  // (新しい owner, 元の面)
    faces.reserve(static_cast<std::size_t>(p.count));
    for (Index f = p.start; f < p.end(); ++f) {
      faces.emplace_back(old_to_new[static_cast<std::size_t>(mesh.owner[static_cast<std::size_t>(f)])], f);
    }
    std::stable_sort(faces.begin(), faces.end(),
                     [](const auto& a, const auto& b) { return a.first < b.first; });
    for (const auto& [o, f] : faces) {
      append_face(f, false);
      out.owner.push_back(o);
    }
  }
  GSIS_ASSERT(out.n_faces() == n_faces);
  return out;
}

Index matrix_bandwidth(const PolyMesh& mesh) {
  Index bw = 0;
  for (Index f = 0; f < mesh.n_internal_faces(); ++f) {
    bw = std::max(bw, static_cast<Index>(std::abs(mesh.neighbour[static_cast<std::size_t>(f)] -
                                                  mesh.owner[static_cast<std::size_t>(f)])));
  }
  return bw;
}

std::vector<Index> partition_contiguous(Index n_cells, int n_blocks) {
  GSIS_CHECK(n_blocks >= 1, std::format("ブロック数 {} は 1 以上にしてください", n_blocks));
  GSIS_CHECK(n_cells >= n_blocks,
             std::format("ブロック数 {} がセル数 {} より多いです", n_blocks, n_cells));
  std::vector<Index> offsets(static_cast<std::size_t>(n_blocks) + 1);
  const Index base = n_cells / n_blocks;
  const Index rem = n_cells % n_blocks;
  offsets[0] = 0;
  for (int b = 0; b < n_blocks; ++b) {
    offsets[static_cast<std::size_t>(b) + 1] = offsets[static_cast<std::size_t>(b)] + base + (b < rem ? 1 : 0);
  }
  return offsets;
}

std::vector<Index> partition_contiguous_weighted(std::span<const double> weights, int n_blocks) {
  const auto n = static_cast<Index>(weights.size());
  GSIS_CHECK(n_blocks >= 1, std::format("ブロック数 {} は 1 以上にしてください", n_blocks));
  GSIS_CHECK(n >= n_blocks, std::format("ブロック数 {} がセル数 {} より多いです", n_blocks, n));
  std::vector<double> prefix(weights.size() + 1, 0.0);
  for (std::size_t i = 0; i < weights.size(); ++i) {
    GSIS_CHECK(weights[i] >= 0.0, "重みは 0 以上にしてください");
    prefix[i + 1] = prefix[i] + weights[i];
  }
  const double total = prefix.back();
  std::vector<Index> offsets(static_cast<std::size_t>(n_blocks) + 1, 0);
  offsets.back() = n;
  for (int b = 1; b < n_blocks; ++b) {
    const double target = total * b / n_blocks;
    // prefix[i] >= target となる最小の i(各ブロックに 1 セル以上残す)
    auto it = std::lower_bound(prefix.begin(), prefix.end(), target);
    Index i = static_cast<Index>(it - prefix.begin());
    i = std::clamp(i, offsets[static_cast<std::size_t>(b) - 1] + 1, n - (n_blocks - b));
    offsets[static_cast<std::size_t>(b)] = i;
  }
  return offsets;
}

BlockPartitionStats block_partition_stats(const PolyMesh& mesh, std::span<const Index> block_offsets) {
  GSIS_CHECK(block_offsets.size() >= 2 && block_offsets.front() == 0 &&
                 block_offsets.back() == mesh.n_cells,
             "ブロックの区間がセル全体を覆っていません");
  BlockPartitionStats s;
  s.n_blocks = static_cast<int>(block_offsets.size()) - 1;
  s.min_cells = mesh.n_cells;
  for (int b = 0; b < s.n_blocks; ++b) {
    const Index size = block_offsets[static_cast<std::size_t>(b) + 1] - block_offsets[static_cast<std::size_t>(b)];
    GSIS_CHECK(size >= 0, "ブロックの区間が逆順です");
    s.min_cells = std::min(s.min_cells, size);
    s.max_cells = std::max(s.max_cells, size);
  }
  const std::vector<int> block = cell_block_ids(block_offsets);
  for (Index f = 0; f < mesh.n_internal_faces(); ++f) {
    if (block[static_cast<std::size_t>(mesh.owner[static_cast<std::size_t>(f)])] !=
        block[static_cast<std::size_t>(mesh.neighbour[static_cast<std::size_t>(f)])]) {
      ++s.n_cut_faces;
    }
  }
  return s;
}

std::vector<int> cell_block_ids(std::span<const Index> block_offsets) {
  const Index n_cells = block_offsets.back();
  std::vector<int> ids(static_cast<std::size_t>(n_cells), 0);
  for (std::size_t b = 0; b + 1 < block_offsets.size(); ++b) {
    for (Index c = block_offsets[b]; c < block_offsets[b + 1]; ++c) {
      ids[static_cast<std::size_t>(c)] = static_cast<int>(b);
    }
  }
  return ids;
}

}  // namespace gsis
