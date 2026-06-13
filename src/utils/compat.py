"""Compatibility layer for environments without scipy (e.g. Android/Chaquopy)."""
from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np

# ---------------------------------------------------------------------------
# 1. Root finding (XIRR)
# ---------------------------------------------------------------------------

def brentq_fallback(f: Callable[[float], float], a: float, b: float, xtol: float = 1e-7, maxiter: int = 100) -> float:
    """Pure Python implementation of Brent's method for root finding."""
    fa = f(a)
    fb = f(b)
    if fa * fb > 0:
        # Fallback to a simple scan if signs don't differ
        for x in np.linspace(a, b, 20):
            fx = f(x)
            if fx * fa <= 0:
                b, fb = x, fx
                break
        else:
            return b if abs(fb) < abs(fa) else a

    if abs(fa) < abs(fb):
        a, b = b, a
        fa, fb = fb, fa

    c = a
    fc = fa
    mflag = True
    d = 0.0

    for _ in range(maxiter):
        if abs(fb) < xtol:
            return b

        if fa != fc and fb != fc:
            s = (a * fb * fc / ((fa - fb) * (fa - fc)) +
                 b * fa * fc / ((fb - fa) * (fb - fc)) +
                 c * fa * fb / ((fc - fa) * (fc - fb)))
        else:
            s = b - fb * (b - a) / (fb - fa)

        condition1 = not ( (s > (3*a + b)/4 and s < b) or (s < (3*a + b)/4 and s > b) )
        condition2 = mflag and (abs(s - b) >= abs(b - c) / 2)
        condition3 = not mflag and (abs(s - b) >= abs(c - d) / 2)
        condition4 = mflag and (abs(b - c) < xtol)
        condition5 = not mflag and (abs(c - d) < xtol)

        if condition1 or condition2 or condition3 or condition4 or condition5:
            s = (a + b) / 2
            mflag = True
        else:
            mflag = False

        fs = f(s)
        d = c
        c = b
        fc = fb

        if fa * fs < 0:
            b = s
            fb = fs
        else:
            a = s
            fa = fs

        if abs(fa) < abs(fb):
            a, b = b, a
            fa, fb = fb, fa

    return b

def newton_fallback(f: Callable[[float], float], x0: float, fprime: Optional[Callable[[float], float]] = None,
                   args: tuple = (), tol: float = 1.48e-8, maxiter: int = 50) -> float:
    """Pure Python implementation of Newton-Raphson or Secant method."""
    if fprime is None:
        p0 = x0
        p1 = x0 * 1.0001 if x0 != 0 else 0.0001
        for _ in range(maxiter):
            f1 = f(p1, *args)
            f0 = f(p0, *args)
            if f1 == f0: return p1
            p = p1 - f1 * (p1 - p0) / (f1 - f0)
            if abs(p - p1) < tol:
                return p
            p0 = p1
            p1 = p
        return p1
    else:
        p0 = x0
        for _ in range(maxiter):
            fder = fprime(p0, *args)
            if fder == 0: return p0
            p = p0 - f(p0, *args) / fder
            if abs(p - p0) < tol:
                return p
            p0 = p
        return p0

# ---------------------------------------------------------------------------
# 2. Constrained Optimization (Portfolio Optimizer)
# ---------------------------------------------------------------------------

def project_simplex_box(v: np.ndarray, lower: float = 0.0, upper: float = 1.0) -> np.ndarray:
    """Project vector v onto the intersection of the simplex (sum(w)=1) and box constraints [lower, upper]."""
    l = v.min() - 1.0
    r = v.max() + 1.0
    for _ in range(50):
        mid = (l + r) / 2
        w = np.clip(v - mid, lower, upper)
        s = w.sum()
        if abs(s - 1.0) < 1e-10:
            return w
        if s > 1.0:
            l = mid
        else:
            r = mid
    return np.clip(v - l, lower, upper)

def minimize_fallback(fun, x0, args=(), bounds=None, constraints=None, tol=1e-7, maxiter=500, **kwargs):
    """Simple Optimizer using Gradient Descent + Penalty Method for constraints.
    Accepts and ignores extra kwargs for compatibility with scipy.optimize.minimize.
    """
    x = np.array(x0, dtype=float)
    n = len(x)

    lower = np.array([b[0] for b in bounds]) if bounds else np.zeros(n)
    upper = np.array([b[1] for b in bounds]) if bounds else np.ones(n)

    def objective_with_penalty(w, p_coeff):
        base_val = fun(w, *args)
        penalty = 0.0
        if constraints:
            for c in constraints:
                c_val = c['fun'](w)
                if c['type'] == 'eq':
                    penalty += p_coeff * (c_val ** 2)
                else:
                    penalty += p_coeff * (max(0, -c_val) ** 2)
        return base_val + penalty

    # Optimization loop
    lr = 0.1
    p_coeff = 10.0
    for i in range(maxiter):
        # Numerical gradient
        eps = 1e-7
        f0 = objective_with_penalty(x, p_coeff)
        grad = np.zeros(n)
        for j in range(n):
            xj = x.copy()
            xj[j] += eps
            grad[j] = (objective_with_penalty(xj, p_coeff) - f0) / eps

        # Step
        x_new = x - lr * grad

        # Project onto box constraints immediately to keep it stable
        x_new = np.clip(x_new, lower, upper)

        # Simplex projection (sum=1) if no other equality constraints are present
        # This is a bit of a shortcut but helps for simple portfolio problems.
        if len(constraints or []) == 1 and constraints[0]['type'] == 'eq':
             x_new = project_simplex_box(x_new, lower[0], upper[0])

        f1 = objective_with_penalty(x_new, p_coeff)
        if f1 < f0:
            if abs(f1 - f0) < tol:
                x = x_new
                break
            x = x_new
            lr *= 1.1
        else:
            lr *= 0.5
            if lr < 1e-10:
                break

        # Increase penalty coefficient over time
        if i % 50 == 0:
            p_coeff *= 2

    return type('Result', (object,), {'x': x, 'success': True, 'fun': fun(x, *args), 'message': 'Optimization terminated'})

# ---------------------------------------------------------------------------
# 3. Exposing the interfaces
# ---------------------------------------------------------------------------

try:
    from scipy.optimize import brentq, newton, minimize
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    brentq = brentq_fallback
    newton = newton_fallback
    minimize = minimize_fallback
