#!/usr/bin/env python3
"""
Inkscape extension: Piranesi perspective transform for vector paths.

Maps selected paths from their combined bounding box to a user-defined
quadrilateral using Piranesi's non-linear perspective (exponential scaling
based on vanishing points).

https://github.com/brunopostle/piranesi
(C) 2025 Bruno Postle <bruno@postle.net>
GPL-3.0-or-later
"""

import math
import inkex
from inkex.paths import (
    Move, Line, Horz, Vert, Curve, Smooth,
    Quadratic, TepidQuadratic, Arc, ZoneClose,
)


class PiranesiTransform(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument("--tab", default="transform")
        pars.add_argument("--x1", type=float, default=100.0)
        pars.add_argument("--y1", type=float, default=500.0)
        pars.add_argument("--x2", type=float, default=400.0)
        pars.add_argument("--y2", type=float, default=500.0)
        pars.add_argument("--x3", type=float, default=350.0)
        pars.add_argument("--y3", type=float, default=100.0)
        pars.add_argument("--x4", type=float, default=150.0)
        pars.add_argument("--y4", type=float, default=100.0)

    def effect(self):
        selected = list(self.svg.selection.filter(inkex.PathElement))
        if not selected:
            inkex.errormsg("Please select at least one path element.")
            return

        # Flatten each element's transform into its path data so we work in
        # a consistent coordinate space.
        for el in selected:
            el.apply_transform()

        # Combined bounding box of all selected paths (in parent coordinate space).
        combined_bbox = None
        for el in selected:
            bbox = el.path.bounding_box()
            if bbox is not None:
                combined_bbox = bbox if combined_bbox is None else combined_bbox + bbox

        if combined_bbox is None or combined_bbox.width == 0 or combined_bbox.height == 0:
            inkex.errormsg("Cannot determine bounding box of selection.")
            return

        src_x0 = combined_bbox.left
        src_y0 = combined_bbox.top
        src_w = combined_bbox.width
        src_h = combined_bbox.height

        # Output quadrilateral corners (in SVG user units, y increases downward).
        # a = bottom-left (high y), b = bottom-right, c = top-right (low y), d = top-left.
        a = [self.options.x1, self.options.y1]
        b = [self.options.x2, self.options.y2]
        c = [self.options.x3, self.options.y3]
        d = [self.options.x4, self.options.y4]

        # Vanishing points and k-ratios (same maths as piranesi.py).
        line_ab = _points_2line(a, b)
        line_bc = _points_2line(b, c)
        line_cd = _points_2line(c, d)
        line_da = _points_2line(d, a)

        vp_da = _line_intersection(line_ab, line_cd)  # horizontal vanishing point
        vp_cd = _line_intersection(line_bc, line_da)  # vertical vanishing point

        k_ab = _distance_2d(vp_da, b) / _distance_2d(vp_da, a)
        k_bc = _distance_2d(vp_cd, b) / _distance_2d(vp_cd, c)
        k_cd = _distance_2d(vp_da, c) / _distance_2d(vp_da, d)
        k_da = _distance_2d(vp_cd, a) / _distance_2d(vp_cd, d)

        def forward(x_n, y_n):
            """Map normalised (0–1, 0–1) coords to the output quadrilateral."""
            x_ab = _exp_scale(k_ab, x_n)
            x_cd = _exp_scale(k_cd, x_n)
            y_bc = _exp_scale(k_bc, y_n)
            y_da = _exp_scale(k_da, y_n)

            p_ab = _add(_a(a), _scale(_sub(b, a), x_ab))
            p_cd = _add(_a(d), _scale(_sub(c, d), x_cd))
            p_bc = _add(_a(c), _scale(_sub(b, c), y_bc))
            p_da = _add(_a(d), _scale(_sub(a, d), y_da))

            pt = _line_intersection(_points_2line(p_ab, p_cd), _points_2line(p_bc, p_da))
            return pt[0], pt[1]

        def transform_point(x, y):
            x_n = (x - src_x0) / src_w
            y_n = (y - src_y0) / src_h
            return forward(x_n, y_n)

        for el in selected:
            el.path = _transform_path(el.path, transform_point)


# ---------------------------------------------------------------------------
# Path transformation
# ---------------------------------------------------------------------------

def _transform_path(path, transform_func):
    """Apply transform_func(x, y) -> (x', y') to every coordinate in a path."""
    abs_path = path.to_absolute()
    result = inkex.Path()
    cur_x, cur_y = 0.0, 0.0  # tracks current pen position for H/V expansion

    for cmd in abs_path:
        name = type(cmd).__name__

        if name == "ZoneClose":
            result.append(ZoneClose())

        elif name in ("Move", "Line", "TepidQuadratic"):
            x, y = transform_func(cmd.args[0], cmd.args[1])
            result.append(type(cmd)(x, y))
            cur_x, cur_y = cmd.args[0], cmd.args[1]

        elif name == "Horz":
            # Expand H x → L x cur_y before transforming.
            x, y = transform_func(cmd.args[0], cur_y)
            result.append(Line(x, y))
            cur_x = cmd.args[0]

        elif name == "Vert":
            # Expand V y → L cur_x y before transforming.
            x, y = transform_func(cur_x, cmd.args[0])
            result.append(Line(x, y))
            cur_y = cmd.args[0]

        elif name == "Quadratic":
            x1, y1 = transform_func(cmd.args[0], cmd.args[1])
            x, y = transform_func(cmd.args[2], cmd.args[3])
            result.append(Quadratic(x1, y1, x, y))
            cur_x, cur_y = cmd.args[2], cmd.args[3]

        elif name == "Smooth":
            # S has an explicit second control point; first is implied (mirror).
            # We transform the explicit coords; smoothness is approximate after a
            # non-linear warp.
            x2, y2 = transform_func(cmd.args[0], cmd.args[1])
            x, y = transform_func(cmd.args[2], cmd.args[3])
            result.append(Smooth(x2, y2, x, y))
            cur_x, cur_y = cmd.args[2], cmd.args[3]

        elif name == "Curve":
            x1, y1 = transform_func(cmd.args[0], cmd.args[1])
            x2, y2 = transform_func(cmd.args[2], cmd.args[3])
            x, y = transform_func(cmd.args[4], cmd.args[5])
            result.append(Curve(x1, y1, x2, y2, x, y))
            cur_x, cur_y = cmd.args[4], cmd.args[5]

        elif name == "Arc":
            # Arc radii and rotation are not transformed; only the endpoint moves.
            # For large arcs under a non-linear warp this is an approximation.
            x, y = transform_func(cmd.args[5], cmd.args[6])
            result.append(
                Arc(cmd.args[0], cmd.args[1], cmd.args[2], cmd.args[3], cmd.args[4], x, y)
            )
            cur_x, cur_y = cmd.args[5], cmd.args[6]

        else:
            result.append(cmd)

    return result


# ---------------------------------------------------------------------------
# Geometry helpers (mirrors of piranesi.py)
# ---------------------------------------------------------------------------

def _exp_scale(k, t):
    """Exponential scaling along an edge with ratio k at normalised position t."""
    if abs(k - 1.0) < 1e-10:
        return t
    return ((k ** t) - 1) / (k - 1)


def _points_2line(p0, p1):
    dx = p1[0] - p0[0]
    if abs(dx) < 1e-10:
        dx = 1e-10
    a = (p1[1] - p0[1]) / dx
    return {"a": a, "b": p0[1] - p0[0] * a}


def _line_intersection(l0, l1):
    if abs(l0["a"] - l1["a"]) < 1e-10:
        return [0.5, 0.5]
    x = (l1["b"] - l0["b"]) / (l0["a"] - l1["a"])
    return [x, l0["a"] * x + l0["b"]]


def _a(p):
    return list(p)


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1]]


def _add(a, b):
    return [a[0] + b[0], a[1] + b[1]]


def _scale(v, f):
    return [v[0] * f, v[1] * f]


def _distance_2d(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


if __name__ == "__main__":
    PiranesiTransform().run()
