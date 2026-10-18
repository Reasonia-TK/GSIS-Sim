#include "mesh/geometry.hpp"

#include <algorithm>
#include <cmath>
#include <format>
#include <limits>

#include "core/error.hpp"

namespace gsis {

FaceGeometry compute_face_geometry(std::span<const Vec3> points, std::span<const Index> nodes) {
  const std::size_t n = nodes.size();
  GSIS_ASSERT(n >= 3);
  if (n == 3) {
    const Vec3& p0 = points[static_cast<std::size_t>(nodes[0])];
    const Vec3& p1 = points[static_cast<std::size_t>(nodes[1])];
    const Vec3& p2 = points[static_cast<std::size_t>(nodes[2])];
    return {0.5 * cross(p1 - p0, p2 - p0), (p0 + p1 + p2) / 3.0};
  }

  // 節点の平均を仮の中心とする
  Vec3 xbar{};
  for (const Index k : nodes) xbar += points[static_cast<std::size_t>(k)];
  xbar /= static_cast<double>(n);

  // 扇形の三角形 (p_k, p_{k+1}, x̄) の法線 n_k と重心 c_k
  Vec3 sum_n{};
  for (std::size_t k = 0; k < n; ++k) {
    const Vec3& pk = points[static_cast<std::size_t>(nodes[k])];
    const Vec3& pk1 = points[static_cast<std::size_t>(nodes[(k + 1) % n])];
    sum_n += cross(pk - xbar, pk1 - xbar);
  }
  const Vec3 area_vector = 0.5 * sum_n;
  const double mag = norm(sum_n);
  if (!(mag > 0.0)) {
    return {area_vector, xbar};
  }
  const Vec3 nhat = sum_n / mag;

  double sum_a = 0.0;
  Vec3 sum_ac{};
  for (std::size_t k = 0; k < n; ++k) {
    const Vec3& pk = points[static_cast<std::size_t>(nodes[k])];
    const Vec3& pk1 = points[static_cast<std::size_t>(nodes[(k + 1) % n])];
    const double a = dot(cross(pk - xbar, pk1 - xbar), nhat);
    const Vec3 c = (pk + pk1 + xbar) / 3.0;
    sum_a += a;
    sum_ac += a * c;
  }
  const Vec3 center = std::abs(sum_a) > 0.0 ? sum_ac / sum_a : xbar;
  return {area_vector, center};
}

MeshGeometry compute_geometry(const PolyMesh& mesh) {
  const Index n_faces = mesh.n_faces();
  const Index n_internal = mesh.n_internal_faces();
  const auto n_cells = static_cast<std::size_t>(mesh.n_cells);

  MeshGeometry g;
  g.face_area_vector.resize(static_cast<std::size_t>(n_faces));
  g.face_area.resize(static_cast<std::size_t>(n_faces));
  g.face_normal.resize(static_cast<std::size_t>(n_faces));
  g.face_center.resize(static_cast<std::size_t>(n_faces));

  for (Index f = 0; f < n_faces; ++f) {
    const auto fi = static_cast<std::size_t>(f);
    const FaceGeometry fg = compute_face_geometry(mesh.points, mesh.face(f));
    const double area = norm(fg.area_vector);
    GSIS_CHECK(area > 0.0, std::format("面 {} の面積がゼロです", f));
    g.face_area_vector[fi] = fg.area_vector;
    g.face_area[fi] = area;
    g.face_normal[fi] = fg.area_vector / area;
    g.face_center[fi] = fg.center;
  }

  // 仮の中心 c̄: セルの面中心の単純平均
  std::vector<Vec3> cbar(n_cells, Vec3{});
  std::vector<int> nf(n_cells, 0);
  for (Index f = 0; f < n_faces; ++f) {
    const auto fi = static_cast<std::size_t>(f);
    const auto o = static_cast<std::size_t>(mesh.owner[fi]);
    cbar[o] += g.face_center[fi];
    ++nf[o];
    if (f < n_internal) {
      const auto nb = static_cast<std::size_t>(mesh.neighbour[fi]);
      cbar[nb] += g.face_center[fi];
      ++nf[nb];
    }
  }
  for (std::size_t c = 0; c < n_cells; ++c) {
    GSIS_CHECK(nf[c] > 0, std::format("セル {} に面がありません", c));
    cbar[c] /= static_cast<double>(nf[c]);
  }

  // 面ごとの角錐: v_f = (1/3) S_out · (x_f − c̄)、重心 (3/4) x_f + (1/4) c̄
  g.cell_volume.assign(n_cells, 0.0);
  std::vector<Vec3> moment(n_cells, Vec3{});
  auto add_pyramid = [&](std::size_t c, const Vec3& s_out, const Vec3& xf) {
    const double v = dot(s_out, xf - cbar[c]) / 3.0;
    g.cell_volume[c] += v;
    moment[c] += v * (0.75 * xf + 0.25 * cbar[c]);
  };
  for (Index f = 0; f < n_faces; ++f) {
    const auto fi = static_cast<std::size_t>(f);
    add_pyramid(static_cast<std::size_t>(mesh.owner[fi]), g.face_area_vector[fi], g.face_center[fi]);
    if (f < n_internal) {
      add_pyramid(static_cast<std::size_t>(mesh.neighbour[fi]), -g.face_area_vector[fi],
                  g.face_center[fi]);
    }
  }
  g.cell_center.resize(n_cells);
  for (std::size_t c = 0; c < n_cells; ++c) {
    const double v = g.cell_volume[c];
    // 体積が正でないセルは検査で報告する。重心は仮の中心で代用する。
    g.cell_center[c] = std::abs(v) > 0.0 ? moment[c] / v : cbar[c];
  }
  return g;
}

MeshCheckReport check_geometry(const PolyMesh& mesh, const MeshGeometry& g,
                               double closure_tolerance, int max_listed) {
  const Index n_faces = mesh.n_faces();
  const Index n_internal = mesh.n_internal_faces();
  const auto n_cells = static_cast<std::size_t>(mesh.n_cells);

  MeshCheckReport r;
  r.closure_tolerance = closure_tolerance;

  // 閉性: |Σ S_out| <= tol · Σ |S_f|
  std::vector<Vec3> sum_s(n_cells, Vec3{});
  std::vector<double> sum_a(n_cells, 0.0);
  for (Index f = 0; f < n_faces; ++f) {
    const auto fi = static_cast<std::size_t>(f);
    const auto o = static_cast<std::size_t>(mesh.owner[fi]);
    sum_s[o] += g.face_area_vector[fi];
    sum_a[o] += g.face_area[fi];
    if (f < n_internal) {
      const auto nb = static_cast<std::size_t>(mesh.neighbour[fi]);
      sum_s[nb] -= g.face_area_vector[fi];
      sum_a[nb] += g.face_area[fi];
    }
  }
  int listed_open = 0;
  for (std::size_t c = 0; c < n_cells; ++c) {
    const double res = sum_a[c] > 0.0 ? norm(sum_s[c]) / sum_a[c] : 0.0;
    if (res > r.max_closure_residual) {
      r.max_closure_residual = res;
      r.worst_closure_cell = static_cast<Index>(c);
    }
    if (!(res <= closure_tolerance)) {
      ++r.n_open_cells;
      if (listed_open++ < max_listed) {
        r.errors.push_back(std::format("セル {} が閉じていません(|ΣS_out|/Σ|S_f| = {:.3e}、許容 {:.1e})",
                                       c, res, closure_tolerance));
      }
    }
  }
  if (r.n_open_cells > max_listed) {
    r.errors.push_back(std::format("閉じていないセルは全部で {} 個あります", r.n_open_cells));
  }

  // 体積
  r.min_volume = std::numeric_limits<double>::infinity();
  r.max_volume = -std::numeric_limits<double>::infinity();
  int listed_vol = 0;
  for (std::size_t c = 0; c < n_cells; ++c) {
    const double v = g.cell_volume[c];
    r.min_volume = std::min(r.min_volume, v);
    r.max_volume = std::max(r.max_volume, v);
    r.total_volume += v;
    if (!(v > 0.0)) {
      ++r.n_nonpositive_volume;
      if (listed_vol++ < max_listed) {
        r.errors.push_back(std::format("セル {} の体積が正ではありません(V = {:.6e})", c, v));
      }
    }
  }
  if (r.n_nonpositive_volume > max_listed) {
    r.errors.push_back(
        std::format("体積が正でないセルは全部で {} 個あります", r.n_nonpositive_volume));
  }

  // 内部面の向き: S_f · (x_N − x_O) > 0(違反は警告)
  int listed_orient = 0;
  for (Index f = 0; f < n_internal; ++f) {
    const auto fi = static_cast<std::size_t>(f);
    const Vec3 d = g.cell_center[static_cast<std::size_t>(mesh.neighbour[fi])] -
                   g.cell_center[static_cast<std::size_t>(mesh.owner[fi])];
    if (!(dot(g.face_area_vector[fi], d) > 0.0)) {
      ++r.n_orientation_warnings;
      if (listed_orient++ < max_listed) {
        r.warnings.push_back(std::format("内部面 {}(owner {}、neighbour {})で S_f·(x_N − x_O) <= 0 です",
                                         f, mesh.owner[fi], mesh.neighbour[fi]));
      }
    }
  }
  if (r.n_orientation_warnings > max_listed) {
    r.warnings.push_back(std::format("S_f·(x_N − x_O) <= 0 の内部面は全部で {} 面あります",
                                     r.n_orientation_warnings));
  }

  r.n_unused_points = count_unused_points(mesh);
  if (r.n_unused_points > 0) {
    r.warnings.push_back(
        std::format("どの面からも参照されない節点が {} 個あります", r.n_unused_points));
  }
  if (n_cells == 0) {
    r.min_volume = 0.0;
    r.max_volume = 0.0;
  }
  return r;
}

}  // namespace gsis
