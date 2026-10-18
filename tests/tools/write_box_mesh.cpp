// gsis-write-box-mesh: テスト・スモーク用の箱メッシュを .pmesh に書く補助ツール。
//
//   gsis-write-box-mesh <out.pmesh> [--n NX NY NZ] [--lo X Y Z] [--hi X Y Z]
//                       [--distort D] [--seed S] [--tets] [--tube]
//
//   --tube を付けると、x− を inlet、x+ を outlet、それ以外を wall にする(既定は 6 面を別のパッチ)。

#include <charconv>
#include <cstdio>
#include <format>
#include <string>
#include <vector>

#include "core/error.hpp"
#include "core/paths.hpp"
#include "mesh/pmesh_io.hpp"
#include "support/box_mesh.hpp"

namespace {

double parse_double(const std::string& s) {
  double v = 0.0;
  const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
  GSIS_CHECK(ec == std::errc() && ptr == s.data() + s.size(), std::format("数値ではありません: {}", s));
  return v;
}

int parse_int(const std::string& s) {
  int v = 0;
  const auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), v);
  GSIS_CHECK(ec == std::errc() && ptr == s.data() + s.size(), std::format("整数ではありません: {}", s));
  return v;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    std::vector<std::string> args(argv + 1, argv + argc);
    gsis::test::BoxMeshOptions o;
    bool tube = false;
    std::string out;
    for (std::size_t i = 0; i < args.size(); ++i) {
      const std::string& a = args[i];
      auto need = [&](std::size_t k) {
        GSIS_CHECK(i + k < args.size(), std::format("{} の後に値が {} 個必要です", a, k));
      };
      if (a == "--n") {
        need(3);
        for (int k = 0; k < 3; ++k) o.n[static_cast<std::size_t>(k)] = parse_int(args[++i]);
      } else if (a == "--lo") {
        need(3);
        o.lo = gsis::Vec3{parse_double(args[i + 1]), parse_double(args[i + 2]), parse_double(args[i + 3])};
        i += 3;
      } else if (a == "--hi") {
        need(3);
        o.hi = gsis::Vec3{parse_double(args[i + 1]), parse_double(args[i + 2]), parse_double(args[i + 3])};
        i += 3;
      } else if (a == "--distort") {
        need(1);
        o.distortion = parse_double(args[++i]);
      } else if (a == "--seed") {
        need(1);
        o.seed = static_cast<unsigned>(parse_int(args[++i]));
      } else if (a == "--tets") {
        o.tetrahedra = true;
      } else if (a == "--tube") {
        tube = true;
      } else if (!a.empty() && a[0] == '-') {
        GSIS_FAIL(std::format("知らないオプション {} です", a));
      } else {
        out = a;
      }
    }
    GSIS_CHECK(!out.empty(), "出力ファイル(.pmesh)を指定してください");
    if (tube) {
      const gsis::test::BoxMeshOptions t = gsis::test::tube_like_box(o.n, o.lo, o.hi);
      o.patch_names = t.patch_names;
      o.patch_kinds = t.patch_kinds;
    }
    gsis::PolyMesh mesh = gsis::test::make_box_mesh(o);
    mesh.metadata = {{"generator", "gsis-write-box-mesh"},
                     {"source", std::format("n={}x{}x{} distort={} seed={} tets={} tube={}", o.n[0], o.n[1],
                                            o.n[2], o.distortion, o.seed, o.tetrahedra, tube)}};
    const auto path = gsis::path_from_utf8(out);
    gsis::ensure_parent_directory(path);
    gsis::write_pmesh(path, mesh);
    std::printf("%s: %d cells, %d faces, %d points\n", out.c_str(), mesh.n_cells, mesh.n_faces(),
                mesh.n_points());
    return 0;
  } catch (const std::exception& e) {
    std::fprintf(stderr, "error: %s\n", e.what());
    return 1;
  }
}
