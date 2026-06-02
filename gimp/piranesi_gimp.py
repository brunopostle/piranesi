#!/usr/bin/env python3
"""
Piranesi GIMP 3.x plugin - interactive perspective transform.

Found in GIMP under:  Filters > Distorts > Piranesi...

A floating dialog shows the layer with four draggable corner handles.  Dragging
a corner updates a coarse live preview in real time; releasing updates it at
full preview quality.  Use the slider to adjust the amount of conventional
affine (rectilnear) perspective vs. Piranesi perspective.  Click Apply to warp
the layer in-place (recorded as a single undo step).

Install:
  mkdir -p ~/.config/GIMP/3.2/plug-ins/piranesi_gimp
  cp gimp/piranesi_gimp.py ~/.config/GIMP/3.2/plug-ins/piranesi_gimp/piranesi_gimp.py
  chmod +x ~/.config/GIMP/3.2/plug-ins/piranesi_gimp/piranesi_gimp.py


https://medium.com/@brunopostle/piranesis-perspective-trick-6bcd7a754da9

(C) 2026 Bruno Postle <bruno@postle.net>

Piranesi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import math
import sys

import gi

gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gegl", "0.4")

from gi.repository import Gimp, GimpUi, GLib, Gtk, Gdk, GdkPixbuf, Gegl

import numpy as np


# ---------------------------------------------------------------------------
# Vector / geometry helpers
# ---------------------------------------------------------------------------

def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1]]

def _add(a, b):
    return [a[0] + b[0], a[1] + b[1]]

def _scale(v, f):
    return [v[0] * f, v[1] * f]

def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

def _line(p0, p1):
    dx = p1[0] - p0[0] or 1e-11
    a = (p1[1] - p0[1]) / dx
    return {"a": a, "b": p0[1] - p0[0] * a}

def _intersect(l0, l1):
    if l0["a"] == l1["a"]:
        return [0.5, 0.5]
    x = (l1["b"] - l0["b"]) / (l0["a"] - l1["a"])
    return [x, l0["a"] * x + l0["b"]]


# ---------------------------------------------------------------------------
# Rectilinear (homography) transform
# ---------------------------------------------------------------------------

def _compute_homography(a, b, c, d):
    dx, dy = d[0], d[1]
    cx, cy = c[0], c[1]
    ax, ay = a[0], a[1]
    bx, by = b[0], b[1]
    A = cx - bx; B = ax - bx; C = bx - cx - ax + dx
    D = cy - by; E = ay - by; F = by - cy - ay + dy
    det = A * E - B * D
    if abs(det) < 1e-10:
        g = h = 0.0
    else:
        g = (C * E - B * F) / det
        h = (A * F - C * D) / det
    Hc = dx; Hf = dy
    Ha = cx + g * cx - dx; Hb = ax + h * ax - dx
    Hd = cy + g * cy - dy; He = ay + h * ay - dy
    return Ha, Hb, Hc, Hd, He, Hf, g, h

def _homo_fwd(u, v, coeffs):
    Ha, Hb, Hc, Hd, He, Hf, g, h = coeffs
    w = g * u + h * v + 1.0
    return (Ha * u + Hb * v + Hc) / w, (Hd * u + He * v + Hf) / w


# ---------------------------------------------------------------------------
# Piranesi transform core
# ---------------------------------------------------------------------------

def _k_interp(k, t):
    if abs(k - 1.0) < 1e-9:
        return t
    return (k**t - 1.0) / (k - 1.0)

def _lines_parallel(l0, l1):
    a0, a1 = l0["a"], l1["a"]
    if abs(a0) > 1e8 and abs(a1) > 1e8:
        return True
    return abs(a0 - a1) < 1e-4 * (1.0 + abs(a0) + abs(a1))

def build_transforms(a, b, c, d, out_w, out_h, blend=1.0):
    eps = 1e-10
    line_ab = _line(a, b); line_cd = _line(c, d)
    line_bc = _line(b, c); line_da = _line(d, a)

    if _lines_parallel(line_ab, line_cd):
        k_ab = k_cd = 1.0
    else:
        vp_da = _intersect(line_ab, line_cd)
        k_ab = _dist(vp_da, b) / max(_dist(vp_da, a), eps)
        k_cd = _dist(vp_da, c) / max(_dist(vp_da, d), eps)

    if _lines_parallel(line_bc, line_da):
        k_bc = k_da = 1.0
    else:
        vp_cd = _intersect(line_bc, line_da)
        k_bc = _dist(vp_cd, b) / max(_dist(vp_cd, c), eps)
        k_da = _dist(vp_cd, a) / max(_dist(vp_cd, d), eps)

    _bilinear = k_ab == 1.0 and k_bc == 1.0 and k_cd == 1.0 and k_da == 1.0

    def _piranesi_fwd(x, y):
        px_ab = _add(a, _scale(_sub(b, a), _k_interp(k_ab, x)))
        px_cd = _add(d, _scale(_sub(c, d), _k_interp(k_cd, x)))
        if _bilinear:
            pt = _add(px_cd, _scale(_sub(px_ab, px_cd), y))
        else:
            py_bc = _add(c, _scale(_sub(b, c), _k_interp(k_bc, y)))
            py_da = _add(d, _scale(_sub(a, d), _k_interp(k_da, y)))
            pt = _intersect(_line(px_ab, px_cd), _line(py_bc, py_da))
        return pt[0], pt[1]

    homo = _compute_homography(a, b, c, d)
    t = max(0.0, min(1.0, blend))

    if t >= 1.0 - 1e-9:
        def forward(x, y): return _piranesi_fwd(x, y)
    elif t <= 1e-9:
        def forward(x, y): return _homo_fwd(x, y, homo)
    else:
        def forward(x, y):
            px, py = _piranesi_fwd(x, y)
            hx, hy = _homo_fwd(x, y, homo)
            return (1 - t) * hx + t * px, (1 - t) * hy + t * py

    def reverse(px, py):
        xn, yn = px / out_w, py / out_h
        x, y = 0.5, 0.5
        step = 0.001
        lx, ly = x, y
        for _ in range(11):
            fx0, fy0 = forward(x, y)
            fx1, _ = forward(x + step, y)
            _, fy2 = forward(x, y + step)
            ddx = (fx1 - fx0) / out_w
            ddy = (fy2 - fy0) / out_h
            if abs(ddx) < 1e-10 or abs(ddy) < 1e-10:
                break
            x = max(0.0, min(1.0, x + (xn - fx0 / out_w) / ddx * step / 2))
            y = max(0.0, min(1.0, y + (yn - fy0 / out_h) / ddy * step / 2))
            if abs(x - lx) < 1e-7 and abs(y - ly) < 1e-7:
                break
            lx, ly = x, y
        return x, y

    return forward, reverse


# ---------------------------------------------------------------------------
# Numpy image utilities
# ---------------------------------------------------------------------------

def _drawable_to_array(drawable):
    """Read a GIMP drawable into a (h, w, 4) uint8 numpy array via buf.get()."""
    buf = drawable.get_buffer()
    rect = buf.get_extent()
    w, h = rect.width, rect.height
    data = buf.get(rect, 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.CLAMP)
    return np.frombuffer(bytes(data), dtype=np.uint8).reshape(h, w, 4).copy()


def _downscale(arr, new_w, new_h):
    """Bilinear downscale of (h, w, 4) uint8 array."""
    h, w = arr.shape[:2]
    src = arr.astype(np.float32)
    xi = np.linspace(0, w - 1, new_w, dtype=np.float32)
    yi = np.linspace(0, h - 1, new_h, dtype=np.float32)
    xi0 = np.minimum(xi.astype(np.int32), w - 2)
    xi1 = xi0 + 1
    yi0 = np.minimum(yi.astype(np.int32), h - 2)
    yi1 = yi0 + 1
    tx = (xi - xi0)[np.newaxis, :, np.newaxis]
    ty = (yi - yi0)[:, np.newaxis, np.newaxis]
    return (src[yi0[:, None], xi0[None, :]] * (1 - tx) * (1 - ty)
            + src[yi0[:, None], xi1[None, :]] * tx * (1 - ty)
            + src[yi1[:, None], xi0[None, :]] * (1 - tx) * ty
            + src[yi1[:, None], xi1[None, :]] * tx * ty).astype(np.uint8)


def _array_to_pixbuf(arr):
    """Convert (h, w, 4) uint8 numpy array to GdkPixbuf."""
    h, w = arr.shape[:2]
    data = arr.tobytes()
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4
    )


# ---------------------------------------------------------------------------
# Numpy warp
# ---------------------------------------------------------------------------

def numpy_transform(src_arr, out_w, out_h, points, grid_size=32, blend=1.0):
    """Warp src_arr (h,w,4 uint8) into quad. Returns (out_h,out_w,4 uint8)."""
    src_h, src_w = src_arr.shape[:2]
    a, b, c, d = [list(map(float, p)) for p in points]
    _, reverse = build_transforms(a, b, c, d, out_w, out_h, blend=blend)

    # Build coarse reverse grid
    gn = grid_size + 1
    gx_g = np.empty((gn, gn), dtype=np.float32)
    gy_g = np.empty((gn, gn), dtype=np.float32)
    for gy in range(gn):
        for gx in range(gn):
            ox = int(gx * (out_w - 1) / grid_size)
            oy = int(gy * (out_h - 1) / grid_size)
            sx, sy = reverse(ox, oy)
            gx_g[gy, gx] = sx * src_w
            gy_g[gy, gx] = sy * src_h

    # Expand grid to per-pixel coordinate map
    xi = np.linspace(0, grid_size, out_w, dtype=np.float32)
    yi = np.linspace(0, grid_size, out_h, dtype=np.float32)
    xi0 = np.minimum(xi.astype(np.int32), grid_size - 1)
    xi1 = xi0 + 1
    yi0 = np.minimum(yi.astype(np.int32), grid_size - 1)
    yi1 = yi0 + 1
    tx = (xi - xi0)[np.newaxis, :]
    ty = (yi - yi0)[:, np.newaxis]

    cx = (gx_g[yi0[:, None], xi0[None, :]] * (1 - tx) * (1 - ty)
          + gx_g[yi0[:, None], xi1[None, :]] * tx * (1 - ty)
          + gx_g[yi1[:, None], xi0[None, :]] * (1 - tx) * ty
          + gx_g[yi1[:, None], xi1[None, :]] * tx * ty)
    cy = (gy_g[yi0[:, None], xi0[None, :]] * (1 - tx) * (1 - ty)
          + gy_g[yi0[:, None], xi1[None, :]] * tx * (1 - ty)
          + gy_g[yi1[:, None], xi0[None, :]] * (1 - tx) * ty
          + gy_g[yi1[:, None], xi1[None, :]] * tx * ty)

    # Bilinear sample from source
    cx = np.clip(cx, 0, src_w - 1)
    cy = np.clip(cy, 0, src_h - 1)
    x0 = cx.astype(np.int32)
    y0 = cy.astype(np.int32)
    x1 = np.minimum(x0 + 1, src_w - 1)
    y1 = np.minimum(y0 + 1, src_h - 1)
    fx = (cx - x0)[:, :, np.newaxis].astype(np.float32)
    fy = (cy - y0)[:, :, np.newaxis].astype(np.float32)
    src = src_arr.astype(np.float32)

    result = (src[y0, x0] * (1 - fx) * (1 - fy)
              + src[y0, x1] * fx * (1 - fy)
              + src[y1, x0] * (1 - fx) * fy
              + src[y1, x1] * fx * fy)

    # Polygon mask.  Skip supersampling during live drag (grid_size <= _GRID_LIVE)
    # to avoid allocating 4× meshgrids on every mouse-move event.
    ss = 1 if grid_size <= _GRID_LIVE else 4
    Ws, Hs = out_w * ss, out_h * ss
    xs = (np.arange(Ws, dtype=np.float32) + 0.5) / ss
    ys = (np.arange(Hs, dtype=np.float32) + 0.5) / ss
    px, py = np.meshgrid(xs, ys)

    n = len(points)
    # Shoelace sum: positive → polygon is CW in screen coords (y-down),
    # interior points have cross ≤ 0 for each directed edge.
    area = sum(
        (points[(i + 1) % n][0] - points[i][0]) * (points[i][1] + points[(i + 1) % n][1])
        for i in range(n)
    )
    inside = np.ones((Hs, Ws), dtype=np.bool_)
    for i in range(n):
        ax, ay = points[i]
        bx, by = points[(i + 1) % n]
        cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        inside &= (cross <= 0) if area >= 0 else (cross >= 0)

    if ss > 1:
        mask = inside.reshape(out_h, ss, out_w, ss).mean(axis=(1, 3))[:, :, np.newaxis]
    else:
        mask = inside[:, :, np.newaxis].astype(np.float32)

    result[:, :, 3:4] *= mask
    return np.clip(result, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# GIMP helpers
# ---------------------------------------------------------------------------

def _image_to_layer_pts(image_pts, drawable):
    _, off_x, off_y = drawable.get_offsets()
    return [[p[0] - off_x, p[1] - off_y] for p in image_pts]


# ---------------------------------------------------------------------------
# Apply transform
# ---------------------------------------------------------------------------

_GRID_LIVE = 16
_GRID_PREV = 32
_GRID_FINAL = 128


def _apply_transform(image, drawable, image_pts, blend=1.0):
    """Run the full-resolution transform and replace the layer in-place."""
    Gimp.progress_init("Piranesi: computing transform...")

    src_arr = _drawable_to_array(drawable)
    out_h, out_w = src_arr.shape[:2]
    Gimp.progress_update(0.15)

    layer_pts = _image_to_layer_pts(image_pts, drawable)
    result = numpy_transform(src_arr, out_w, out_h, layer_pts, _GRID_FINAL, blend=blend)
    Gimp.progress_update(0.85)

    _, off_x, off_y = drawable.get_offsets()
    stack_pos = image.get_item_position(drawable)

    new_layer = Gimp.Layer.new(
        image, drawable.get_name(), out_w, out_h,
        Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL,
    )
    image.undo_group_start()
    image.insert_layer(new_layer, None, stack_pos)
    new_layer.set_offsets(off_x, off_y)
    shadow = new_layer.get_shadow_buffer()
    shadow.set(Gegl.Rectangle.new(0, 0, out_w, out_h), "R'G'B'A u8", result.tobytes())
    shadow.flush()
    new_layer.merge_shadow(True)
    new_layer.update(0, 0, out_w, out_h)
    image.remove_layer(drawable)
    image.undo_group_end()

    Gimp.displays_flush()
    Gimp.progress_update(1.0)


# ---------------------------------------------------------------------------
# Floating control panel
# ---------------------------------------------------------------------------

_HANDLE_R = 8
_HANDLE_HIT_R = 16
_PANEL_MAX = 720


class _ControlPanel:
    """Floating dialog with draggable corner handles.

    Corner order: BL, BR, TR, TL (matches numpy_transform).
    Corners stored in layer-local pixel coordinates.
    """

    def __init__(self, image, drawable):
        self._image = image
        self._drawable = drawable
        _, self._off_x, self._off_y = drawable.get_offsets()

        src_arr = _drawable_to_array(drawable)
        src_h, src_w = src_arr.shape[:2]

        sc = min(_PANEL_MAX / src_w, _PANEL_MAX / src_h, 1.0)
        self._scale = sc
        self._disp_w = max(1, int(src_w * sc))
        self._disp_h = max(1, int(src_h * sc))

        self._disp_arr = _downscale(src_arr, self._disp_w, self._disp_h)

        w, h = float(src_w), float(src_h)
        self._corners = [[0.0, h], [w, h], [w, 0.0], [0.0, 0.0]]
        self._drag_idx = None
        self._blend = 1.0
        self._blend_timer = None
        self._pixbuf = _array_to_pixbuf(self._disp_arr)
        self._refresh_preview(_GRID_PREV)

    # ---------------------------------------------------------------- helpers

    def _corners_disp(self):
        sc = self._scale
        return [[c[0] * sc, c[1] * sc] for c in self._corners]

    def _refresh_preview(self, grid_size=_GRID_PREV):
        sc = self._scale
        pts = [[c[0] * sc, c[1] * sc] for c in self._corners]
        try:
            result = numpy_transform(
                self._disp_arr, self._disp_w, self._disp_h, pts, grid_size, blend=self._blend
            )
            self._pixbuf = _array_to_pixbuf(result)
        except Exception as exc:
            print(f"[piranesi] preview error: {exc}", file=sys.stderr)

    def image_pts(self):
        return [[c[0] + self._off_x, c[1] + self._off_y] for c in self._corners]

    # ------------------------------------------------------------ GTK events

    def _on_draw(self, da, cr):
        cr.set_source_rgb(0.25, 0.25, 0.25)
        cr.paint()
        if self._pixbuf:
            Gdk.cairo_set_source_pixbuf(cr, self._pixbuf, 0, 0)
            cr.paint()
        pts = self._corners_disp()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.set_source_rgba(1.0, 1.0, 0.2, 0.7)
        cr.set_line_width(1.5)
        cr.stroke()
        for i, (cx, cy) in enumerate(pts):
            cr.arc(cx, cy, _HANDLE_R, 0, 2 * math.pi)
            if i == self._drag_idx:
                cr.set_source_rgba(1.0, 0.5, 0.0, 1.0)
            else:
                cr.set_source_rgba(1.0, 1.0, 0.2, 1.0)
            cr.fill_preserve()
            cr.set_source_rgb(0.0, 0.0, 0.0)
            cr.set_line_width(1.5)
            cr.stroke()

    def _on_press(self, da, ev):
        if ev.button != 1:
            return
        hit2 = _HANDLE_HIT_R**2
        best, nearest = hit2, None
        for i, (cx, cy) in enumerate(self._corners_disp()):
            d2 = (ev.x - cx) ** 2 + (ev.y - cy) ** 2
            if d2 < best:
                best, nearest = d2, i
        self._drag_idx = nearest
        if nearest is not None:
            da.queue_draw()

    def _on_motion(self, da, ev):
        if self._drag_idx is None:
            return
        sc = self._scale
        self._corners[self._drag_idx] = [ev.x / sc, ev.y / sc]
        self._refresh_preview(_GRID_LIVE)
        da.queue_draw()

    def _on_release(self, da, ev):
        if self._drag_idx is not None:
            self._drag_idx = None
            self._refresh_preview(_GRID_PREV)
            da.queue_draw()

    def _on_blend_changed(self, adj, da):
        self._blend = adj.get_value()
        if self._blend_timer is not None:
            GLib.source_remove(self._blend_timer)
        self._blend_timer = GLib.timeout_add(80, self._blend_timeout, da)

    def _blend_timeout(self, da):
        self._blend_timer = None
        self._refresh_preview(_GRID_PREV)
        da.queue_draw()
        return False

    # ------------------------------------------------------------------- run

    def run(self):
        dlg = Gtk.Dialog(title="Piranesi - Perspective Transform")
        dlg.set_resizable(True)

        da = Gtk.DrawingArea()
        da.set_size_request(self._disp_w, self._disp_h)
        da.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        da.connect("draw", self._on_draw)
        da.connect("button-press-event", self._on_press)
        da.connect("button-release-event", self._on_release)
        da.connect("motion-notify-event", self._on_motion)

        vb = dlg.get_content_area()
        vb.pack_start(da, True, True, 0)

        lbl = Gtk.Label(label="Drag the corner handles - release to update preview")
        lbl.set_margin_top(4)
        lbl.set_margin_bottom(2)
        vb.pack_start(lbl, False, False, 0)

        blend_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        blend_box.set_margin_start(8)
        blend_box.set_margin_end(8)
        blend_box.set_margin_bottom(4)
        blend_box.pack_start(Gtk.Label(label="Projection:"), False, False, 0)

        blend_adj = Gtk.Adjustment(
            value=self._blend, lower=0.0, upper=1.0,
            step_increment=0.01, page_increment=0.1,
        )
        slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=blend_adj)
        slider.set_draw_value(False)
        slider.set_hexpand(True)
        slider.add_mark(0.0, Gtk.PositionType.BOTTOM, "Rectilinear")
        slider.add_mark(0.5, Gtk.PositionType.BOTTOM, None)
        slider.add_mark(1.0, Gtk.PositionType.BOTTOM, "Piranesi")
        blend_adj.connect("value-changed", self._on_blend_changed, da)
        blend_box.pack_start(slider, True, True, 0)
        vb.pack_start(blend_box, False, False, 0)

        dlg.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        ok_btn = dlg.add_button("_Apply", Gtk.ResponseType.OK)
        ok_btn.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)

        dlg.show_all()
        response = dlg.run()
        dlg.destroy()
        return response == Gtk.ResponseType.OK


# ---------------------------------------------------------------------------
# GIMP 3 plugin class
# ---------------------------------------------------------------------------

class PiranesiPlugin(Gimp.PlugIn):

    def do_set_i18n(self, name):
        return False, None, None

    def do_query_procedures(self):
        return ["plug-in-piranesi"]

    def do_create_procedure(self, name):
        proc = Gimp.ImageProcedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, self._run, None,
        )
        proc.set_image_types("RGB*, GRAY*")
        proc.set_menu_label("Piranesi...")
        proc.add_menu_path("<Image>/Filters/Distorts")
        proc.set_documentation(
            "Piranesi perspective transform",
            "Opens a dialog showing the layer with four draggable corner handles. "
            "Drag to reshape the perspective quad, mix conventional rectilinear "
            "perspective with the slider, then click Apply to warp the layer.",
            name,
        )
        proc.set_attribution("Bruno Postle", "Bruno Postle", "2026")
        return proc

    def _run(self, procedure, run_mode, image, drawables, config, run_data):
        drawable = drawables[0] if drawables else image.get_active_drawable()

        if run_mode != Gimp.RunMode.INTERACTIVE:
            return procedure.new_return_values(
                Gimp.PDBStatusType.CALLING_ERROR, GLib.Error()
            )

        GimpUi.init("piranesi")

        panel = _ControlPanel(image, drawable)
        if panel.run():
            _apply_transform(image, drawable, panel.image_pts(), blend=panel._blend)

        Gimp.displays_flush()
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


Gimp.main(PiranesiPlugin.__gtype__, sys.argv)
