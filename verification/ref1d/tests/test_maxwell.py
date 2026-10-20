"""Maxwell壁の自由分子極限、離散の零流束、α=1の旧版一致。"""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
import numpy as np
from ref1d import analytic
from ref1d.mesh import make_mesh
from ref1d.solver import Problem, Solver
from ref1d.velocity import gauss_legendre, midpoint

ROOT = Path(__file__).resolve().parents[3]

class MaxwellTests(unittest.TestCase):
    def test_alpha_one_baseline(self):
        # gitの読取りだけを使い、S3前の計算順とのビット一致を確かめる。
        source = subprocess.check_output(["git", "show", "ceb9de9f6d5648533e8f065d22a4c64769a7c704:verification/ref1d/src/ref1d/solver.py"], cwd=ROOT).decode("utf-8")
        spec = importlib.util.spec_from_loader("ref1d.s3_baseline", loader=None)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        exec(compile(source, "baseline_solver.py", "exec"), module.__dict__)
        for kw in (dict(u_L=-0.1,u_R=0.1),dict(T_L=0.95,T_R=1.05)):
            with self.subTest(kw=kw):
                mesh,vg=make_mesh(64),midpoint(30,10.0)
                a=Solver(Problem(Kn=0.5,**kw),mesh,vg).solve(tol=1e-10)
                b=module.Solver(module.Problem(Kn=0.5,**kw),mesh,vg).solve(tol=1e-10)
                self.assertTrue(a.converged and b.converged)
                np.testing.assert_array_equal(a.mom,b.mom)

    def test_free_molecular(self):
        for alpha in (0.2,0.5,1.0):
            for kw in (dict(u_L=-0.1,u_R=0.1),dict(T_L=0.5,T_R=1.5)):
                with self.subTest(alpha=alpha,kw=kw):
                    pr=Problem(Kn=np.inf,accommodation=alpha,**kw)
                    r=Solver(pr,make_mesh(16),gauss_legendre(xi_max=12)).solve(tol=1e-11)
                    fm=analytic.free_molecular(pr)
                    self.assertTrue(r.converged)
                    np.testing.assert_allclose(r.col("Pxy"),fm.Pxy,atol=3e-13,rtol=1e-11)
                    np.testing.assert_allclose(r.col("qx"),fm.qx,atol=3e-13,rtol=1e-11)

    def test_finite_kn_zero_mass(self):
        for Kn in (0.05,0.5,5.0):
            with self.subTest(Kn=Kn):
                pr=Problem(Kn=Kn,T_L=0.95,T_R=1.05,accommodation=0.5)
                r=Solver(pr,make_mesh(100),midpoint(30,10)).solve(tol=1e-10)
                self.assertTrue(r.converged)
                np.testing.assert_allclose(r.col("Jmass")[[0,-1]],0.0,atol=2e-14)

    def test_invalid_alpha(self):
        for alpha in (0.0,-0.1,1.1,np.nan):
            with self.assertRaises(ValueError): Problem(Kn=1.0,accommodation=alpha)

if __name__ == "__main__":
    unittest.main()
