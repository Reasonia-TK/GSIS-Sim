// mesh-info: メッシュ(.pmesh)を読み、検査結果と統計を表示する。

#include <algorithm>
#include <format>
#include <numeric>
#include <string>
#include <vector>

#include "app/cli.hpp"
#include "core/log.hpp"
#include "core/paths.hpp"
#include "core/timer.hpp"
#include "io/vtu_writer.hpp"
#include "mesh/fv_geometry.hpp"
#include "mesh/mesh_quality.hpp"
#include "mesh/ordering.hpp"
#include "mesh/pmesh_io.hpp"

namespace gsis::cli {

int cmd_mesh_info(std::span<const std::string> args) {
  std::string mesh_file;
  std::string vtu_file;
  std::string boundary_vtu_file;
  bool ascii = false;
  for (std::size_t i = 0; i < args.size(); ++i) {
    const std::string& a = args[i];
    if (a == "--vtu" || a == "--boundary-vtu") {
      if (i + 1 >= args.size()) throw UsageError{a + " の後にファイル名が必要です"};
      (a == "--vtu" ? vtu_file : boundary_vtu_file) = args[++i];
    } else if (a == "--ascii") {
      ascii = true;
    } else if (!a.empty() && a[0] == '-') {
      throw UsageError{"知らないオプション " + a + " です"};
    } else if (mesh_file.empty()) {
      mesh_file = a;
    } else {
      throw UsageError{"メッシュファイルは 1 つだけ指定してください"};
    }
  }
  if (mesh_file.empty()) throw UsageError{"メッシュファイル(.pmesh)を指定してください"};

  Timer timer;
  const LoadedMesh lm = load_pmesh(path_from_utf8(mesh_file));
  const double t_load = timer.seconds();
  const PolyMesh& mesh = lm.mesh;
  const MeshGeometry& g = lm.geometry;
  const MeshCheckReport& r = lm.report;

  const FvGeometry fv = compute_fv_geometry(mesh, g);
  const ShapeStats shape = compute_shape_stats(mesh);
  const NonOrthogonalityStats nonorth = compute_non_orthogonality(mesh, g, fv);
  const BoundingBox bb = compute_bounding_box(mesh);
  const std::vector<PatchSummary> patches = summarize_patches(mesh, g);
  const CellFaceCsr csr = build_cell_faces(mesh);
  const LsqGradient lsq = build_lsq_gradient(mesh, g, csr);
  const std::vector<Index> rcm = rcm_ordering(mesh);
  const Index bw_before = matrix_bandwidth(mesh);
  const Index bw_after = matrix_bandwidth(renumber_cells(mesh, rcm));

  print_heading("メッシュ");
  print_item("ファイル", mesh_file);
  print_item("形式", std::format("pmesh v1、長さの単位 {}", mesh.length_unit));
  for (const auto& [k, v] : mesh.metadata) print_item("metadata." + k, v);
  print_item("節点", format_count(mesh.n_points()));
  print_item("面", std::format("{}(内部 {}、境界 {})", format_count(mesh.n_faces()),
                               format_count(mesh.n_internal_faces()),
                               format_count(mesh.n_boundary_faces())));
  print_item("面の形", std::format("三角形 {}、四角形 {}、5 角形以上 {}(最大 {} 節点)",
                                   format_count(shape.n_triangles), format_count(shape.n_quads),
                                   format_count(shape.n_polygons), shape.max_face_nodes));
  print_item("セル", format_count(mesh.n_cells));
  print_item("セルの面数", std::format("4 面 {}、5 面 {}、6 面 {}、7 面 {}、8 面以上 {}(最大 {} 面)",
                                       format_count(shape.cells_by_faces[0]),
                                       format_count(shape.cells_by_faces[1]),
                                       format_count(shape.cells_by_faces[2]),
                                       format_count(shape.cells_by_faces[3]),
                                       format_count(shape.cells_by_faces[4]), shape.max_cell_faces));
  print_item("座標の範囲 [m]", std::format("x [{:.6g}, {:.6g}]、y [{:.6g}, {:.6g}]、z [{:.6g}, {:.6g}]",
                                         bb.lo.x, bb.hi.x, bb.lo.y, bb.hi.y, bb.lo.z, bb.hi.z));

  print_heading("検査(docs/pmesh_format.md §3・§4)");
  print_item("位相・番号・パッチ", "OK");
  print_item("閉性", std::format("max |ΣS_out|/Σ|S_f| = {:.3e}(許容 {:.0e})OK", r.max_closure_residual,
                                r.closure_tolerance));
  print_item("体積", std::format("最小 {:.6e}、最大 {:.6e} m^3(比 {:.3g})OK", r.min_volume,
                                r.max_volume, r.max_volume / r.min_volume));
  print_item("総体積", std::format("{:.15e} m^3", r.total_volume));
  print_item("内部面の向き", r.n_orientation_warnings == 0
                                 ? std::string("S_f·(x_N − x_O) > 0(全面)OK")
                                 : std::format("S_f·(x_N − x_O) <= 0 の面が {} 面(警告)",
                                               format_count(r.n_orientation_warnings)));
  print_item("d_n <= 0 の内部面", format_count(fv.n_nonpositive_dn));
  print_item("参照されない節点", format_count(r.n_unused_points));
  const auto min_cond = std::min_element(lsq.conditioning.begin(), lsq.conditioning.end());
  print_item("最小二乗勾配の行列", std::format("det(G)/(tr(G)/3)^3 の最小 {:.3e}(1 が最良)", *min_cond));

  print_heading("パッチ");
  {
    const std::string head =
        std::format("  {:>4}  {:<20}{:<10}{:>10}  {:>14}  {}\n", "番号", "名前", "種類", "面数",
                    "面積 [m^2]", "ΣS_f / 面積(平面なら外向き法線)");
    std::fwrite(head.data(), 1, head.size(), stdout);
    for (std::size_t p = 0; p < patches.size(); ++p) {
      const PatchSummary& s = patches[p];
      const Vec3 n = s.area > 0.0 ? s.area_vector / s.area : Vec3{};
      const std::string line =
          std::format("  {:>4}  {:<20}{:<10}{:>10}  {:>14.6e}  ({:+.4f}, {:+.4f}, {:+.4f})\n", p,
                      s.name, s.kind, format_count(s.n_faces), s.area, n.x, n.y, n.z);
      std::fwrite(line.data(), 1, line.size(), stdout);
    }
  }

  print_heading("非直交角(内部面の n̂ と d_ij のなす角)");
  if (mesh.n_internal_faces() > 0) {
    print_item("最大", std::format("{:.3f}°(面 {})", nonorth.max_deg, nonorth.worst_face));
    print_item("平均", std::format("{:.3f}°(面積加重 {:.3f}°)", nonorth.mean_deg,
                                  nonorth.area_weighted_mean_deg));
    std::string above;
    for (std::size_t t = 0; t < nonorth.kThresholdsDeg.size(); ++t) {
      if (!above.empty()) above += "、";
      above += std::format("> {:.0f}°: {}", nonorth.kThresholdsDeg[t], format_count(nonorth.n_above[t]));
    }
    print_item("閾値を超える面", above);
  } else {
    print_item("内部面", "なし");
  }

  print_heading("セルの順序(LU-SGS 用)");
  print_item("行列の帯幅", std::format("現在 {} → RCM 後 {}", format_count(bw_before), format_count(bw_after)));

  if (!vtu_file.empty()) {
    VtuWriter w = VtuWriter::polyhedra(mesh);
    std::vector<std::int32_t> cell_id(static_cast<std::size_t>(mesh.n_cells));
    std::iota(cell_id.begin(), cell_id.end(), 0);
    std::vector<std::int32_t> rcm_rank(static_cast<std::size_t>(mesh.n_cells));
    for (std::size_t i = 0; i < rcm.size(); ++i) rcm_rank[static_cast<std::size_t>(rcm[i])] = static_cast<std::int32_t>(i);
    std::vector<std::int32_t> n_faces(shape.cell_face_count.begin(), shape.cell_face_count.end());
    w.add_cell_scalar("cell_id", std::span<const std::int32_t>(cell_id));
    w.add_cell_scalar("volume", std::span<const double>(g.cell_volume));
    w.add_cell_scalar("n_faces", std::span<const std::int32_t>(n_faces));
    w.add_cell_scalar("non_orthogonality_max_deg", std::span<const double>(nonorth.cell_max_deg));
    w.add_cell_scalar("lsq_conditioning", std::span<const double>(lsq.conditioning));
    w.add_cell_scalar("rcm_rank", std::span<const std::int32_t>(rcm_rank));
    w.add_cell_vector("cell_center", std::span<const Vec3>(g.cell_center));
    w.write(path_from_utf8(vtu_file), ascii ? VtuEncoding::Ascii : VtuEncoding::AppendedRaw);
    print_heading("出力");
    print_item("VTU 体積", vtu_file);
  }
  if (!boundary_vtu_file.empty()) {
    VtuWriter w = VtuWriter::boundary_polygons(mesh);
    std::vector<double> area;
    for (const Patch& p : mesh.patches) {
      for (Index f = p.start; f < p.end(); ++f) area.push_back(g.face_area[static_cast<std::size_t>(f)]);
    }
    w.add_cell_scalar("area", std::span<const double>(area));
    w.write(path_from_utf8(boundary_vtu_file), ascii ? VtuEncoding::Ascii : VtuEncoding::AppendedRaw);
    if (vtu_file.empty()) print_heading("出力");
    print_item("VTU 境界面", boundary_vtu_file);
  }
  log_info("読み込みと検査 {:.3f} s、全体 {:.3f} s", t_load, timer.seconds());
  return 0;
}

}  // namespace gsis::cli
