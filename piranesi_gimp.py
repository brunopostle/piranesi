#!/usr/bin/env python3
"""
Piranesi GIMP plugin — interactive perspective transform with draggable corner handles.

Install (GIMP 2.10):
  cp piranesi_gimp.py ~/.config/GIMP/2.10/plug-ins/
  chmod +x ~/.config/GIMP/2.10/plug-ins/piranesi_gimp.py

The plugin appears under Filters > Distorts > Piranesi…
Requires: Pillow  (pip install pillow)
"""

import math
import os
import tempfile

from gimpfu import *  # noqa: F401,F403

try:
    import gtk
    import cairo
    import gobject
    from PIL import Image as _PILImage, ImageChops, ImageDraw

    # Pillow ≥9 renamed these; keep both spellings working
    _MESH     = getattr(_PILImage.Transform,   "MESH",    _PILImage.MESH)
    _BILINEAR = getattr(_PILImage.Resampling,  "BILINEAR", _PILImage.BILINEAR)
    _LANCZOS  = getattr(_PILImage.Resampling,  "LANCZOS",  _PILImage.LANCZOS)

    _DEPS_OK = True
    _DEPS_ERR = ""
except ImportError as _exc:
    _DEPS_OK  = False
    _DEPS_ERR = str(_exc)


# ---------------------------------------------------------------------------
# Vector / geometry helpers  (mirrors piranesi.py)
# ---------------------------------------------------------------------------

def _sub(a, b):    return [a[0] - b[0], a[1] - b[1]]
def _add(a, b):    return [a[0] + b[0], a[1] + b[1]]
def _scale(v, f):  return [v[0] * f,    v[1] * f]
def _dist(a, b):   return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)


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
    """Logarithmic blend; degrades to linear when k≈1."""
    if abs(k - 1.0) < 1e-9:
        return t
    return (k ** t - 1.0) / (k - 1.0)


def build_transforms(a, b, c, d, out_w, out_h):
    """Return *(forward, reverse)* for the Piranesi quad mapping.

    All four corner points are pixel coords in the OUTPUT image:
      a = bottom-left  b = bottom-right  c = top-right  d = top-left

    forward(x, y)   – normalised source [0,1]² → output pixels
    reverse(px, py) – output pixels → normalised source [0,1]²
    """
    vp_cd = _intersect(_line(b, c), _line(d, a))   # VP for left/right edges
    vp_da = _intersect(_line(a, b), _line(c, d))   # VP for top/bottom edges

    eps = 1e-10
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
        x, y = 0.5, 0.5
        step = 0.001
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
    """Warp *src* (PIL Image) into the quadrilateral given by *points*.

    points = [BL, BR, TR, TL] as [x, y] pixel coords in the output canvas.
    Returns an RGBA PIL Image of size out_w × out_h.
    grid_size=16 → fast preview;  grid_size=64 → final quality.
    """
    a, b, c, d = [list(map(float, p)) for p in points]
    _, reverse = build_transforms(a, b, c, d, out_w, out_h)

    if src.mode != "RGBA":
        src = src.convert("RGBA")
    sw, sh = src.size

    # Build reverse-mapping grid
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
            ul, ll = grid[gy][gx],     grid[gy + 1][gx]
            lr, ur = grid[gy + 1][gx + 1], grid[gy][gx + 1]
            mesh.append((
                (x0, y0, x1, y1),
                (ul[0], ul[1], ll[0], ll[1], lr[0], lr[1], ur[0], ur[1]),
            ))

    warped = src.transform((out_w, out_h), _MESH, mesh, _BILINEAR)

    # Anti-aliased quad mask at 4× then downscale
    sc = 4
    mask_big = _PILImage.new("L", (out_w * sc, out_h * sc), 0)
    ImageDraw.Draw(mask_big).polygon(
        [(p[0] * sc, p[1] * sc) for p in points], fill=255
    )
    mask = mask_big.resize((out_w, out_h), _LANCZOS)

    r, g, b_ch, alpha = warped.split()
    return _PILImage.merge("RGBA", (r, g, b_ch, ImageChops.multiply(alpha, mask)))


# ---------------------------------------------------------------------------
# Interactive GTK dialog with draggable corner handles
# ---------------------------------------------------------------------------

_HANDLE_R = 9          # handle circle radius in canvas pixels
_HANDLE_COLORS = [
    (1.00, 0.35, 0.35),  # BL – red
    (0.35, 1.00, 0.35),  # BR – green
    (0.35, 0.60, 1.00),  # TR – blue
    (1.00, 0.85, 0.25),  # TL – yellow
]
_LABELS    = ["BL", "BR", "TR", "TL"]
_QUAD_LOOP = [0, 1, 2, 3, 0]   # draw BL→BR→TR→TL→BL

_CANVAS_MAX  = 500   # preview canvas fits in this bounding box
_GRID_PREV   = 16    # grid cells for live preview (fast)
_GRID_FINAL  = 64    # grid cells for final render (quality)


class _PiranesiDialog:
    """Shows a canvas with the warped preview and four draggable corner handles."""

    def __init__(self, image, drawable):
        self.image    = image
        self.drawable = drawable
        self.out_w    = image.width
        self.out_h    = image.height

        self._scale = min(_CANVAS_MAX / self.out_w, _CANVAS_MAX / self.out_h, 1.0)
        self.cw = max(1, int(self.out_w * self._scale))
        self.ch = max(1, int(self.out_h * self._scale))

        # Default corners: same proportional positions as the CLI defaults
        W, H = self.out_w, self.out_h
        self.points = [
            [int(80   * W / 1600), int(990  * H / 1600)],  # BL
            [int(1400 * W / 1600), int(1350 * H / 1600)],  # BR
            [int(1560 * W / 1600), int(250  * H / 1600)],  # TR
            [int(40   * W / 1600), int(510  * H / 1600)],  # TL
        ]

        self._drag_idx   = None   # which handle is being dragged
        self._preview_pb = None   # current gtk.gdk.Pixbuf
        self._pend       = False  # debounce flag for dragging
        self._src_pil    = self._load_source()

    # ------------------------------------------------------------------
    # Source image I/O via temp PNG
    # ------------------------------------------------------------------

    def _load_source(self):
        tmp = tempfile.mktemp(suffix=".png")
        try:
            pdb.file_png_save(
                RUN_NONINTERACTIVE,
                self.image, self.drawable,
                tmp, tmp, 0, 9, 1, 1, 1, 1, 1,
            )
            img = _PILImage.open(tmp)
            img.load()
            return img
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _c2i(self, cx, cy):
        """Canvas pixels → image pixels."""
        return cx / self._scale, cy / self._scale

    def _i2c(self, ix, iy):
        """Image pixels → canvas pixels."""
        return ix * self._scale, iy * self._scale

    # ------------------------------------------------------------------
    # Dialog
    # ------------------------------------------------------------------

    def run(self):
        """Show the dialog; return list of [BL, BR, TR, TL] or None if cancelled."""
        dlg = gtk.Dialog(
            "Piranesi Perspective Transform",
            flags=gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
        )
        dlg.add_buttons(
            gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
            gtk.STOCK_OK,     gtk.RESPONSE_OK,
        )
        dlg.set_default_response(gtk.RESPONSE_OK)
        dlg.set_resizable(False)

        vb = dlg.vbox
        vb.set_spacing(4)
        vb.set_border_width(6)

        hint = gtk.Label(
            "Drag the coloured handles to reposition the quadrilateral corners.\n"
            "BL = bottom-left   BR = bottom-right   TR = top-right   TL = top-left"
        )
        hint.set_alignment(0.0, 0.5)
        vb.pack_start(hint, False, False, 0)

        self._canvas = gtk.DrawingArea()
        self._canvas.set_size_request(self.cw, self.ch)
        self._canvas.add_events(
            gtk.gdk.BUTTON_PRESS_MASK   |
            gtk.gdk.BUTTON_RELEASE_MASK |
            gtk.gdk.POINTER_MOTION_MASK
        )
        self._canvas.connect("expose-event",         self._on_expose)
        self._canvas.connect("button-press-event",   self._on_press)
        self._canvas.connect("button-release-event", self._on_release)
        self._canvas.connect("motion-notify-event",  self._on_motion)

        frame = gtk.Frame()
        frame.set_shadow_type(gtk.SHADOW_IN)
        frame.add(self._canvas)
        vb.pack_start(frame, False, False, 0)

        self._status = gtk.Label(self._coord_text())
        self._status.set_alignment(0.0, 0.5)
        vb.pack_start(self._status, False, False, 0)

        dlg.show_all()
        gobject.idle_add(self._update_preview)

        response = dlg.run()
        dlg.destroy()
        return self.points if response == gtk.RESPONSE_OK else None

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _on_expose(self, _w, _ev):
        cr = self._canvas.window.cairo_create()
        self._draw(cr)

    def _draw(self, cr):
        cr.set_source_rgb(0.15, 0.15, 0.15)
        cr.paint()

        if self._preview_pb is not None:
            gtk.gdk.cairo_set_source_pixbuf(cr, self._preview_pb, 0, 0)
            cr.paint()

        pts = [self._i2c(p[0], p[1]) for p in self.points]

        # Quad outline
        cr.set_source_rgba(1, 1, 1, 0.75)
        cr.set_line_width(1.5)
        cr.move_to(*pts[_QUAD_LOOP[0]])
        for i in _QUAD_LOOP[1:]:
            cr.line_to(*pts[i])
        cr.stroke()

        # Corner handles
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(9)
        for i, (cx, cy) in enumerate(pts):
            r, g, b = _HANDLE_COLORS[i]
            cr.set_source_rgba(0, 0, 0, 0.4)
            cr.arc(cx + 1.5, cy + 1.5, _HANDLE_R, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(r, g, b)
            cr.arc(cx, cy, _HANDLE_R, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(1.5)
            cr.arc(cx, cy, _HANDLE_R, 0, 2 * math.pi)
            cr.stroke()
            lbl = _LABELS[i]
            ext = cr.text_extents(lbl)
            cr.set_source_rgb(0, 0, 0)
            cr.move_to(cx - ext[2] / 2, cy + ext[3] / 2)
            cr.show_text(lbl)

    # ------------------------------------------------------------------
    # Mouse handling
    # ------------------------------------------------------------------

    def _hit(self, cx, cy):
        for i, p in enumerate(self.points):
            px, py = self._i2c(p[0], p[1])
            if (cx - px) ** 2 + (cy - py) ** 2 <= (_HANDLE_R * 2.5) ** 2:
                return i
        return None

    def _on_press(self, _w, ev):
        if ev.button == 1:
            self._drag_idx = self._hit(ev.x, ev.y)
            if self._drag_idx is not None:
                self._canvas.window.set_cursor(gtk.gdk.Cursor(gtk.gdk.FLEUR))

    def _on_release(self, _w, _ev):
        if self._drag_idx is not None:
            self._drag_idx = None
            self._canvas.window.set_cursor(None)
            self._update_preview()

    def _on_motion(self, _w, ev):
        if self._drag_idx is None:
            return
        ix = max(0, min(self.out_w - 1, int(ev.x / self._scale)))
        iy = max(0, min(self.out_h - 1, int(ev.y / self._scale)))
        self.points[self._drag_idx] = [ix, iy]
        self._status.set_text(self._coord_text())
        self._canvas.queue_draw()
        if not self._pend:
            self._pend = True
            gobject.timeout_add(120, self._deferred_preview)

    def _deferred_preview(self):
        self._pend = False
        self._update_preview()
        return False

    # ------------------------------------------------------------------
    # Preview update
    # ------------------------------------------------------------------

    def _update_preview(self):
        try:
            scaled = [[p[0] * self._scale, p[1] * self._scale] for p in self.points]
            src_small = self._src_pil.resize((self.cw, self.ch), _BILINEAR)
            result = pil_transform(src_small, self.cw, self.ch, scaled, _GRID_PREV)

            raw = result.tobytes()
            self._preview_pb = gtk.gdk.pixbuf_new_from_data(
                raw,
                gtk.gdk.COLORSPACE_RGB,
                True,   # has_alpha
                8,
                self.cw, self.ch,
                self.cw * 4,
            )
            self._canvas.queue_draw()
        except Exception as exc:
            self._status.set_text(f"Preview error: {exc}")

    # ------------------------------------------------------------------

    def _coord_text(self):
        p = self.points
        return (
            f"BL ({p[0][0]}, {p[0][1]})  "
            f"BR ({p[1][0]}, {p[1][1]})  "
            f"TR ({p[2][0]}, {p[2][1]})  "
            f"TL ({p[3][0]}, {p[3][1]})"
        )


# ---------------------------------------------------------------------------
# Apply transform and import result back into GIMP
# ---------------------------------------------------------------------------

def _apply_transform(image, drawable, points):
    gimp.progress_init("Piranesi: computing transform…")

    tmp_in = tempfile.mktemp(suffix=".png")
    try:
        pdb.file_png_save(
            RUN_NONINTERACTIVE,
            image, drawable,
            tmp_in, tmp_in, 0, 9, 1, 1, 1, 1, 1,
        )
        src = _PILImage.open(tmp_in)
        src.load()
    finally:
        if os.path.exists(tmp_in):
            os.unlink(tmp_in)

    gimp.progress_update(0.15)
    result = pil_transform(src, image.width, image.height, points, _GRID_FINAL)
    gimp.progress_update(0.85)

    tmp_out = tempfile.mktemp(suffix=".png")
    try:
        result.save(tmp_out)
        new_img = pdb.gimp_file_load(RUN_NONINTERACTIVE, tmp_out, tmp_out)
    finally:
        if os.path.exists(tmp_out):
            os.unlink(tmp_out)

    gimp.Display(new_img)
    gimp.displays_flush()
    gimp.progress_update(1.0)


# ---------------------------------------------------------------------------
# Entry point registered with GIMP
# ---------------------------------------------------------------------------

def _plugin_main(image, drawable):
    if not _DEPS_OK:
        gimp.message(f"Piranesi plugin: missing dependency — {_DEPS_ERR}")
        return

    dlg = _PiranesiDialog(image, drawable)
    points = dlg.run()
    if points is None:
        return   # user cancelled

    _apply_transform(image, drawable, points)


# ---------------------------------------------------------------------------
# GIMP registration
# ---------------------------------------------------------------------------

register(
    "plug-in-piranesi",
    "Piranesi perspective transform",
    (
        "Apply Piranesi's non-linear quadrilateral perspective transform. "
        "An interactive dialog lets you drag four corner handles on a live "
        "preview to define the output quadrilateral."
    ),
    "Bruno Postle",
    "Bruno Postle",
    "2025",
    "<Image>/Filters/Distorts/Piranesi…",
    "RGB*, GRAY*",
    [],   # no auto-generated param dialog; we show our own
    [],
    _plugin_main,
)

main()
