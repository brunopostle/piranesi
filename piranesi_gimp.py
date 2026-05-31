#!/usr/bin/env python3
"""
Piranesi GIMP 3.x plugin — interactive perspective transform.

Found in GIMP under:  Filters ▸ Distorts ▸ Piranesi…

A floating dialog shows the layer with four draggable corner handles.
Drag any corner to reshape the perspective quad; release to update the
low-quality preview.  Click Apply to warp the layer into a new image.

Install:
  mkdir -p ~/.config/GIMP/3.2/plug-ins/piranesi_gimp
  cp piranesi_gimp.py ~/.config/GIMP/3.2/plug-ins/piranesi_gimp/piranesi_gimp.py
  chmod +x ~/.config/GIMP/3.2/plug-ins/piranesi_gimp/piranesi_gimp.py

Requires: Pillow  (pip install pillow)

Copyright (C) 2025  Bruno Postle
License: GNU General Public License v3 or later <https://www.gnu.org/licenses/gpl-3.0.html>
"""

import math
import os
import sys
import tempfile

import gi
from gi.repository import Gimp, GimpUi, GLib, Gtk, Gdk, GdkPixbuf, Gio, Gegl

gi.require_version("Gimp", "3.0")
gi.require_version("GimpUi", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gegl", "0.4")


try:
    from PIL import Image as _PIL, ImageChops, ImageDraw

    _BILINEAR = getattr(_PIL.Resampling, "BILINEAR", _PIL.BILINEAR)
    _LANCZOS = getattr(_PIL.Resampling, "LANCZOS", _PIL.LANCZOS)
    _MESH = getattr(_PIL.Transform, "MESH", _PIL.MESH)

    _DEPS_OK = True
    _DEPS_ERR = ""
except ImportError as _exc:
    _DEPS_OK = False
    _DEPS_ERR = str(_exc)


# ---------------------------------------------------------------------------
# Vector / geometry helpers  (mirrors piranesi.py)
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
# Piranesi transform core
# ---------------------------------------------------------------------------


def _k_interp(k, t):
    if abs(k - 1.0) < 1e-9:
        return t
    return (k**t - 1.0) / (k - 1.0)


def _lines_parallel(l0, l1):
    """True when two lines are effectively parallel.

    Near-vertical lines get a slope of ±dy/1e-11 from _line(), so opposite-
    direction verticals have slopes with opposite signs and a huge difference.
    Catch that case by checking whether both slopes are simply very large.
    """
    a0, a1 = l0["a"], l1["a"]
    if abs(a0) > 1e8 and abs(a1) > 1e8:  # both near-vertical
        return True
    return abs(a0 - a1) < 1e-4 * (1.0 + abs(a0) + abs(a1))


def build_transforms(a, b, c, d, out_w, out_h):
    """Return *(forward, reverse)* for the Piranesi quad mapping.

    Points are pixel coords in the OUTPUT image:
      a = bottom-left  b = bottom-right  c = top-right  d = top-left

    When opposite sides are parallel (rectangle / parallelogram) the
    vanishing point is at infinity; detect this and use k=1 (linear
    interpolation) so the identity case doesn't produce garbage.
    """
    eps = 1e-10

    line_ab = _line(a, b)
    line_cd = _line(c, d)
    line_bc = _line(b, c)
    line_da = _line(d, a)

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

    def forward(x, y):
        px_ab = _add(a, _scale(_sub(b, a), _k_interp(k_ab, x)))
        px_cd = _add(d, _scale(_sub(c, d), _k_interp(k_cd, x)))
        if _bilinear:
            # Opposite sides parallel: use standard bilinear patch to avoid
            # catastrophic cancellation from near-vertical connecting lines.
            # y=0 is TOP (px_cd) and y=1 is BOTTOM (px_ab) in Piranesi coords.
            pt = _add(px_cd, _scale(_sub(px_ab, px_cd), y))
        else:
            py_bc = _add(c, _scale(_sub(b, c), _k_interp(k_bc, y)))
            py_da = _add(d, _scale(_sub(a, d), _k_interp(k_da, y)))
            pt = _intersect(_line(px_ab, px_cd), _line(py_bc, py_da))
        return pt[0], pt[1]

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
# PIL-based image transform
# ---------------------------------------------------------------------------


def pil_transform(src, out_w, out_h, points, grid_size=32):
    """Warp *src* into the quad given by *points* [BL, BR, TR, TL].

    Returns an RGBA PIL Image of size out_w × out_h.
    grid_size=16 → fast preview;  grid_size=64 → final quality.
    """
    a, b, c, d = [list(map(float, p)) for p in points]
    _, reverse = build_transforms(a, b, c, d, out_w, out_h)

    if src.mode != "RGBA":
        src = src.convert("RGBA")
    sw, sh = src.size

    grid = [[None] * (grid_size + 1) for _ in range(grid_size + 1)]
    for gy in range(grid_size + 1):
        for gx in range(grid_size + 1):
            ox = int(gx * (out_w - 1) / grid_size)
            oy = int(gy * (out_h - 1) / grid_size)
            sx, sy = reverse(ox, oy)
            grid[gy][gx] = (sx * sw, sy * sh)

    mesh = []
    for gy in range(grid_size):
        for gx in range(grid_size):
            x0 = gx * out_w // grid_size
            y0 = gy * out_h // grid_size
            x1 = (gx + 1) * out_w // grid_size
            y1 = (gy + 1) * out_h // grid_size
            ul, ll = grid[gy][gx], grid[gy + 1][gx]
            lr, ur = grid[gy + 1][gx + 1], grid[gy][gx + 1]
            mesh.append(
                (
                    (x0, y0, x1, y1),
                    (ul[0], ul[1], ll[0], ll[1], lr[0], lr[1], ur[0], ur[1]),
                )
            )

    warped = src.transform((out_w, out_h), _MESH, mesh, _BILINEAR)

    sc = 4
    mask_big = _PIL.new("L", (out_w * sc, out_h * sc), 0)
    ImageDraw.Draw(mask_big).polygon([(p[0] * sc, p[1] * sc) for p in points], fill=255)
    mask = mask_big.resize((out_w, out_h), _LANCZOS)

    r, g, b_ch, alpha = warped.split()
    return _PIL.merge("RGBA", (r, g, b_ch, ImageChops.multiply(alpha, mask)))


# ---------------------------------------------------------------------------
# GIMP 3 file I/O helpers
# ---------------------------------------------------------------------------


def _drawable_to_pil(image, drawable):
    """Export a GIMP drawable to a PIL Image via a GEGL-written temp PNG."""
    tmp = tempfile.mktemp(suffix=".png")
    try:
        Gegl.init(None)
        buf = drawable.get_buffer()

        graph = Gegl.Node.new()
        src = graph.create_child("gegl:buffer-source")
        src.set_property("buffer", buf)
        sink = graph.create_child("gegl:png-save")
        sink.set_property("path", tmp)
        src.link(sink)
        sink.process()

        img = _PIL.open(tmp)
        img.load()
        return img
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _pil_to_new_gimp_image(result_pil):
    """Save a PIL Image to a temp PNG and return a new GIMP image."""
    tmp = tempfile.mktemp(suffix=".png")
    try:
        result_pil.save(tmp)
        gfile = Gio.File.new_for_path(tmp)
        # Try the GIMP 3 module-level load function first; fall back to PDB.
        try:
            return Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, gfile)
        except AttributeError:
            pass
        proc = Gimp.get_pdb().lookup_procedure("file-png-load")
        if proc is None:
            proc = Gimp.get_pdb().lookup_procedure("gimp-file-load")
        config = proc.create_config()
        config.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
        config.set_property("file", gfile)
        return proc.run(config).index(1)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _image_to_layer_pts(image_pts, drawable):
    """Convert image-space corner points to layer-local coordinates."""
    _, off_x, off_y = drawable.get_offsets()
    return [[p[0] - off_x, p[1] - off_y] for p in image_pts]


# ---------------------------------------------------------------------------
# Apply transform
# ---------------------------------------------------------------------------

_GRID_PREV = 32
_GRID_FINAL = 64


def _apply_transform(image, drawable, image_pts):
    """Run the full-resolution Piranesi transform and open the result."""
    Gimp.progress_init("Piranesi: computing transform…")

    src = _drawable_to_pil(image, drawable)
    out_w, out_h = src.size
    Gimp.progress_update(0.15)

    # Convert image-space handles to layer-local pixel coords
    layer_pts = _image_to_layer_pts(image_pts, drawable)

    result = pil_transform(src, out_w, out_h, layer_pts, _GRID_FINAL)
    Gimp.progress_update(0.85)

    new_image = _pil_to_new_gimp_image(result)
    Gimp.Display.new(new_image)
    Gimp.displays_flush()
    Gimp.progress_update(1.0)


# ---------------------------------------------------------------------------
# Floating control panel — self-contained GTK canvas with draggable handles
# ---------------------------------------------------------------------------

_HANDLE_R = 8  # visual radius of corner handles (display px)
_HANDLE_HIT_R = 16  # click/drag hit radius (display px)
_PANEL_MAX = 720  # maximum drawing-area dimension (display px)


class _ControlPanel:
    """Floating dialog with a Cairo canvas showing the layer and four
    draggable corner handles.  No GIMP path tool required.

    Corner order throughout: BL, BR, TR, TL (matches pil_transform).
    Corners are stored in layer-local pixel coordinates.
    """

    def __init__(self, image, drawable):
        self._image = image
        self._drawable = drawable
        _, self._off_x, self._off_y = drawable.get_offsets()

        self._src_pil = _drawable_to_pil(image, drawable)
        src_w, src_h = self._src_pil.size

        sc = min(_PANEL_MAX / src_w, _PANEL_MAX / src_h, 1.0)
        self._scale = sc
        self._disp_w = max(1, int(src_w * sc))
        self._disp_h = max(1, int(src_h * sc))

        # Pre-scale source once for fast preview rendering
        disp_src = self._src_pil.resize((self._disp_w, self._disp_h), _BILINEAR)
        if disp_src.mode != "RGBA":
            disp_src = disp_src.convert("RGBA")
        self._disp_src = disp_src

        # Initial corners: full layer bounding box, layer-local coords
        w, h = float(src_w), float(src_h)
        self._corners = [[0.0, h], [w, h], [w, 0.0], [0.0, 0.0]]
        self._drag_idx = None
        self._pixbuf = self._pil_to_pixbuf(self._disp_src)
        self._refresh_preview()

    # ---------------------------------------------------------------- helpers

    def _pil_to_pixbuf(self, img):
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        data = img.tobytes()
        return GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(data),
            GdkPixbuf.Colorspace.RGB,
            True,
            8,
            img.width,
            img.height,
            img.width * 4,
        )

    def _corners_disp(self):
        sc = self._scale
        return [[c[0] * sc, c[1] * sc] for c in self._corners]

    def _refresh_preview(self):
        sc = self._scale
        pts = [[c[0] * sc, c[1] * sc] for c in self._corners]
        try:
            result = pil_transform(
                self._disp_src, self._disp_w, self._disp_h, pts, _GRID_PREV
            )
            self._pixbuf = self._pil_to_pixbuf(result)
        except Exception as exc:
            print(f"[piranesi] preview error: {exc}", file=sys.stderr)

    def image_pts(self):
        """Return corners as image-space [x, y] pairs for _apply_transform."""
        return [[c[0] + self._off_x, c[1] + self._off_y] for c in self._corners]

    # ------------------------------------------------------------ GTK events

    def _on_draw(self, da, cr):
        cr.set_source_rgb(0.25, 0.25, 0.25)
        cr.paint()

        if self._pixbuf:
            Gdk.cairo_set_source_pixbuf(cr, self._pixbuf, 0, 0)
            cr.paint()

        # Quad outline
        pts = self._corners_disp()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.close_path()
        cr.set_source_rgba(1.0, 1.0, 0.2, 0.7)
        cr.set_line_width(1.5)
        cr.stroke()

        # Corner handles
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
        da.queue_draw()

    def _on_release(self, da, ev):
        if self._drag_idx is not None:
            self._drag_idx = None
            self._refresh_preview()
            da.queue_draw()

    # ------------------------------------------------------------------- run

    def run(self):
        """Show the dialog; return True if Apply was clicked."""
        dlg = Gtk.Dialog(title="Piranesi – Perspective Transform")
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

        lbl = Gtk.Label(label="Drag the corner handles · release to update preview")
        lbl.set_margin_top(4)
        lbl.set_margin_bottom(4)
        vb.pack_start(lbl, False, False, 0)

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
        return False, None, None  # disable localisation

    def do_query_procedures(self):
        return ["plug-in-piranesi"]

    def do_create_procedure(self, name):
        proc = Gimp.ImageProcedure.new(
            self,
            name,
            Gimp.PDBProcType.PLUGIN,
            self._run,
            None,
        )
        proc.set_image_types("RGB*, GRAY*")
        proc.set_menu_label("Piranesi…")
        proc.add_menu_path("<Image>/Filters/Distorts")
        proc.set_documentation(
            "Piranesi perspective transform",
            (
                "Opens a dialog showing the layer with four draggable corner "
                "handles. Drag to reshape the perspective quad, then click "
                "Apply to warp the layer into a new image."
            ),
            name,
        )
        proc.set_attribution("Bruno Postle", "Bruno Postle", "2025")
        return proc

    def _run(self, procedure, run_mode, image, drawables, config, run_data):
        if not _DEPS_OK:
            Gimp.message(f"Piranesi: missing dependency — {_DEPS_ERR}")
            return procedure.new_return_values(
                Gimp.PDBStatusType.CALLING_ERROR, GLib.Error()
            )

        drawable = drawables[0] if drawables else image.get_active_drawable()

        if run_mode != Gimp.RunMode.INTERACTIVE:
            return procedure.new_return_values(
                Gimp.PDBStatusType.CALLING_ERROR, GLib.Error()
            )

        GimpUi.init("piranesi")

        panel = _ControlPanel(image, drawable)
        apply_ = panel.run()

        if apply_:
            _apply_transform(image, drawable, panel.image_pts())

        Gimp.displays_flush()
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


Gimp.main(PiranesiPlugin.__gtype__, sys.argv)
