#include "kinetic/kinetic_setup.hpp"

#include <algorithm>
#include <cmath>
#include <format>
#include <map>
#include <numeric>
#include <set>

#include "config/case_mesh_check.hpp"
#include "core/error.hpp"
#include "core/log.hpp"
#include "mesh/ordering.hpp"

namespace gsis::kinetic {
namespace {

// 並べ替えた後のメッシュの境界面と元の境界面を、節点の並びで対応づける
// (renumber_cells は境界面の向きを変えず、同じパッチの中で並べ替えるだけ)
std::vector<Index> match_boundary_faces(const PolyMesh& original, const PolyMesh& renumbered) {
  const Index n_int_o = original.n_internal_faces();
  const Index n_int_r = renumbered.n_internal_faces();
  GSIS_ASSERT(n_int_o == n_int_r && original.patches.size() == renumbered.patches.size());
  std::vector<Index> new_to_old(static_cast<std::size_t>(original.n_boundary_faces()), kInvalidIndex);
  for (std::size_t pi = 0; pi < original.patches.size(); ++pi) {
    const Patch& po = original.patches[pi];
    const Patch& pr = renumbered.patches[pi];
    GSIS_ASSERT(po.start == pr.start && po.count == pr.count);
    std::map<std::vector<Index>, Index> index;
    for (Index f = po.start; f < po.end(); ++f) {
      const auto nodes = original.face(f);
      index.emplace(std::vector<Index>(nodes.begin(), nodes.end()), f);
    }
    for (Index f = pr.start; f < pr.end(); ++f) {
      const auto nodes = renumbered.face(f);
      const auto it = index.find(std::vector<Index>(nodes.begin(), nodes.end()));
      GSIS_CHECK(it != index.end(), std::format("並べ替えた境界面 {} が元のメッシュに見つかりません", f));
      new_to_old[static_cast<std::size_t>(f - n_int_r)] = it->second - n_int_o;
    }
  }
  return new_to_old;
}

// 再帰的な座標二分割(RCB): cells を P 個のコンパクトなブロックに分ける(境界ボックスの最も長い軸で、
// セル数がブロック数に比例するように分ける)。同順位は (座標, セル番号) で決める(結果は決定的)
void bisect(std::vector<Index>& cells, int P, const std::vector<Vec3>& centers, std::vector<std::vector<Index>>& out) {
  if (P <= 1 || cells.size() <= 1) {
    out.push_back(cells);
    return;
  }
  Vec3 lo = centers[static_cast<std::size_t>(cells.front())];
  Vec3 hi = lo;
  for (const Index c : cells) {
    const Vec3& x = centers[static_cast<std::size_t>(c)];
    for (int a = 0; a < 3; ++a) {
      lo[a] = std::min(lo[a], x[a]);
      hi[a] = std::max(hi[a], x[a]);
    }
  }
  int axis = 0;
  for (int a = 1; a < 3; ++a) {
    if (hi[a] - lo[a] > hi[axis] - lo[axis]) axis = a;
  }
  const int P1 = P / 2;
  const int P2 = P - P1;
  const auto n1 = static_cast<std::ptrdiff_t>(
      std::llround(static_cast<double>(cells.size()) * static_cast<double>(P1) / static_cast<double>(P)));
  auto less = [&](Index a, Index b) {
    const double xa = centers[static_cast<std::size_t>(a)][axis];
    const double xb = centers[static_cast<std::size_t>(b)][axis];
    return xa != xb ? xa < xb : a < b;
  };
  std::nth_element(cells.begin(), cells.begin() + n1, cells.end(), less);
  std::vector<Index> left(cells.begin(), cells.begin() + n1);
  std::vector<Index> right(cells.begin() + n1, cells.end());
  std::sort(left.begin(), left.end());
  std::sort(right.begin(), right.end());
  bisect(left, P1, centers, out);
  bisect(right, P2, centers, out);
}

// ブロックの中を逆 Cuthill–McKee で並べる(隣接はブロックの中だけ。連結成分ごとに、次数の小さい点から
// 幅優先探索して最後の段の次数最小の点を始点にし直す)
std::vector<Index> block_rcm(const std::vector<Index>& cells, const CellAdjacency& adj, const std::vector<int>& block_of,
                             int b, std::vector<char>& placed, std::vector<long long>& mark, long long& stamp) {
  auto degree = [&](Index c) {
    int d = 0;
    for (Offset k = adj.offsets[static_cast<std::size_t>(c)]; k < adj.offsets[static_cast<std::size_t>(c) + 1]; ++k) {
      if (block_of[static_cast<std::size_t>(adj.cells[static_cast<std::size_t>(k)])] == b) ++d;
    }
    return d;
  };
  auto by_degree = [&](Index x, Index y) {
    const int dx = degree(x);
    const int dy = degree(y);
    return dx != dy ? dx < dy : x < y;
  };
  // 幅優先探索の最後の段(印 mark を使い、placed は変えない)
  auto last_level = [&](Index start) {
    ++stamp;
    std::vector<Index> level{start};
    mark[static_cast<std::size_t>(start)] = stamp;
    std::vector<Index> last = level;
    while (!level.empty()) {
      last = level;
      std::vector<Index> next;
      for (const Index c : level) {
        for (Offset k = adj.offsets[static_cast<std::size_t>(c)]; k < adj.offsets[static_cast<std::size_t>(c) + 1];
             ++k) {
          const Index nb = adj.cells[static_cast<std::size_t>(k)];
          if (block_of[static_cast<std::size_t>(nb)] == b && mark[static_cast<std::size_t>(nb)] != stamp) {
            mark[static_cast<std::size_t>(nb)] = stamp;
            next.push_back(nb);
          }
        }
      }
      level = std::move(next);
    }
    return last;
  };
  std::vector<Index> seeds = cells;
  std::sort(seeds.begin(), seeds.end(), by_degree);
  std::vector<Index> order;
  order.reserve(cells.size());
  std::vector<Index> nbrs;
  for (const Index seed : seeds) {
    if (placed[static_cast<std::size_t>(seed)]) continue;
    const std::vector<Index> last = last_level(seed);
    const Index start = *std::min_element(last.begin(), last.end(), by_degree);
    const std::size_t first = order.size();
    order.push_back(start);
    placed[static_cast<std::size_t>(start)] = 1;
    for (std::size_t head = first; head < order.size(); ++head) {
      const Index c = order[head];
      nbrs.clear();
      for (Offset k = adj.offsets[static_cast<std::size_t>(c)]; k < adj.offsets[static_cast<std::size_t>(c) + 1]; ++k) {
        const Index nb = adj.cells[static_cast<std::size_t>(k)];
        if (block_of[static_cast<std::size_t>(nb)] == b && !placed[static_cast<std::size_t>(nb)]) {
          placed[static_cast<std::size_t>(nb)] = 1;
          nbrs.push_back(nb);
        }
      }
      std::sort(nbrs.begin(), nbrs.end(), by_degree);
      order.insert(order.end(), nbrs.begin(), nbrs.end());
    }
  }
  std::reverse(order.begin(), order.end());
  return order;
}

// RCB でブロックに分け、ブロックの中を逆 Cuthill–McKee で並べた new_to_old と、ブロックの区間
std::vector<Index> rcb_ordering(const PolyMesh& mesh, const MeshGeometry& geometry, int P,
                                std::vector<Index>& block_offsets) {
  const auto n = static_cast<std::size_t>(mesh.n_cells);
  std::vector<Index> all(n);
  std::iota(all.begin(), all.end(), 0);
  std::vector<std::vector<Index>> groups;
  bisect(all, P, geometry.cell_center, groups);
  std::vector<int> block_of(n, -1);
  for (std::size_t b = 0; b < groups.size(); ++b) {
    for (const Index c : groups[b]) block_of[static_cast<std::size_t>(c)] = static_cast<int>(b);
  }
  const CellAdjacency adj = build_cell_adjacency(mesh);
  std::vector<char> placed(n, 0);
  std::vector<long long> mark(n, -1);
  long long stamp = 0;
  std::vector<Index> new_to_old;
  new_to_old.reserve(n);
  block_offsets.assign(1, 0);
  for (std::size_t b = 0; b < groups.size(); ++b) {
    const std::vector<Index> order = block_rcm(groups[b], adj, block_of, static_cast<int>(b), placed, mark, stamp);
    GSIS_ASSERT(order.size() == groups[b].size());
    new_to_old.insert(new_to_old.end(), order.begin(), order.end());
    block_offsets.push_back(static_cast<Index>(new_to_old.size()));
  }
  GSIS_ASSERT(new_to_old.size() == n);
  return new_to_old;
}

}  // namespace

std::vector<Vec3> make_sweep_directions(const std::string& sweep_order) {
  std::vector<Vec3> d;
  if (sweep_order == "octant" || sweep_order == "dir13") {
    if (sweep_order == "dir13") {
      d.push_back(Vec3{1.0, 0.0, 0.0});
      d.push_back(Vec3{0.0, 1.0, 0.0});
      d.push_back(Vec3{0.0, 0.0, 1.0});
      d.push_back(Vec3{1.0, 1.0, 0.0});
      d.push_back(Vec3{1.0, -1.0, 0.0});
      d.push_back(Vec3{1.0, 0.0, 1.0});
      d.push_back(Vec3{1.0, 0.0, -1.0});
      d.push_back(Vec3{0.0, 1.0, 1.0});
      d.push_back(Vec3{0.0, 1.0, -1.0});
    }
    d.push_back(Vec3{1.0, 1.0, 1.0});
    d.push_back(Vec3{1.0, 1.0, -1.0});
    d.push_back(Vec3{1.0, -1.0, 1.0});
    d.push_back(Vec3{1.0, -1.0, -1.0});
  } else {
    GSIS_CHECK(sweep_order == "block", std::format("知らない sweep_order \"{}\" です", sweep_order));
  }
  for (Vec3& x : d) x = x / norm(x);
  return d;
}

int sweep_direction_of(const std::vector<Vec3>& dirs, const Vec3& xi) {
  int best = 0;
  double best_v = -1.0;
  for (std::size_t g = 0; g < dirs.size(); ++g) {
    const double v = std::abs(dot(dirs[g], xi));
    if (v > best_v + 1e-12) {
      best_v = v;
      best = static_cast<int>(g);
    }
  }
  return best;
}

VelocityLayout make_velocity_layout(const VelocityGrid& grid, const std::vector<int>& mirror_axes,
                                    const std::vector<Vec3>& sweep_dirs) {
  VelocityLayout L;
  L.nv = grid.size();
  L.weight = grid.weight();
  std::vector<int> axes = mirror_axes;
  std::sort(axes.begin(), axes.end());
  axes.erase(std::unique(axes.begin(), axes.end()), axes.end());
  for (const int a : axes) {
    GSIS_CHECK(a >= 0 && a < 3, std::format("鏡映の軸 {} が不正です", a));
    GSIS_CHECK(grid.symmetric(a),
               std::format("速度格子の軸 {} の範囲が 0 に対して対称でないので、鏡映を使えません", "xyz"[a]));
    GSIS_CHECK(grid.n(a) % 2 == 0,
               std::format("対称面に使う速度格子の軸 {} の点数 {} は偶数にしてください", "xyz"[a], grid.n(a)));
  }
  const int n_axes = static_cast<int>(axes.size());
  L.orbit_size = 1 << n_axes;
  for (int r = 0; r < n_axes; ++r) L.axis_xor[static_cast<std::size_t>(axes[static_cast<std::size_t>(r)])] = 1 << r;

  L.storage_to_grid.reserve(static_cast<std::size_t>(L.nv));
  for (int k = 0; k < L.nv; ++k) {
    const std::array<int, 3> ijk = grid.multi_index(k);
    bool representative = true;
    for (const int a : axes) {
      if (ijk[static_cast<std::size_t>(a)] >= grid.n(a) / 2) representative = false;
    }
    if (!representative) continue;
    for (int m = 0; m < L.orbit_size; ++m) {
      std::array<int, 3> mm = ijk;
      for (int r = 0; r < n_axes; ++r) {
        if (m & (1 << r)) {
          const auto a = static_cast<std::size_t>(axes[static_cast<std::size_t>(r)]);
          mm[a] = grid.n(static_cast<int>(a)) - 1 - mm[a];
        }
      }
      L.storage_to_grid.push_back(grid.index(mm[0], mm[1], mm[2]));
    }
  }
  GSIS_ASSERT(static_cast<int>(L.storage_to_grid.size()) == L.nv);
  if (n_axes == 0 && !sweep_dirs.empty()) {
    // スイープの向きごとにまとめる(向きの中は速度格子の順。安定な並べ替え)
    std::vector<int> group(static_cast<std::size_t>(L.nv));
    for (int k = 0; k < L.nv; ++k) group[static_cast<std::size_t>(k)] = sweep_direction_of(sweep_dirs, grid.xi(k));
    std::stable_sort(L.storage_to_grid.begin(), L.storage_to_grid.end(), [&](int a, int b) {
      return group[static_cast<std::size_t>(a)] < group[static_cast<std::size_t>(b)];
    });
  }
  L.grid_to_storage.assign(static_cast<std::size_t>(L.nv), -1);
  L.xi_x.resize(static_cast<std::size_t>(L.nv));
  L.xi_y.resize(static_cast<std::size_t>(L.nv));
  L.xi_z.resize(static_cast<std::size_t>(L.nv));
  for (int p = 0; p < L.nv; ++p) {
    const int k = L.storage_to_grid[static_cast<std::size_t>(p)];
    GSIS_ASSERT(L.grid_to_storage[static_cast<std::size_t>(k)] < 0);
    L.grid_to_storage[static_cast<std::size_t>(k)] = p;
    const Vec3 xi = grid.xi(k);
    L.xi_x[static_cast<std::size_t>(p)] = xi.x;
    L.xi_y[static_cast<std::size_t>(p)] = xi.y;
    L.xi_z[static_cast<std::size_t>(p)] = xi.z;
    L.xi_max = std::max(L.xi_max, norm(xi));
  }
  // 鏡映が保存位置の xor になっていることを確かめる
  for (const int a : axes) {
    const std::vector<int>& mirror = grid.mirror(a);
    for (int p = 0; p < L.nv; ++p) {
      const int q = p ^ L.axis_xor[static_cast<std::size_t>(a)];
      GSIS_ASSERT(L.storage_to_grid[static_cast<std::size_t>(q)] ==
                  mirror[static_cast<std::size_t>(L.storage_to_grid[static_cast<std::size_t>(p)])]);
    }
  }
  return L;
}

KineticSetup build_kinetic_setup(const CaseConfig& config, const PolyMesh& mesh, const MeshGeometry& geometry,
                                 const KineticParams& params) {
  KineticSetup s;
  s.params = params;
  if (params.limiter == "none") {
    s.limiter_mode = kLimiterNone;
  } else {
    s.limiter_mode = params.limiter_eps == "relative" ? kLimiterVenkatRelative : kLimiterVenkatAbsolute;
  }
  s.derived = derive_quantities(config);
  s.length_scale = config.conditions.L_ref;
  const ReferenceScales& ref = s.derived.ref;

  const VelocityGrid grid(config.velocity_grid.n, config.velocity_grid.lo, config.velocity_grid.hi);
  {
    const std::vector<std::string> errors = check_case_against_mesh(config, mesh, geometry, grid);
    if (!errors.empty()) {
      std::string msg = "ケースとメッシュが合いません:";
      for (const std::string& e : errors) msg += "\n  " + e;
      throw Error(msg);
    }
  }

  // LU-SGS のブロック数
  long long P_req = params.sweep_blocks;
  if (P_req == 0) P_req = std::clamp<long long>(mesh.n_cells / 2048, 1, 64);
  const int P = static_cast<int>(std::min<long long>(P_req, mesh.n_cells));

  // セルの並べ替えとブロック
  //   "rcb": 座標の再帰二分割でコンパクトなブロックに分け、ブロックの中を逆 Cuthill–McKee で並べる
  //   "rcm": 全体を RCM で並べ、連続区間で分ける(大きな領域ではブロックが RCM の数段の薄い層になる)
  //   "none": 元の番号のまま連続区間で分ける
  if (params.cell_ordering == "rcm" || params.cell_ordering == "rcb") {
    if (params.cell_ordering == "rcb") {
      s.cell_new_to_old = rcb_ordering(mesh, geometry, P, s.block_offsets);
    } else {
      s.cell_new_to_old = rcm_ordering(mesh);
    }
    s.mesh = renumber_cells(mesh, s.cell_new_to_old);
    s.cell_old_to_new = invert_permutation(s.cell_new_to_old);
    s.bface_new_to_old = match_boundary_faces(mesh, s.mesh);
    s.bface_old_to_new = invert_permutation(s.bface_new_to_old);
    s.geometry = compute_geometry(s.mesh);
  } else {
    s.mesh = mesh;
    s.geometry = geometry;
    s.cell_new_to_old.resize(static_cast<std::size_t>(mesh.n_cells));
    std::iota(s.cell_new_to_old.begin(), s.cell_new_to_old.end(), 0);
    s.cell_old_to_new = s.cell_new_to_old;
    s.bface_new_to_old.resize(static_cast<std::size_t>(mesh.n_boundary_faces()));
    std::iota(s.bface_new_to_old.begin(), s.bface_new_to_old.end(), 0);
    s.bface_old_to_new = s.bface_new_to_old;
  }
  s.csr = build_cell_faces(s.mesh);
  s.lsq = build_lsq_gradient(s.mesh, s.geometry, s.csr, LsqOptions{params.lsq_weight_power});

  // 境界パッチ
  const Index n_int = s.mesh.n_internal_faces();
  std::vector<int> mirror_axes;
  s.closed_domain = true;
  for (std::size_t pi = 0; pi < s.mesh.patches.size(); ++pi) {
    const Patch& patch = s.mesh.patches[pi];
    const BoundarySpec* b = config.find_boundary(patch.name);
    GSIS_ASSERT(b != nullptr);
    PatchCondition pc;
    pc.name = patch.name;
    pc.type = b->type;
    pc.first_bface = patch.start - n_int;
    pc.n_faces = patch.count;
    switch (b->type) {
      case BoundaryType::MaxwellWall:
      case BoundaryType::DiffuseWall:
        pc.accommodation = b->accommodation;
        pc.kind = kBcDiffuseWall;
        pc.T = *b->temperature / ref.temperature;
        if (b->velocity) pc.u_wall = *b->velocity / ref.velocity;
        break;
      case BoundaryType::MassFlowInlet:
      case BoundaryType::PressureInlet:
        pc.target_mass_flow = b->mass_flow.value_or(0.0) / (ref.mass_flux * ref.length * ref.length);
        pc.kind = kBcPressure;
        pc.T = *b->temperature / ref.temperature;
        pc.p = b->pressure.value_or(s.derived.p_in) / ref.pressure;
        pc.zero_tangential = b->inlet_zero_tangential;
        s.closed_domain = false;
        break;
      case BoundaryType::PressureOutlet:
        pc.p = *b->pressure / ref.pressure;
        pc.kind = pc.p > 0.0 ? kBcPressure : kBcVacuum;
        s.closed_domain = false;
        break;
      case BoundaryType::Vacuum:
        pc.kind = kBcVacuum;
        s.closed_domain = false;
        break;
      case BoundaryType::Symmetry:
        pc.kind = kBcSymmetry;
        if (patch.count > 0) {
          pc.mirror_axis = symmetry_patch_axis(s.mesh, s.geometry, patch);
          GSIS_CHECK(pc.mirror_axis >= 0, std::format("対称面 \"{}\" の法線が軸に揃っていません", patch.name));
          mirror_axes.push_back(pc.mirror_axis);
        }
        break;
      case BoundaryType::PeriodicSlab:
        pc.kind = kBcPeriodic;
        break;
    }
    if (pc.accommodation < 1.0) {
      pc.kind = kBcMaxwellWall;
      for (int a = 0; a < 3; ++a) GSIS_CHECK(grid.n(a) >= 2, "Maxwell壁の速度格子は各軸2点以上必要です");
      for (Index f = patch.start; f < patch.end(); ++f) {
        const auto& n = s.geometry.face_normal[static_cast<std::size_t>(f)];
        for (int a = 0; a < 3; ++a) {
          if (std::abs(std::abs(n[a]) - 1.0) < 1e-10) {
            GSIS_CHECK(grid.symmetric(a), "Maxwell壁の鏡映軸の速度範囲は対称にしてください");
            mirror_axes.push_back(a);
          }
        }
      }
    }
    // 壁速度は面に接する向きでなければならない
    if ((pc.kind == kBcDiffuseWall || pc.kind == kBcMaxwellWall) && norm(pc.u_wall) > 0.0) {
      for (Index f = patch.start; f < patch.end(); ++f) {
        const double un = dot(pc.u_wall, s.geometry.face_normal[static_cast<std::size_t>(f)]);
        GSIS_CHECK(std::abs(un) <= 1e-9 * norm(pc.u_wall),
                   std::format("壁 \"{}\" の壁速度に法線方向の成分があります(面 {}、u·n = {:.3e})", patch.name,
                               f, un));
      }
    }
    s.patches.push_back(pc);
  }
  s.bface_patch = s.mesh.boundary_face_patches();
  s.grid_n = config.velocity_grid.n;
  for (const auto& pc : s.patches) if (pc.kind == kBcMaxwellWall) s.n_maxwell_faces += pc.n_faces;
  if (s.n_maxwell_faces > 0) {
    s.maxwell.resize(s.n_bfaces());
    int slot = 0;
    for (int b = 0; b < s.n_bfaces(); ++b) {
      const auto& pc = s.patches[s.bface_patch[b]];
      if (pc.kind == kBcMaxwellWall) s.maxwell[b] = {static_cast<long long>(slot++) * grid.size(), 1.0 - pc.accommodation};
    }
  }

  // 保存的な離散平衡分布の判定に使う速度格子の範囲と刻み
  s.discrete_eq = params.equilibrium == "discrete";
  for (int a = 0; a < 3; ++a) {
    s.deq_grid.lo[a] = grid.lo(a);
    s.deq_grid.hi[a] = grid.hi(a);
    s.deq_grid.d[a] = grid.delta(a);
  }
  s.deq_grid.tolerance = params.precision == "fp64" ? 1e-11 : 1e-6;

  // 速度点の保存順(スイープの向きを使うなら向きごとにまとめる)
  s.sweep_dirs = make_sweep_directions(params.sweep_order);
  s.velocity = make_velocity_layout(grid, mirror_axes, s.sweep_dirs);

  // 無次元の幾何量と擬似時間刻み
  const double L = s.length_scale;
  const auto n_cells = static_cast<std::size_t>(s.mesh.n_cells);
  s.volume.resize(n_cells);
  s.vdt.assign(n_cells, 0.0);
  for (std::size_t c = 0; c < n_cells; ++c) {
    s.volume[c] = s.geometry.cell_volume[c] / (L * L * L);
  }
  if (params.pseudo_cfl > 0.0 && params.pseudo_time == "velocity") {
    // V/Δt_{i,k} = Σ_f max(ξ_k·S_f, 0) / CFL → 対角は (1 + 1/CFL) Σa⁺ + V/τ
    s.diag_scale = 1.0 + 1.0 / params.pseudo_cfl;
  } else if (params.pseudo_cfl > 0.0) {
    // Δt_i = CFL V_i / (ξ_max Σ_f |S_f|) → V/Δt = ξ_max Σ|S_f| / CFL
    for (Index c = 0; c < s.mesh.n_cells; ++c) {
      double area_sum = 0.0;
      for (Offset k = s.csr.begin(c); k < s.csr.end(c); ++k) {
        area_sum += s.geometry.face_area[static_cast<std::size_t>(s.csr.faces[static_cast<std::size_t>(k)])];
      }
      s.vdt[static_cast<std::size_t>(c)] = s.velocity.xi_max * area_sum / (L * L) / params.pseudo_cfl;
    }
  }

  // LU-SGS のブロック("rcb" は並べ替えのときに決めた区間)
  if (params.cell_ordering != "rcb") s.block_offsets = partition_contiguous(s.mesh.n_cells, P);

  // スイープの向きごとの順序(sweep_order = "octant"・"dir13")。各ブロックの中を x·d_g の小さい順
  // (同じ値なら内部の番号の順)に並べる
  s.n_sweep_dirs = static_cast<int>(s.sweep_dirs.size());
  if (s.n_sweep_dirs > 0) {
    GSIS_CHECK(s.n_sweep_dirs <= 255, "スイープの向きが多すぎます");
    const std::size_t nc = n_cells;
    s.sweep_order.resize(static_cast<std::size_t>(s.n_sweep_dirs) * nc);
    s.sweep_pos.resize(static_cast<std::size_t>(s.n_sweep_dirs) * nc);
    std::vector<double> key(nc);
    for (int g = 0; g < s.n_sweep_dirs; ++g) {
      const Vec3& d = s.sweep_dirs[static_cast<std::size_t>(g)];
      for (std::size_t c = 0; c < nc; ++c) key[c] = dot(d, s.geometry.cell_center[c]);
      std::int32_t* order = s.sweep_order.data() + static_cast<std::size_t>(g) * nc;
      std::int32_t* pos = s.sweep_pos.data() + static_cast<std::size_t>(g) * nc;
      for (std::size_t b = 0; b + 1 < s.block_offsets.size(); ++b) {
        const Index c0 = s.block_offsets[b];
        const Index c1 = s.block_offsets[b + 1];
        for (Index c = c0; c < c1; ++c) order[c] = static_cast<std::int32_t>(c);
        std::sort(order + c0, order + c1, [&](std::int32_t a, std::int32_t bb) {
          const double ka = key[static_cast<std::size_t>(a)];
          const double kb = key[static_cast<std::size_t>(bb)];
          return ka != kb ? ka < kb : a < bb;
        });
      }
      for (std::size_t k = 0; k < nc; ++k) pos[static_cast<std::size_t>(order[k])] = static_cast<std::int32_t>(k);
    }
    s.sweep_dir_of.resize(static_cast<std::size_t>(s.velocity.nv));
    for (int p = 0; p < s.velocity.nv; ++p) {
      const auto pi = static_cast<std::size_t>(p);
      s.sweep_dir_of[pi] = static_cast<std::uint8_t>(
          sweep_direction_of(s.sweep_dirs, Vec3{s.velocity.xi_x[pi], s.velocity.xi_y[pi], s.velocity.xi_z[pi]}));
    }
  }

  // 気体
  s.mu0 = s.derived.mu_hat0;
  s.omega = config.gas.omega;
  s.Pr = config.gas.Pr;
  s.gamma = config.gas.gamma;
  return s;
}

std::vector<std::int32_t> make_csr_offsets(const KineticSetup& s) {
  std::vector<std::int32_t> out(s.csr.offsets.size());
  GSIS_CHECK(s.csr.offsets.back() < (Offset{1} << 31), "セル → 面の要素が多すぎます");
  for (std::size_t i = 0; i < out.size(); ++i) out[i] = static_cast<std::int32_t>(s.csr.offsets[i]);
  return out;
}

template <class F>
std::vector<CsrEntry<F>> make_csr_entries(const KineticSetup& s) {
  const double L = s.length_scale;
  const Index n_int = s.mesh.n_internal_faces();
  std::vector<CsrEntry<F>> out(s.csr.faces.size());
  for (Index c = 0; c < s.mesh.n_cells; ++c) {
    const Vec3& xc = s.geometry.cell_center[static_cast<std::size_t>(c)];
    for (Offset k = s.csr.begin(c); k < s.csr.end(c); ++k) {
      const auto ki = static_cast<std::size_t>(k);
      const Index f = s.csr.faces[ki];
      const Index other = s.csr.other[ki];
      const Vec3& xf = s.geometry.face_center[static_cast<std::size_t>(f)];
      const Vec3 S = static_cast<double>(s.csr.sign[ki]) * s.geometry.face_area_vector[static_cast<std::size_t>(f)] /
                     (L * L);
      const Vec3 ds = (xf - xc) / L;
      Vec3 dother{};
      if (other != kInvalidIndex) dother = (xf - s.geometry.cell_center[static_cast<std::size_t>(other)]) / L;
      const Vec3 coef = s.lsq.coeff[ki] * L;
      CsrEntry<F>& e = out[ki];
      e.other = other;
      e.bface = other == kInvalidIndex ? f - n_int : -1;
      e.sx = static_cast<F>(S.x);
      e.sy = static_cast<F>(S.y);
      e.sz = static_cast<F>(S.z);
      e.dsx = static_cast<F>(ds.x);
      e.dsy = static_cast<F>(ds.y);
      e.dsz = static_cast<F>(ds.z);
      e.dox = static_cast<F>(dother.x);
      e.doy = static_cast<F>(dother.y);
      e.doz = static_cast<F>(dother.z);
      e.cx = static_cast<F>(coef.x);
      e.cy = static_cast<F>(coef.y);
      e.cz = static_cast<F>(coef.z);
    }
  }
  return out;
}

template <class F>
std::vector<BoundaryFaceInfo<F>> make_bface_infos(const KineticSetup& s) {
  const double L = s.length_scale;
  const Index n_int = s.mesh.n_internal_faces();
  std::vector<BoundaryFaceInfo<F>> out(static_cast<std::size_t>(s.n_bfaces()));
  for (Index b = 0; b < s.n_bfaces(); ++b) {
    const auto f = static_cast<std::size_t>(b + n_int);
    const int pi = s.bface_patch[static_cast<std::size_t>(b)];
    const PatchCondition& pc = s.patches[static_cast<std::size_t>(pi)];
    const Index owner = s.mesh.owner[f];
    const Vec3 dx = (s.geometry.face_center[f] - s.geometry.cell_center[static_cast<std::size_t>(owner)]) / L;
    const Vec3& n = s.geometry.face_normal[f];
    BoundaryFaceInfo<F>& o = out[static_cast<std::size_t>(b)];
    o.owner = owner;
    o.kind = pc.kind;
    o.mirror_xor = pc.kind == kBcSymmetry ? s.velocity.axis_xor[static_cast<std::size_t>(pc.mirror_axis)] : 0;
    if (pc.kind == kBcMaxwellWall) {
      const auto& normal = s.geometry.face_normal[static_cast<std::size_t>(f)];
      for (int a = 0; a < 3; ++a) if (std::abs(std::abs(normal[a]) - 1.0) < 1e-10) o.mirror_xor = s.velocity.axis_xor[a];
    }
    o.patch = pi;
    o.nx = static_cast<F>(n.x);
    o.ny = static_cast<F>(n.y);
    o.nz = static_cast<F>(n.z);
    o.area = static_cast<F>(s.geometry.face_area[f] / (L * L));
    o.dx = static_cast<F>(dx.x);
    o.dy = static_cast<F>(dx.y);
    o.dz = static_cast<F>(dx.z);
  }
  return out;
}

template <class F>
std::vector<F> make_eps2(const KineticSetup& s) {
  const double K = s.params.limiter_k;
  std::vector<F> out(s.volume.size());
  for (std::size_t c = 0; c < out.size(); ++c) out[c] = static_cast<F>(K * K * K * s.volume[c]);
  return out;
}

template std::vector<CsrEntry<float>> make_csr_entries<float>(const KineticSetup&);
template std::vector<CsrEntry<double>> make_csr_entries<double>(const KineticSetup&);
template std::vector<BoundaryFaceInfo<float>> make_bface_infos<float>(const KineticSetup&);
template std::vector<BoundaryFaceInfo<double>> make_bface_infos<double>(const KineticSetup&);
template std::vector<float> make_eps2<float>(const KineticSetup&);
template std::vector<double> make_eps2<double>(const KineticSetup&);

}  // namespace gsis::kinetic
