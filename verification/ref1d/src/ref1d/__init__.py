"""平行平板間の 1 次元参照ソルバー(Shakhov モデル、完全拡散反射)。

3 次元ソルバー(GSIS-PBC / CIS)の検証用に、熱伝導とクエット流の格子収束した解を作る。
使い方と数値解法は verification/ref1d/README.md を参照。

主なモジュール:
- solver: 問題の定義(Problem)と不動点反復(Solver)
- velocity: ξx の速度格子
- mesh: 節点格子
- kernels: numba の計算核
- analytic: 自由分子解、NSF + すべり・温度跳躍の解
- cases: 検証ケースの定義と CSV の入出力
"""

from .solver import Problem, Result, Solver, solve_case

__all__ = ["Problem", "Result", "Solver", "solve_case"]
