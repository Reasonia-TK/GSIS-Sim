// mesh: 有限体積の量(d_ij、d_n、補間重み)、セル → 面の CSR、最小二乗勾配

#include <algorithm>
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include <vector>

#include "mesh/fv_geometry.hpp"
#include "mesh/geometry.hpp"
#include "mesh/geometry_f32.hpp"
#include "support/box_mesh.hpp"

using namespace gsis;
using Catch::Approx;

TEST_CASE("cell-face CSR lists every face once per adjacent cell", "[mesh][fv]") {
  test::BoxMeshOptions o;
  o.n = {3, 2, 2};
  o.tetrahedra = true;
  const PolyMesh mesh = test::make_box_mesh(o);
  const CellFaceCsr csr = build_cell_faces(mesh);
  REQUIRE(csr.n_cells() == mesh.n_cells);
  CHECK(csr.faces.size() == static_cast<std::size_t>(2 * mesh.n_internal_faces() + mesh.n_boundary_faces()));
  std::vector<int> seen_owner(static_cast<std::size_t>(mesh.n_faces()), 0);
  std::vector<int> seen_neighbour(static_cast<std::size_t>(mesh.n_faces()), 0);
  for (Index c = 0; c < mesh.n_cells; ++c) {
    CHECK(csr.n_faces_of(c) == 4);  // 四面体
    for (Offset k = csr.begin(c); k < csr.end(c); ++k) {
      const auto ki = static_cast<std::size_t>(k);
      const Index f = csr.faces[ki];
      const auto fi = static_cast<std::size_t>(f);
      if (k > csr.begin(c)) CHECK(csr.faces[ki - 1] < f);  // 面番号の昇順
      if (csr.sign[ki] > 0) {
        CHECK(mesh.owner[fi] == c);
        CHECK(csr.other[ki] == (mesh.is_internal(f) ? mesh.neighbour[fi] : kInvalidIndex));
        ++seen_owner[fi];
      } else {
        CHECK(mesh.is_internal(f));
        CHECK(mesh.neighbour[fi] == c);
        CHECK(csr.other[ki] == mesh.owner[fi]);
        ++seen_neighbour[fi];
      }
    }
  }
  for (Index f = 0; f < mesh.n_faces(); ++f) {
    CHECK(seen_owner[static_cast<std::size_t>(f)] == 1);
    CHECK(seen_neighbour[static_cast<std::size_t>(f)] == (mesh.is_internal(f) ? 1 : 0));
  }
}

TEST_CASE("interpolation weights (eq. 23) and d_n", "[mesh][fv]") {
  SECTION("graded structured box: w_N = h_O / (h_O + h_N) on x faces") {
    test::BoxMeshOptions o;
    o.n = {6, 2, 2};
    o.lo = {0.0, 0.0, 0.0};
    o.hi = {3.0, 1.0, 1.0};
    o.grading = {1.5, 1.0, 1.0};
    const PolyMesh mesh = test::make_box_mesh(o);
    const MeshGeometry g = compute_geometry(mesh);
    const FvGeometry fv = compute_fv_geometry(mesh, g);
    CHECK(fv.n_nonpositive_dn == 0);
    int n_x_faces = 0;
    for (Index f = 0; f < mesh.n_internal_faces(); ++f) {
      const auto fi = static_cast<std::size_t>(f);
      CHECK(fv.w_owner[fi] + fv.w_neighbour[fi] == Approx(1.0).epsilon(1e-15));
      CHECK(fv.dn[fi] == Approx(norm(fv.d[fi])).epsilon(1e-13));  // 直交格子
      const Vec3& n = g.face_normal[fi];
      if (std::abs(n.x) > 0.5) {
        ++n_x_faces;
        const double xo = g.cell_center[static_cast<std::size_t>(mesh.owner[fi])].x;
        const double xn = g.cell_center[static_cast<std::size_t>(mesh.neighbour[fi])].x;
        const double xf = g.face_center[fi].x;
        const double h_o = 2.0 * (xf - xo);
        const double h_n = 2.0 * (xn - xf);
        CHECK(fv.w_neighbour[fi] == Approx(h_o / (h_o + h_n)).epsilon(1e-13));
        CHECK(h_n / h_o == Approx(1.5).epsilon(1e-12));  // 等比 1.5
      } else {
        CHECK(fv.w_neighbour[fi] == Approx(0.5).epsilon(1e-13));  // y・z 方向は一様
      }
    }
    CHECK(n_x_faces == 5 * 2 * 2);
    // 境界面: d_n = 2 |x_f − x_O|(直方体のセルでは法線方向の幅)
    for (Index f = mesh.n_internal_faces(); f < mesh.n_faces(); ++f) {
      const auto fi = static_cast<std::size_t>(f);
      CHECK(fv.dn[fi] == Approx(2.0 * norm(g.face_center[fi] - g.cell_center[static_cast<std::size_t>(mesh.owner[fi])])));
      CHECK(fv.w_owner[fi] == 1.0);
      CHECK(fv.w_neighbour[fi] == 0.0);
    }
  }
  SECTION("distorted tetrahedra: weights stay in [0, 1] and follow the definition") {
    test::BoxMeshOptions o;
    o.n = {3, 3, 3};
    o.tetrahedra = true;
    o.distortion = 0.15;
    const PolyMesh mesh = test::make_box_mesh(o);
    const MeshGeometry g = compute_geometry(mesh);
    const FvGeometry fv = compute_fv_geometry(mesh, g);
    int n_unclamped = 0;
    for (Index f = 0; f < mesh.n_internal_faces(); ++f) {
      const auto fi = static_cast<std::size_t>(f);
      const Vec3& n = g.face_normal[fi];
      const Vec3 xo = g.cell_center[static_cast<std::size_t>(mesh.owner[fi])];
      const double dn = dot(n, fv.d[fi]);
      CHECK(fv.dn[fi] == Approx(dn).epsilon(1e-14));
      REQUIRE(dn > 0.0);
      const double wn = fv.w_neighbour[fi];
      CHECK(wn >= 0.0);
      CHECK(wn <= 1.0);
      CHECK(fv.w_owner[fi] + wn == Approx(1.0).epsilon(1e-15));
      // 式から独立な性質: 0 < w < 1 なら、補間点 x_O + w_N d_ij は面の平面上にある
      if (wn > 0.0 && wn < 1.0) {
        ++n_unclamped;
        const Vec3 xi = xo + wn * fv.d[fi];
        CHECK(std::abs(dot(n, xi - g.face_center[fi])) < 1e-13 * norm(fv.d[fi]));
      }
    }
    CHECK(n_unclamped == mesh.n_internal_faces());  // この程度の歪みでは打ち切られない
  }
}

TEST_CASE("least-squares gradient is exact for linear fields", "[mesh][fv][lsq]") {
  const Vec3 grad_exact{1.3, -0.7, 2.1};
  const double phi0 = 0.4;
  auto phi = [&](const Vec3& x) { return phi0 + dot(grad_exact, x); };

  struct Variant {
    const char* name;
    bool tets;
    double distortion;
    std::array<double, 3> grading;
  };
  const Variant variants[] = {
      {"uniform hex", false, 0.0, {1.0, 1.0, 1.0}},
      {"graded hex", false, 0.0, {1.4, 0.7, 1.2}},
      {"distorted hex", false, 0.25, {1.0, 1.0, 1.0}},
      {"tetrahedra", true, 0.0, {1.0, 1.0, 1.0}},
      {"distorted tetrahedra", true, 0.15, {1.1, 1.0, 0.9}},
  };
  for (const Variant& v : variants) {
    for (const double power : {0.0, 1.0, 2.0}) {
      DYNAMIC_SECTION(v.name << ", weight power " << power) {
        test::BoxMeshOptions o;
        o.n = {4, 3, 3};
        o.lo = {-1.0, 0.5, 2.0};
        o.hi = {1.0, 1.5, 3.2};
        o.tetrahedra = v.tets;
        o.distortion = v.distortion;
        o.grading = v.grading;
        o.seed = 11;
        const PolyMesh mesh = test::make_box_mesh(o);
        const MeshGeometry g = compute_geometry(mesh);
        const CellFaceCsr csr = build_cell_faces(mesh);
        const LsqGradient lsq = build_lsq_gradient(mesh, g, csr, LsqOptions{power});
        std::vector<Real> phi_cell(static_cast<std::size_t>(mesh.n_cells));
        for (std::size_t c = 0; c < phi_cell.size(); ++c) phi_cell[c] = phi(g.cell_center[c]);
        std::vector<Real> phi_bnd(static_cast<std::size_t>(mesh.n_boundary_faces()));
        for (std::size_t b = 0; b < phi_bnd.size(); ++b) {
          phi_bnd[b] = phi(g.face_center[b + static_cast<std::size_t>(mesh.n_internal_faces())]);
        }
        std::vector<Vec3> grad(static_cast<std::size_t>(mesh.n_cells));
        compute_lsq_gradient(mesh, csr, lsq, phi_cell, phi_bnd, grad);
        double max_err = 0.0;
        for (const Vec3& gr : grad) max_err = std::max(max_err, norm(gr - grad_exact));
        CHECK(max_err < 1e-11 * norm(grad_exact));
        const double min_cond = *std::min_element(lsq.conditioning.begin(), lsq.conditioning.end());
        CHECK(min_cond > 0.0);
        CHECK(min_cond <= 1.0 + 1e-12);

        // G^{-1} の形(grad = G^{-1} Σ_k ω_k r_k Δφ_k)でも同じ勾配になる
        double max_err_ginv = 0.0;
        for (Index c = 0; c < mesh.n_cells; ++c) {
          const auto ci = static_cast<std::size_t>(c);
          Vec3 b{};
          for (Offset k = csr.begin(c); k < csr.end(c); ++k) {
            const auto ki = static_cast<std::size_t>(k);
            const Vec3 xk = csr.other[ki] != kInvalidIndex
                                ? g.cell_center[static_cast<std::size_t>(csr.other[ki])]
                                : g.face_center[static_cast<std::size_t>(csr.faces[ki])];
            const Vec3 r = xk - g.cell_center[ci];
            const double w = power == 0.0 ? 1.0 : std::pow(norm(r), -power);
            b += w * (phi(xk) - phi_cell[ci]) * r;
          }
          const auto& m = lsq.g_inverse[ci];  // xx, yy, zz, xy, yz, xz
          const Vec3 gr{m[0] * b.x + m[3] * b.y + m[5] * b.z, m[3] * b.x + m[1] * b.y + m[4] * b.z,
                        m[5] * b.x + m[4] * b.y + m[2] * b.z};
          max_err_ginv = std::max(max_err_ginv, norm(gr - grad_exact));
        }
        CHECK(max_err_ginv < 1e-11 * norm(grad_exact));
      }
    }
  }
}

TEST_CASE("FP32 geometry copy is scaled by the reference length", "[mesh][fv]") {
  test::BoxMeshOptions o;
  o.n = {2, 2, 2};
  o.lo = {0.0, 0.0, 0.0};
  o.hi = {2e-3, 2e-3, 2e-3};
  const PolyMesh mesh = test::make_box_mesh(o);
  const MeshGeometry g = compute_geometry(mesh);
  const CellFaceCsr csr = build_cell_faces(mesh);
  const LsqGradient lsq = build_lsq_gradient(mesh, g, csr);
  const MeshGeometryF32 f32 = make_geometry_f32(mesh, g, csr, lsq, 1e-3);
  REQUIRE(f32.cells.volume.size() == 8);
  for (const float v : f32.cells.volume) CHECK(v == Approx(1.0).epsilon(1e-6));  // (1 mm)^3 / L^3
  CHECK(f32.faces.owner == mesh.owner);
  CHECK(f32.cell_faces.size() == csr.faces.size());
  CHECK(f32.cell_face_sign.size() == csr.faces.size());
  CHECK(f32.faces.boundary_patch.size() == static_cast<std::size_t>(mesh.n_boundary_faces()));
  CHECK(f32.bytes() > 0);
  // 勾配の係数は L 倍(無次元の座標での係数)
  CHECK(f32.lsq_cx[0] == Approx(lsq.coeff[0].x * 1e-3).epsilon(1e-6));
}

TEST_CASE("FP32 geometry copy reproduces a linear gradient in nondimensional coordinates", "[mesh][fv]") {
  // 歪んだ四面体の箱(SI、長さ 1 mm 程度)。無次元の座標 x' = x / L で φ = a + g'·x' の勾配を、
  // FP32 の写しだけ(係数ベクトルの形と G^{-1} の形)で計算する
  const double L = 1e-3;
  test::BoxMeshOptions o;
  o.n = {3, 3, 2};
  o.lo = {0.0, -1e-3, -1e-3};
  o.hi = {3e-3, 1e-3, 1e-3};
  o.tetrahedra = true;
  o.distortion = 0.15;
  const PolyMesh mesh = test::make_box_mesh(o);
  const MeshGeometry g = compute_geometry(mesh);
  const CellFaceCsr csr = build_cell_faces(mesh);
  const Vec3 grad_nd{0.7, -1.3, 0.4};
  for (const double power : {0.0, 2.0}) {
    const LsqGradient lsq = build_lsq_gradient(mesh, g, csr, LsqOptions{power});
    const MeshGeometryF32 f = make_geometry_f32(mesh, g, csr, lsq, L);
    auto pos = [&](std::int32_t other, std::int32_t face) {
      return other >= 0 ? Vec3{f.cells.cx[static_cast<std::size_t>(other)], f.cells.cy[static_cast<std::size_t>(other)],
                               f.cells.cz[static_cast<std::size_t>(other)]}
                        : Vec3{f.faces.cx[static_cast<std::size_t>(face)], f.faces.cy[static_cast<std::size_t>(face)],
                               f.faces.cz[static_cast<std::size_t>(face)]};
    };
    double err_coeff = 0.0, err_ginv = 0.0;
    for (Index c = 0; c < mesh.n_cells; ++c) {
      const auto ci = static_cast<std::size_t>(c);
      const Vec3 xc{f.cells.cx[ci], f.cells.cy[ci], f.cells.cz[ci]};
      Vec3 g1{}, b{};
      for (std::int64_t k = f.cell_face_offsets[ci]; k < f.cell_face_offsets[ci + 1]; ++k) {
        const auto ki = static_cast<std::size_t>(k);
        const Vec3 xk = pos(f.cell_face_other[ki], f.cell_faces[ki]);
        const Vec3 r = xk - xc;
        const double dphi = dot(grad_nd, r);
        g1 += dphi * Vec3{f.lsq_cx[ki], f.lsq_cy[ki], f.lsq_cz[ki]};
        const double w = power == 0.0 ? 1.0 : std::pow(norm(r), -power);
        b += w * dphi * r;
      }
      const double m[6] = {f.lsq_g_inverse[0][ci], f.lsq_g_inverse[1][ci], f.lsq_g_inverse[2][ci],
                           f.lsq_g_inverse[3][ci], f.lsq_g_inverse[4][ci], f.lsq_g_inverse[5][ci]};
      const Vec3 g2{m[0] * b.x + m[3] * b.y + m[5] * b.z, m[3] * b.x + m[1] * b.y + m[4] * b.z,
                    m[5] * b.x + m[4] * b.y + m[2] * b.z};
      err_coeff = std::max(err_coeff, norm(g1 - grad_nd));
      err_ginv = std::max(err_ginv, norm(g2 - grad_nd));
    }
    INFO("weight power " << power << ": coeff " << err_coeff << ", G^-1 " << err_ginv);
    CHECK(err_coeff < 1e-4 * norm(grad_nd));  // FP32 の丸めの程度
    CHECK(err_ginv < 1e-4 * norm(grad_nd));
  }
}
