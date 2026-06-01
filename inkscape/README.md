# Piranesi Transform — Inkscape Extension

Applies Piranesi's non-linear perspective to selected vector paths. Unlike a
standard projective (homography) transform, opposite sides of the
quadrilateral remain visually parallel — the technique used by Piranesi and
Canaletto in 18th-century veduta paintings.

A blend slider lets you mix between conventional rectilinear perspective (0)
and the full Piranesi transform (100).

## Installation

Copy both files to your Inkscape user extensions directory:

```
cp piranesi_inkscape.py piranesi_inkscape.inx ~/.config/inkscape/extensions/
```

Then restart Inkscape. The extension appears under **Extensions → Transforms →
Piranesi Transform**.

## Usage

1. Draw the paths you want to warp.
2. Draw a 4-node closed polygon to define the output shape — use the **Pen**
   or **Rectangle** tool, then the **Node** tool to drag the corners into
   position.
3. Select all paths **and** the polygon. The polygon must be topmost in
   z-order; raise it with **Object → Raise to Top** if needed.
4. Run **Extensions → Transforms → Piranesi Transform**.
5. Adjust the **Perspective blend** slider (enable **Live preview** to see
   the result on the canvas as you drag).
6. Click **Apply**, then **Close**.

The reference quad is left in place after the transform. Convert objects to
paths first with **Object → Object to Path** if needed.

## Notes

- All element transforms are flattened before warping.
- Bézier control handles are transformed directly; smoothness is preserved
  approximately for gentle distortions.
- Clicking **Apply** twice will apply the transform twice — use **Edit →
  Undo** (Ctrl+Z) to recover if this happens.
