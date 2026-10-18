#include "mesh/section_cut.hpp"

#include <charconv>
#include <cmath>
#include <format>

#include "core/error.hpp"

namespace gsis {

SectionSpec parse_section_spec(std::string_view positions, std::string_view axis) {
  SectionSpec spec;
  if (axis == "x" || axis == "X") {
    spec.axis = 0;
  } else if (axis == "y" || axis == "Y") {
    spec.axis = 1;
  } else if (axis == "z" || axis == "Z") {
    spec.axis = 2;
  } else {
    GSIS_FAIL(std::format("断面の軸は \"x\"・\"y\"・\"z\" のどれかです(値: \"{}\")", axis));
  }
  auto is_sep = [](char c) { return c == ',' || c == ';' || c == ' ' || c == '\t'; };
  std::size_t i = 0;
  while (i < positions.size()) {
    while (i < positions.size() && is_sep(positions[i])) ++i;
    if (i >= positions.size()) break;
    std::size_t j = i;
    while (j < positions.size() && !is_sep(positions[j])) ++j;
    const std::string_view tok = positions.substr(i, j - i);
    double v = 0.0;
    const auto [ptr, ec] = std::from_chars(tok.data(), tok.data() + tok.size(), v);
    GSIS_CHECK(ec == std::errc{} && ptr == tok.data() + tok.size() && std::isfinite(v),
               std::format("断面の位置 \"{}\" は数値ではありません(並び: \"{}\")", tok, positions));
    spec.positions.push_back(v);
    i = j;
  }
  return spec;
}

const char* section_axis_name(int axis) {
  switch (axis) {
    case 0: return "x";
    case 1: return "y";
    default: return "z";
  }
}

std::vector<SectionCut> build_section_cuts(const PolyMesh& mesh, const MeshGeometry& geometry, double length_scale,
                                           const SectionSpec& spec) {
  GSIS_CHECK(spec.axis >= 0 && spec.axis <= 2, std::format("断面の軸 {} が範囲外です", spec.axis));
  GSIS_CHECK(length_scale > 0.0, "断面の長さの基準 L_ref は正です");
  const Index nif = mesh.n_internal_faces();
  const double L = length_scale;
  const int a = spec.axis;
  std::vector<SectionCut> cuts;
  cuts.reserve(spec.positions.size());
  for (const double X : spec.positions) {
    SectionCut cut;
    cut.position = X;
    cut.axis = a;
    double sx = 0.0;
    for (Index f = 0; f < nif; ++f) {
      const auto fi = static_cast<std::size_t>(f);
      const double xo = geometry.cell_center[static_cast<std::size_t>(mesh.owner[fi])][a] / L;
      const double xn = geometry.cell_center[static_cast<std::size_t>(mesh.neighbour[fi])][a] / L;
      const bool o_before = xo < X;
      const bool n_before = xn < X;
      if (o_before == n_before) continue;
      const signed char s = o_before ? 1 : -1;
      const double A = geometry.face_area[fi] / (L * L);
      cut.faces.push_back(f);
      cut.sign.push_back(s);
      cut.area += A;
      cut.projected_area += static_cast<double>(s) * geometry.face_area_vector[fi][a] / (L * L);
      sx += A * geometry.face_center[fi][a] / L;
    }
    cut.face_position = cut.area > 0.0 ? sx / cut.area : X;
    cuts.push_back(std::move(cut));
  }
  return cuts;
}

double section_flow(const SectionCut& cut, std::span<const double> face_flux) {
  double s = 0.0;
  for (std::size_t k = 0; k < cut.faces.size(); ++k) {
    const auto f = static_cast<std::size_t>(cut.faces[k]);
    GSIS_CHECK(f < face_flux.size(), "断面の面の番号が流束の配列の範囲外です");
    s += static_cast<double>(cut.sign[k]) * face_flux[f];
  }
  return s;
}

}  // namespace gsis
