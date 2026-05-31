#!/usr/bin/env python3
"""
Piranesi GIMP 3.x plugin — interactive perspective transform.

Canvas handles: four anchor points on a GIMP path, editable with the
Paths tool directly on the canvas.  A small floating panel shows a
preview thumbnail and Apply / Cancel buttons.

Install:
  mkdir -p ~/.config/GIMP/3.0/plug-ins/piranesi_gimp
  cp piranesi_gimp.py ~/.config/GIMP/3.0/plug-ins/piranesi_gimp/piranesi_gimp.py
  chmod +x ~/.config/GIMP/3.0/plug-ins/piranesi_gimp/piranesi_gimp.py

Requires: Pillow  (pip install pillow)
"""

import math
import os
import sys
import tempfile

import gi

gi.require_version("Gimp",       "3.0")
gi.require_version("GimpUi",    "3.0")
gi.require_version("Gtk",       "3.0")
gi.require_version("Gdk",       "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gimp, GimpUi, GLib, GObject, Gtk, Gdk, GdkPixbuf, Gio

try:
    from PIL import Image as _PIL, ImageChops, ImageDraw

    _BILINEAR = getattr(_PIL.Resampling, "BILINEAR", _PIL.BILINEAR)
    _LANCZOS  = getattr(_PIL.Resampling, "LANCZOS",  _PIL.LANCZOS)
    _MESH     = getattr(_PIL.Transform,  "MESH",     _PIL.MESH)

    _DEPS_OK  = True
    _DEPS_ERR = ""
except ImportError as _exc:
    _DEPS_OK  = False
    _DEPS_ERR = str(_exc)


# ---------------------------------------------------------------------------
# Vector / geometry helpers  (mirrors piranesi.py)
# ---------------------------------------------------------------------------

def _sub(a, b):   return [a[0] - b[0], a[1] - b[1]]
def _add(a, b):   return [a[0] + b[0], a[1] + b[1]]
def _scale(v, f): return [v[0] * f,    v[1] * f]
def _dist(a, b):  return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)


def _line(p0, p1):
    dx = p1[0] - p0[0] or 1e-11
    a  = (p1[1] - p0[1]) / dx
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
    return (k ** t - 1.0) / (k - 1.0)


def build_transforms(a, b, c, d, out_w, out_h):
    """Return *(forward, reverse)* for the Piranesi quad mapping.

    Points are pixel coords in the OUTPUT image:
      a = bottom-left  b = bottom-right  c = top-right  d = top-left
    """
    vp_cd = _intersect(_line(b, c), _line(d, a))
    vp_da = _intersect(_line(a, b), _line(c, d))

    eps  = 1e-10
    k_ab = _dist(vp_da, b) / max(_dist(vp_da, a), eps)
    k_bc = _dist(vp_cd, b) / max(_dist(vp_cd, c), eps)
    k_cd = _dist(vp_da, c) / max(_dist(vp_da, d), eps)
    k_da = _dist(vp_cd, a) / max(_dist(vp_cd, d), eps)

    def forward(x, y):
        px_ab = _add(a, _scale(_sub(b, a), _k_interp(k_ab, x)))
        px_cd = _add(d, _scale(_sub(c, d), _k_interp(k_cd, x)))
        py_bc = _add(c, _scale(_sub(b, c), _k_interp(k_bc, y)))
        py_da = _add(d, _scale(_sub(a, d), _k_interp(k_da, y)))
        pt = _intersect(_line(px_ab, px_cd), _line(py_bc, py_da))
        return pt[0], pt[1]

    def reverse(px, py):
        xn, yn = px / out_w, py / out_h
        x,  y  = 0.5, 0.5
        step   = 0.001
        lx, ly = x, y
        for _ in range(11):
            fx0, fy0 = forward(x, y)
            fx1, _   = forward(x + step, y)
            _,   fy2 = forward(x, y + step)
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
            ul, ll = grid[gy][gx],         grid[gy + 1][gx]
            lr, ur = grid[gy + 1][gx + 1], grid[gy][gx + 1]
            mesh.append((
                (x0, y0, x1, y1),
                (ul[0], ul[1], ll[0], ll[1], lr[0], lr[1], ur[0], ur[1]),
            ))

    warped = src.transform((out_w, out_h), _MESH, mesh, _BILINEAR)

    sc       = 4
    mask_big = _PIL.new("L", (out_w * sc, out_h * sc), 0)
    ImageDraw.Draw(mask_big).polygon(
        [(p[0] * sc, p[1] * sc) for p in points], fill=255
    )
    mask = mask_big.resize((out_w, out_h), _LANCZOS)

    r, g, b_ch, alpha = warped.split()
    return _PIL.merge("RGBA", (r, g, b_ch, ImageChops.multiply(alpha, mask)))


# ---------------------------------------------------------------------------
# GIMP 3 file I/O helpers
# ---------------------------------------------------------------------------

def _drawable_to_pil(image, drawable):
    """Export a GIMP drawable to a PIL Image via a temp PNG."""
    tmp = tempfile.mktemp(suffix=".png")
    try:
        Gimp.file_overwrite(
            Gimp.RunMode.NONINTERACTIVE,
            image,
            [drawable],
            Gio.File.new_for_path(tmp),
        )
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
        return Gimp.file_load(
            Gimp.RunMode.NONINTERACTIVE,
            Gio.File.new_for_path(tmp),
        )
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# GIMP canvas path (Vectors) helpers
# ---------------------------------------------------------------------------

_PATH_NAME = "Piranesi corners"


def _create_corner_path(image, drawable):
    """Create a closed quadrilateral path at the layer's bounding box.

    Anchor order: BL → BR → TR → TL  (clockwise starting bottom-left).
    Returns the new Gimp.Vectors object.
    """
    off_x, off_y = drawable.get_offsets()
    w = drawable.get_width()
    h = drawable.get_height()

    # Image-space coordinates, Y-down convention (bottom = off_y + h)
    corners_image = [
        (off_x,     off_y + h),   # BL
        (off_x + w, off_y + h),   # BR
        (off_x + w, off_y),       # TR
        (off_x,     off_y),       # TL
    ]

    vectors = Gimp.Vectors.new(image, _PATH_NAME)
    image.insert_vectors(vectors, None, -1)
    vectors.set_visible(True)

    # Build bezier control-point list.  For a straight-edged quad every
    # anchor's two handles sit exactly on the anchor itself (linear tangent).
    # Format per anchor: [ctrl_before_x, ctrl_before_y,
    #                      anchor_x,     anchor_y,
    #                      ctrl_after_x, ctrl_after_y]
    ctrl_pts = []
    for px, py in corners_image:
        ctrl_pts.extend([float(px), float(py),   # ctrl before
                         float(px), float(py),   # anchor
                         float(px), float(py)])  # ctrl after

    vectors.bezier_stroke_new(ctrl_pts, len(ctrl_pts), True)  # closed=True
    return vectors


def _read_path_corners(vectors):
    """Return [[BL], [BR], [TR], [TL]] as IMAGE-space [x, y] pairs.

    Reads the first stroke of *vectors* and extracts the first four anchors.
    Returns None if the path has fewer than four anchors.
    """
    stroke_ids = vectors.get_strokes()
    if not stroke_ids:
        return None

    coords, _closed = vectors.stroke_get_points(stroke_ids[0])
    # 6 floats per anchor (ctrl_before xy, anchor xy, ctrl_after xy)
    n_anchors = len(coords) // 6
    if n_anchors < 4:
        return None

    return [
        [int(coords[i * 6 + 2]), int(coords[i * 6 + 3])]
        for i in range(4)
    ]


def _image_to_layer_pts(image_pts, drawable):
    """Convert image-space corner points to layer-local coordinates."""
    off_x, off_y = drawable.get_offsets()
    return [[p[0] - off_x, p[1] - off_y] for p in image_pts]


# ---------------------------------------------------------------------------
# Apply transform
# ---------------------------------------------------------------------------

_GRID_PREV  = 16
_GRID_FINAL = 64


def _apply_transform(image, drawable, image_pts):
    """Run the full-resolution Piranesi transform and open the result."""
    Gimp.progress_init("Piranesi: computing transform…")

    src      = _drawable_to_pil(image, drawable)
    out_w, out_h = src.size
    Gimp.progress_update(0.15)

    # Convert image-space handles to layer-local pixel coords
    layer_pts = _image_to_layer_pts(image_pts, drawable)

    result = pil_transform(src, out_w, out_h, layer_pts, _GRID_FINAL)
    Gimp.progress_update(0.85)

    new_image = _pil_to_new_gimp_image(result)
    Gimp.display_new(new_image)
    Gimp.displays_flush()
    Gimp.progress_update(1.0)


# ---------------------------------------------------------------------------
# Floating control panel
# ---------------------------------------------------------------------------

_PANEL_PREVIEW_MAX = 300   # thumbnail fits in this bounding box


class _ControlPanel:
    """Small floating window: preview thumbnail + Refresh / Apply / Cancel.

    The plugin runs in a separate process from GIMP, so this window does NOT
    block the GIMP canvas — the user can freely switch tools and drag path
    anchors while this panel is open.
    """

    def __init__(self, image, drawable, vectors):
        self.image    = image
        self.drawable = drawable
        self.vectors  = vectors

        self._src_pil = _drawable_to_pil(image, drawable)
        src_w, src_h  = self._src_pil.size

        scale = min(_PANEL_PREVIEW_MAX / src_w,
                    _PANEL_PREVIEW_MAX / src_h, 1.0)
        self._pw        = max(1, int(src_w * scale))
        self._ph        = max(1, int(src_h * scale))
        self._src_scale = scale   # layer-px → preview-px

    # ---------------------------------------------------------------- dialog

    def run(self):
        """Show the panel; return True if Apply was clicked, False otherwise."""
        dlg = Gtk.Dialog(title="Piranesi Perspective Transform")
        dlg.set_keep_above(True)
        dlg.set_resizable(False)
        # Non-modal within the plugin process, but GIMP's UI is unaffected
        # regardless because the plugin runs in its own process.
        dlg.add_button("_Cancel", Gtk.ResponseType.CANCEL)
        ok_btn = dlg.add_button("_Apply", Gtk.ResponseType.OK)
        ok_btn.get_style_context().add_class("suggested-action")
        dlg.set_default_response(Gtk.ResponseType.OK)

        vb = dlg.get_content_area()
        vb.set_border_width(10)
        vb.set_spacing(8)

        # Instructions
        lbl = Gtk.Label()
        lbl.set_markup(
            "<b>Drag the path anchors on the GIMP canvas</b>\n"
            "to reposition the four corner handles, then\n"
            "click <i>Refresh Preview</i> or <i>Apply</i>.\n\n"
            "Switch to the <b>Paths tool</b> (Shift+B) to edit."
        )
        lbl.set_xalign(0.0)
        vb.pack_start(lbl, False, False, 0)

        # Preview thumbnail
        self._gtk_image = Gtk.Image()
        self._gtk_image.set_size_request(self._pw, self._ph)
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self._gtk_image)
        vb.pack_start(frame, False, False, 0)

        # Refresh button
        refresh = Gtk.Button(label="_Refresh Preview")
        refresh.set_use_underline(True)
        refresh.connect("clicked", lambda _b: self._update_preview())
        vb.pack_start(refresh, False, False, 0)

        dlg.show_all()
        self._update_preview()          # initial thumbnail

        response = dlg.run()
        dlg.destroy()
        return response == Gtk.ResponseType.OK

    # -------------------------------------------------------------- preview

    def _update_preview(self):
        image_pts = _read_path_corners(self.vectors)
        if image_pts is None:
            return

        try:
            layer_pts  = _image_to_layer_pts(image_pts, self.drawable)
            scaled_pts = [[p[0] * self._src_scale,
                           p[1] * self._src_scale] for p in layer_pts]
            src_small  = self._src_pil.resize(
                (self._pw, self._ph), _BILINEAR
            )
            result = pil_transform(
                src_small, self._pw, self._ph, scaled_pts, _GRID_PREV
            )

            raw = result.tobytes()
            pb  = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(raw),
                GdkPixbuf.Colorspace.RGB,
                True, 8,
                self._pw, self._ph,
                self._pw * 4,
            )
            self._gtk_image.set_from_pixbuf(pb)
        except Exception as exc:
            print(f"[piranesi] preview error: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# GIMP 3 plugin class
# ---------------------------------------------------------------------------

class PiranesiPlugin(Gimp.PlugIn):

    def do_query_procedures(self):
        return ["plug-in-piranesi"]

    def do_create_procedure(self, name):
        proc = Gimp.ImageProcedure.new(
            self, name,
            Gimp.PDBProcType.PLUGIN,
            self._run, None,
        )
        proc.set_image_types("RGB*, GRAY*")
        proc.set_menu_label("Piranesi…")
        proc.add_menu_path("<Image>/Filters/Distorts")
        proc.set_documentation(
            "Piranesi perspective transform",
            (
                "Places four draggable path anchors at the layer corners. "
                "Edit them with the Paths tool on the GIMP canvas, then "
                "click Apply in the floating panel to run the transform."
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

        # 1. Create a path at the layer's bounding box corners
        vectors = _create_corner_path(image, drawable)
        image.set_active_vectors(vectors)
        Gimp.displays_flush()

        # 2. Switch to the Paths tool so anchors are immediately editable
        try:
            Gimp.context_set_tool("gimp-vector-tool")
            Gimp.displays_flush()
        except Exception:
            pass   # non-fatal: user can switch manually

        # 3. Show the floating control panel
        panel  = _ControlPanel(image, drawable, vectors)
        apply_ = panel.run()

        # 4. Read final anchor positions and apply (or just clean up on cancel)
        if apply_:
            image_pts = _read_path_corners(vectors)
            if image_pts:
                _apply_transform(image, drawable, image_pts)

        # 5. Remove the helper path
        image.remove_vectors(vectors)
        Gimp.displays_flush()

        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


Gimp.main(PiranesiPlugin.__gtype__, sys.argv)
