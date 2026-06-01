#!/usr/bin/env python3
"""
Inkscape extension: Piranesi perspective transform for vector paths.

Maps selected paths from their combined bounding box into a reference
quadrilateral.  A blend slider interpolates between a standard rectilinear
homography (0) and Piranesi's non-linear perspective (100).

Workflow: draw a 4-node closed polygon to define the output shape, select it
together with the paths to transform, and run the extension.  The topmost
object in z-order is used as the reference quad.

https://github.com/brunopostle/piranesi
(C) 2026 Bruno Postle <bruno@postle.net>
GPL-3.0-or-later
"""

import math
import inkex
from inkex.paths import (
    Line,
    Curve,
    Smooth,
    Quadratic,
    Arc,
    ZoneClose,
)


class PiranesiTransform(inkex.EffectExtension):

    def add_arguments(self, pars):
        pars.add_argument("--blend", type=int, default=100)

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

        # The topmost selected path must be a 4-node closed polygon.
        quad_corners = _extract_quad_corners(selected[-1].path)

        if quad_corners is None:
            inkex.errormsg(
                "No 4-node reference quad found.\n\n"
                "Draw a 4-node closed polygon, select it together with your "
                "paths (raise it to top with Object > Raise to Top), then "
                "run the extension again."
            )
            return

        if len(selected) == 1:
            inkex.errormsg(
                "The selected quad defines the output shape but there are "
                "no other paths to transform. Also select the paths you "
                "want to warp."
            )
            return

        a, b, c, d = _assign_corners(quad_corners)
        paths_to_transform = selected[:-1]

        # Combined bounding box of the paths being transformed.
        combined_bbox = None
        for el in paths_to_transform:
            bbox = el.path.bounding_box()
            if bbox is not None:
                combined_bbox = bbox if combined_bbox is None else combined_bbox + bbox

        if (
            combined_bbox is None
            or combined_bbox.width == 0
            or combined_bbox.height == 0
        ):
            inkex.errormsg("Cannot determine bounding box of selection.")
            return

        src_x0 = combined_bbox.left
        src_y0 = combined_bbox.top
        src_w = combined_bbox.width
        src_h = combined_bbox.height

        blend = self.options.blend / 100.0

        # Build the forward transform from the four corners.
        # a = bottom-left (large SVG y), b = bottom-right,
        # c = top-right (small SVG y), d = top-left.
        line_ab = _points_2line(a, b)
        line_bc = _points_2line(b, c)
        line_cd = _points_2line(c, d)
        line_da = _points_2line(d, a)

        eps = 1e-10
        if _lines_parallel(line_ab, line_cd):
            k_ab = k_cd = 1.0
        else:
            vp_h = _line_intersection(line_ab, line_cd)
            k_ab = _distance_2d(vp_h, b) / max(_distance_2d(vp_h, a), eps)
            k_cd = _distance_2d(vp_h, c) / max(_distance_2d(vp_h, d), eps)

        if _lines_parallel(line_bc, line_da):
            k_bc = k_da = 1.0
        else:
            vp_v = _line_intersection(line_bc, line_da)
            k_bc = _distance_2d(vp_v, b) / max(_distance_2d(vp_v, c), eps)
            k_da = _distance_2d(vp_v, a) / max(_distance_2d(vp_v, d), eps)

        bilinear = k_ab == 1.0 and k_bc == 1.0 and k_cd == 1.0 and k_da == 1.0
        homo = _compute_homography(a, b, c, d)

        def forward(x_n, y_n):
            p_ab = _add(_cp(a), _scale(_sub(b, a), _exp_scale(k_ab, x_n)))
            p_cd = _add(_cp(d), _scale(_sub(c, d), _exp_scale(k_cd, x_n)))
            if bilinear:
                pir = _add(p_cd, _scale(_sub(p_ab, p_cd), y_n))
            else:
                p_bc = _add(_cp(c), _scale(_sub(b, c), _exp_scale(k_bc, y_n)))
                p_da = _add(_cp(d), _scale(_sub(a, d), _exp_scale(k_da, y_n)))
                pir = _line_intersection(
                    _points_2line(p_ab, p_cd), _points_2line(p_bc, p_da)
                )
            if blend >= 1.0 - 1e-9:
                return pir[0], pir[1]
            hx, hy = _homo_fwd(x_n, y_n, homo)
            if blend <= 1e-9:
                return hx, hy
            return (1 - blend) * hx + blend * pir[0], (1 - blend) * hy + blend * pir[1]

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
    if (
        len(points) == 5
        and abs(points[-1][0] - points[0][0]) < 1e-6
        and abs(points[-1][1] - points[0][1]) < 1e-6
    ):
        points = points[:4]

    return points if len(points) == 4 else None


def _assign_corners(points):
    """Sort 4 points into (a=bottom-left, b=bottom-right, c=top-right, d=top-left).

    In SVG coordinates y increases downward, so bottom = larger y.
    """
    by_y = sorted(points, key=lambda p: p[1], reverse=True)
    bottom = sorted(by_y[:2], key=lambda p: p[0])
    top = sorted(by_y[2:], key=lambda p: p[0])
    a = list(bottom[0])  # bottom-left
    b = list(bottom[1])  # bottom-right
    c = list(top[1])  # top-right
    d = list(top[0])  # top-left
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
                Arc(
                    cmd.args[0],
                    cmd.args[1],
                    cmd.args[2],
                    cmd.args[3],
                    cmd.args[4],
                    x,
                    y,
                )
            )
            cur_x, cur_y = cmd.args[5], cmd.args[6]

        else:
            result.append(cmd)

    return result


# ---------------------------------------------------------------------------
# Geometry helpers (mirrors of piranesi.py / piranesi_gimp.py)
# ---------------------------------------------------------------------------


def _lines_parallel(l0, l1):
    a0, a1 = l0["a"], l1["a"]
    if abs(a0) > 1e8 and abs(a1) > 1e8:
        return True
    return abs(a0 - a1) < 1e-4 * (1.0 + abs(a0) + abs(a1))


def _compute_homography(a, b, c, d):
    """8-coefficient homography mapping [0,1]^2 to the quad.
    Corner correspondence: (0,0)->d  (1,0)->c  (0,1)->a  (1,1)->b
    """
    dx, dy = d[0], d[1]
    cx, cy = c[0], c[1]
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    A = cx - bx
    B = ax - bx
    C = bx - cx - ax + dx
    D = cy - by
    E = ay - by
    F = by - cy - ay + dy
    det = A * E - B * D
    if abs(det) < 1e-10:
        g = h = 0.0
    else:
        g = (C * E - B * F) / det
        h = (A * F - C * D) / det
    Hc = dx
    Hf = dy
    Ha = cx + g * cx - dx
    Hb = ax + h * ax - dx
    Hd = cy + g * cy - dy
    He = ay + h * ay - dy
    return Ha, Hb, Hc, Hd, He, Hf, g, h


def _homo_fwd(u, v, coeffs):
    Ha, Hb, Hc, Hd, He, Hf, g, h = coeffs
    w = g * u + h * v + 1.0
    return (Ha * u + Hb * v + Hc) / w, (Hd * u + He * v + Hf) / w


def _exp_scale(k, t):
    if abs(k - 1.0) < 1e-10:
        return t
    return ((k**t) - 1) / (k - 1)


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
