# Piranesi Transform — GIMP Plugin

Applies Piranesi's non-linear perspective to a GIMP layer. A floating dialog
shows the layer with four draggable corner handles for interactive positioning,
plus a slider to mix between conventional rectilinear perspective and the full
Piranesi transform.

Found in GIMP under **Filters → Distorts → Piranesi…**

## Requirements

- GIMP 3.x (numpy is bundled with GIMP's Python environment)

## Installation

**Linux / macOS**

```
mkdir -p ~/.config/GIMP/3.2/plug-ins/piranesi_gimp
cp gimp/piranesi_gimp.py ~/.config/GIMP/3.2/plug-ins/piranesi_gimp/piranesi_gimp.py
chmod +x ~/.config/GIMP/3.2/plug-ins/piranesi_gimp/piranesi_gimp.py
```

**Windows**

Create the folder `%APPDATA%\GIMP\3.2\plug-ins\piranesi_gimp` and copy
`piranesi_gimp.py` into it, so the path is:

```
%APPDATA%\GIMP\3.2\plug-ins\piranesi_gimp\piranesi_gimp.py
```

Then restart GIMP.

## Usage

1. Open an image and select the layer you want to warp.
2. Run **Filters → Distorts → Piranesi…**
3. A dialog opens showing the layer with four yellow corner handles. Drag the
   handles to define the output quadrilateral. Releasing a handle updates the
   preview at full quality.
4. Adjust the **Projection** slider:
   - **Rectilinear** (left) — standard perspective homography
   - **Piranesi** (right) — non-linear exponential perspective
5. Click **Apply** to warp the layer in place (recorded as a single undo step),
   or **Cancel** to close without changes.
