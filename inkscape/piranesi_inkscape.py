#!/usr/bin/env python3
"""
Inkscape extension: Piranesi perspective transform for vector paths.

Maps selected paths from their combined bounding box to a user-defined
quadrilateral using Piranesi's non-linear perspective (exponential scaling
based on vanishing points).

Preferred workflow: draw a 4-node closed polygon to define the output shape,
select it together with the paths to transform, and run the extension.  The
topmost object in z-order is used as the reference quad if it has exactly
4 straight-line nodes; otherwise the corner coordinates from the dialog are
used as a fallback.

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

        # Flatten each element's own transform into its path data so all
        # coordinates are in a consistent (parent) coordinate space.
        for el in selected:
            el.apply_transform()

        # Sort selected elements by document order so we can reliably identify
        # the topmost one (last in document order = visually on top).
        doc_order = list(self.svg.iter())
        selected.sort(key=lambda el: doc_order.index(el))

        # Check whether the topmost selected path is a 4-node closed polygon
        # that should serve as the output-shape reference quad.
        ref_quad = selected[-1]
        quad_corners = _extract_quad_corners(ref_quad.path)

        if quad_corners is not None and len(selected) > 1:
            # Reference-quad mode: corners come from the drawn shape.
            a, b, c, d = _assign_corners(quad_corners)
            paths_to_transform = selected[:-1]
        else:
            # Fallback: use coordinates from the dialog.
            if quad_corners is not None and len(selected) == 1:
                inkex.errormsg(
                    "The selected quad defines the output shape but there are "
                    "no other paths to transform. Also select the paths you "
                    "want to warp."
                )
                return
            a = [self.options.x1, self.options.y1]
            b = [self.options.x2, self.options.y2]
            c = [self.options.x3, self.options.y3]
            d = [self.options.x4, self.options.y4]
            paths_to_transform = selected

        # Combined bounding box of the paths being transformed.
        combined_bbox = None
        for el in paths_to_transform:
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

        # Build the forward transform from the four corners.
        # a = bottom-left (large SVG y), b = bottom-right,
        # c = top-right (small SVG y), d = top-left.
        line_ab = _points_2line(a, b)
        line_bc = _points_2line(b, c)
        line_cd = _points_2line(c, d)
        line_da = _points_2line(d, a)

        vp_da = _line_intersection(line_ab, line_cd)
        vp_cd = _line_intersection(line_bc, line_da)

        k_ab = _distance_2d(vp_da, b) / _distance_2d(vp_da, a)
        k_bc = _distance_2d(vp_cd, b) / _distance_2d(vp_cd, c)
        k_cd = _distance_2d(vp_da, c) / _distance_2d(vp_da, d)
        k_da = _distance_2d(vp_cd, a) / _distance_2d(vp_cd, d)

        def forward(x_n, y_n):
            x_ab = _exp_scale(k_ab, x_n)
            x_cd = _exp_scale(k_cd, x_n)
            y_bc = _exp_scale(k_bc, y_n)
            y_da = _exp_scale(k_da, y_n)

            p_ab = _add(_cp(a), _scale(_sub(b, a), x_ab))
            p_cd = _add(_cp(d), _scale(_sub(c, d), x_cd))
            p_bc = _add(_cp(c), _scale(_sub(b, c), y_bc))
            p_da = _add(_cp(d), _scale(_sub(a, d), y_da))

            pt = _line_intersection(_points_2line(p_ab, p_cd), _points_2line(p_bc, p_da))
            return pt[0], pt[1]

        def transform_point(x, y):
            x_n = (x - src_x0) / src_w
            y_n = (y - src_y0) / src_h
            return forward(x_n, y_n)

        for el in paths_to_transform:
            el.path = _transform_path(el.path, transform_point)


# ---------------------------------------------------------------------------
# Reference-quad helpers
# ---------------------------------------------------------------------------

def _extract_quad_corners(path):
    """Return 4 (x, y) tuples if path is a closed 4-node straight-line polygon, else None."""
    abs_path = path.to_absolute()
    points = []
    closed = False

    for cmd in abs_path:
        name = type(cmd).__name__
        if name == "Move":
            if points:
                return None  # multiple subpaths not supported
            points.append((cmd.args[0], cmd.args[1]))
        elif name == "Line":
            points.append((cmd.args[0], cmd.args[1]))
        elif name == "ZoneClose":
            closed = True
        else:
            return None  # curves or arcs: not a simple polygon

    if not closed:
        return None

    # Some tools emit an explicit closing L back to the first point before Z.
    if (len(points) == 5
            and abs(points[-1][0] - points[0][0]) < 1e-6
            and abs(points[-1][1] - points[0][1]) < 1e-6):
        points = points[:4]

    return points if len(points) == 4 else None


def _assign_corners(points):
    """Sort 4 points into (a=bottom-left, b=bottom-right, c=top-right, d=top-left).

    In SVG coordinates y increases downward, so bottom = larger y.
    """
    by_y = sorted(points, key=lambda p: p[1], reverse=True)
    bottom = sorted(by_y[:2], key=lambda p: p[0])
    top = sorted(by_y[2:], key=lambda p: p[0])
    a = list(bottom[0])   # bottom-left
    b = list(bottom[1])   # bottom-right
    c = list(top[1])      # top-right
    d = list(top[0])      # top-left
    return a, b, c, d


# ---------------------------------------------------------------------------
# Path transformation
# ---------------------------------------------------------------------------

def _transform_path(path, transform_func):
    """Apply transform_func(x, y) -> (x', y') to every coordinate in a path."""
    abs_path = path.to_absolute()
    result = inkex.Path()
    cur_x, cur_y = 0.0, 0.0

    for cmd in abs_path:
        name = type(cmd).__name__

        if name == "ZoneClose":
            result.append(ZoneClose())

        elif name in ("Move", "Line", "TepidQuadratic"):
            x, y = transform_func(cmd.args[0], cmd.args[1])
            result.append(type(cmd)(x, y))
            cur_x, cur_y = cmd.args[0], cmd.args[1]

        elif name == "Horz":
            x, y = transform_func(cmd.args[0], cur_y)
            result.append(Line(x, y))
            cur_x = cmd.args[0]

        elif name == "Vert":
            x, y = transform_func(cur_x, cmd.args[0])
            result.append(Line(x, y))
            cur_y = cmd.args[0]

        elif name == "Quadratic":
            x1, y1 = transform_func(cmd.args[0], cmd.args[1])
            x, y = transform_func(cmd.args[2], cmd.args[3])
            result.append(Quadratic(x1, y1, x, y))
            cur_x, cur_y = cmd.args[2], cmd.args[3]

        elif name == "Smooth":
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
            x, y = transform_func(cmd.args[5], cmd.args[6])
            result.append(
                Arc(cmd.args[0], cmd.args[1], cmd.args[2],
                    cmd.args[3], cmd.args[4], x, y)
            )
            cur_x, cur_y = cmd.args[5], cmd.args[6]

        else:
            result.append(cmd)

    return result


# ---------------------------------------------------------------------------
# Geometry helpers (mirrors of piranesi.py)
# ---------------------------------------------------------------------------

def _exp_scale(k, t):
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


def _cp(p):
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
