#include "support/box_mesh.hpp"

#include <algorithm>
#include <cmath>
#include <format>
#include <map>
#include <random>

#include "core/error.hpp"
#include "mesh/geometry.hpp"

namespace gsis::test {
namespace {

// 軸方向の節点座標(等比の格子間隔)
std::vector<double> axis_coordinates(int n, double lo, double hi, double ratio) {
  std::vector<double> x(static_cast<std::size_t>(n) + 1);
  const double length = hi - lo;
  for (int i = 0; i <= n; ++i) {
    double t;
    if (std::abs(ratio - 1.0) < 1e-14) {
      t = static_cast<double>(i) / n;
    } else {
      t = (std::pow(ratio, i) - 1.0) / (std::pow(ratio, n) - 1.0);
    }
    x[static_cast<std::size_t>(i)] = lo + length * t;
  }
  x.front() = lo;
  x.back() = hi;
  return x;
}

}  // namespace

BoxMeshOptions tube_like_box(std::array<int, 3> n, Vec3 lo, Vec3 hi) {
  BoxMeshOptions o;
  o.n = n;
  o.lo = lo;
  o.hi = hi;
  o.patch_names = {"inlet", "outlet", "wall", "wall", "wall", "wall"};
  o.patch_kinds = {"inlet", "outlet", "wall", "wall", "wall", "wall"};
  return o;
}

PolyMesh make_box_mesh(const BoxMeshOptions& o) {
  const int nx = o.n[0];
  const int ny = o.n[1];
  const int nz = o.n[2];
  GSIS_CHECK(nx >= 1 && ny >= 1 && nz >= 1, "箱のセル数は 1 以上にしてください");
  GSIS_CHECK(o.distortion >= 0.0 && o.distortion <= 0.3, "歪みの量は [0, 0.3] にしてください");

  std::array<std::vector<double>, 3> axis;
  for (int a = 0; a < 3; ++a) {
    axis[static_cast<std::size_t>(a)] =
        axis_coordinates(o.n[static_cast<std::size_t>(a)], o.lo[a], o.hi[a], o.grading[static_cast<std::size_t>(a)]);
  }
  auto node = [&](int i, int j, int k) { return static_cast<Index>(i + (nx + 1) * (j + (ny + 1) * k)); };

  std::vector<Vec3> points(static_cast<std::size_t>((nx + 1) * (ny + 1) * (nz + 1)));
  std::mt19937 rng(o.seed);
  std::uniform_real_distribution<double> uni(-1.0, 1.0);
  for (int k = 0; k <= nz; ++k) {
    for (int j = 0; j <= ny; ++j) {
      for (int i = 0; i <= nx; ++i) {
        const std::array<int, 3> ijk = {i, j, k};
        Vec3 p{axis[0][static_cast<std::size_t>(i)], axis[1][static_cast<std::size_t>(j)],
               axis[2][static_cast<std::size_t>(k)]};
        for (int a = 0; a < 3; ++a) {
          const auto as = static_cast<std::size_t>(a);
          const double r = uni(rng);  // 乱数列を節点の並びだけで決めるため、常に引く
          const int idx = ijk[as];
          if (o.distortion > 0.0 && idx > 0 && idx < o.n[as]) {
            const auto& x = axis[as];
            const double h = std::min(x[static_cast<std::size_t>(idx)] - x[static_cast<std::size_t>(idx) - 1],
                                      x[static_cast<std::size_t>(idx) + 1] - x[static_cast<std::size_t>(idx)]);
            p[a] += o.distortion * h * r;
          }
        }
        points[static_cast<std::size_t>(node(i, j, k))] = p;
      }
    }
  }

  std::vector<std::vector<std::vector<Index>>> cells;
  cells.reserve(static_cast<std::size_t>(nx * ny * nz) * (o.tetrahedra ? 6 : 1));
  for (int k = 0; k < nz; ++k) {
    for (int j = 0; j < ny; ++j) {
      for (int i = 0; i < nx; ++i) {
        const Index v0 = node(i, j, k), v1 = node(i + 1, j, k), v2 = node(i + 1, j + 1, k),
                    v3 = node(i, j + 1, k), v4 = node(i, j, k + 1), v5 = node(i + 1, j, k + 1),
                    v6 = node(i + 1, j + 1, k + 1), v7 = node(i, j + 1, k + 1);
        if (!o.tetrahedra) {
          cells.push_back({{v0, v4, v7, v3},
                           {v1, v2, v6, v5},
                           {v0, v1, v5, v4},
                           {v3, v7, v6, v2},
                           {v0, v3, v2, v1},
                           {v4, v5, v6, v7}});
          continue;
        }
        // Kuhn 分割: v(0,0,0) から v(1,1,1) へ軸を 1 本ずつ進む 6 通りの経路
        auto corner = [&](int di, int dj, int dk) { return node(i + di, j + dj, k + dk); };
        const int perms[6][3] = {{0, 1, 2}, {0, 2, 1}, {1, 0, 2}, {1, 2, 0}, {2, 0, 1}, {2, 1, 0}};
        for (const auto& perm : perms) {
          int d[3] = {0, 0, 0};
          Index tet[4];
          tet[0] = corner(0, 0, 0);
          for (int s = 0; s < 3; ++s) {
            d[perm[s]] = 1;
            tet[s + 1] = corner(d[0], d[1], d[2]);
          }
          Index A = tet[0], B = tet[1], C = tet[2], D = tet[3];
          const Vec3& pa = points[static_cast<std::size_t>(A)];
          const double vol = dot(points[static_cast<std::size_t>(B)] - pa,
                                 cross(points[static_cast<std::size_t>(C)] - pa, points[static_cast<std::size_t>(D)] - pa));
          if (vol < 0.0) std::swap(B, C);
          cells.push_back({{A, C, B}, {A, B, D}, {A, D, C}, {B, C, D}});
        }
      }
    }
  }

  // パッチ: 同じ名前の側面をまとめる
  std::vector<std::pair<std::string, std::string>> patches;
  std::array<int, 6> side_to_patch{};
  for (int s = 0; s < 6; ++s) {
    const auto ss = static_cast<std::size_t>(s);
    int found = -1;
    for (std::size_t p = 0; p < patches.size(); ++p) {
      if (patches[p].first == o.patch_names[ss]) found = static_cast<int>(p);
    }
    if (found < 0) {
      found = static_cast<int>(patches.size());
      patches.emplace_back(o.patch_names[ss], o.patch_kinds[ss]);
    }
    side_to_patch[ss] = found;
  }
  const Vec3 size = o.hi - o.lo;
  const double tol = 1e-9 * std::max({size.x, size.y, size.z});
  auto classify = [&](const Vec3& c) {
    for (int a = 0; a < 3; ++a) {
      if (std::abs(c[a] - o.lo[a]) <= tol) return side_to_patch[static_cast<std::size_t>(2 * a)];
      if (std::abs(c[a] - o.hi[a]) <= tol) return side_to_patch[static_cast<std::size_t>(2 * a + 1)];
    }
    return -1;
  };
  return build_poly_mesh(std::move(points), cells, patches, classify);
}

PolyMesh build_poly_mesh(std::vector<Vec3> points,
                         const std::vector<std::vector<std::vector<Index>>>& cells,
                         const std::vector<std::pair<std::string, std::string>>& patches,
                         const std::function<int(const Vec3&)>& classify) {
  struct FaceRec {
    std::vector<Index> nodes;
    Index cell;
  };
  std::map<std::vector<Index>, std::vector<FaceRec>> by_key;
  for (std::size_t c = 0; c < cells.size(); ++c) {
    for (const std::vector<Index>& face : cells[c]) {
      std::vector<Index> key = face;
      std::sort(key.begin(), key.end());
      by_key[key].push_back({face, static_cast<Index>(c)});
    }
  }

  struct InternalFace {
    Index owner;
    Index neighbour;
    const std::vector<Index>* key;
    const std::vector<Index>* nodes;
  };
  struct BoundaryFace {
    int patch;
    Index owner;
    const std::vector<Index>* key;
    const std::vector<Index>* nodes;
  };
  std::vector<InternalFace> internal;
  std::vector<BoundaryFace> boundary;
  for (const auto& [key, recs] : by_key) {
    if (recs.size() == 2) {
      GSIS_CHECK(recs[0].cell != recs[1].cell, "同じセルに同じ面が 2 回あります");
      const FaceRec& own = recs[0].cell < recs[1].cell ? recs[0] : recs[1];
      const FaceRec& nei = recs[0].cell < recs[1].cell ? recs[1] : recs[0];
      internal.push_back({own.cell, nei.cell, &key, &own.nodes});
    } else if (recs.size() == 1) {
      const Vec3 center = compute_face_geometry(points, recs[0].nodes).center;
      const int p = classify(center);
      GSIS_CHECK(p >= 0 && static_cast<std::size_t>(p) < patches.size(),
                 std::format("境界面(中心 {}, {}, {})のパッチが決まりません", center.x, center.y, center.z));
      boundary.push_back({p, recs[0].cell, &key, &recs[0].nodes});
    } else {
      GSIS_FAIL("3 つ以上のセルが同じ面を共有しています");
    }
  }
  std::sort(internal.begin(), internal.end(), [](const InternalFace& a, const InternalFace& b) {
    if (a.owner != b.owner) return a.owner < b.owner;
    if (a.neighbour != b.neighbour) return a.neighbour < b.neighbour;
    return *a.key < *b.key;
  });
  std::sort(boundary.begin(), boundary.end(), [](const BoundaryFace& a, const BoundaryFace& b) {
    if (a.patch != b.patch) return a.patch < b.patch;
    if (a.owner != b.owner) return a.owner < b.owner;
    return *a.key < *b.key;
  });

  PolyMesh mesh;
  mesh.points = std::move(points);
  mesh.n_cells = static_cast<Index>(cells.size());
  auto append = [&](const std::vector<Index>& nodes) {
    mesh.face_nodes.insert(mesh.face_nodes.end(), nodes.begin(), nodes.end());
    mesh.face_offsets.push_back(static_cast<Offset>(mesh.face_nodes.size()));
  };
  for (const InternalFace& f : internal) {
    append(*f.nodes);
    mesh.owner.push_back(f.owner);
    mesh.neighbour.push_back(f.neighbour);
  }
  std::size_t bi = 0;
  for (std::size_t p = 0; p < patches.size(); ++p) {
    Patch patch;
    patch.name = patches[p].first;
    patch.kind = patches[p].second;
    patch.start = static_cast<Index>(mesh.owner.size());
    while (bi < boundary.size() && boundary[bi].patch == static_cast<int>(p)) {
      append(*boundary[bi].nodes);
      mesh.owner.push_back(boundary[bi].owner);
      ++bi;
    }
    patch.count = static_cast<Index>(mesh.owner.size()) - patch.start;
    mesh.patches.push_back(std::move(patch));
  }
  mesh.metadata = {{"generator", "gsis tests (box_mesh)"}};
  validate_topology(mesh);
  return mesh;
}

}  // namespace gsis::test
