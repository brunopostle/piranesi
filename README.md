# PIRANESI'S PERSPECTIVE TRICK

## NAME

piranesi.py - Transform images using Piranesi's perspective quadrilateral mapping

## SYNOPSIS

```
piranesi.py [options] input output
```

## DESCRIPTION

**piranesi.py** performs Piranesi's perspective transformation on images by mapping the image to a quadrilateral defined by four points. This transformation differs from conventional affine perspective transforms, following techniques used by the 18th-century artist Giovanni Battista Piranesi to create dramatic visual effects.

The script reads an image from an input file and outputs a transformed image where the original image is mapped to a quadrilateral defined by the four corner points.

## OPTIONS

`--width WIDTH`
  Output image width in pixels (default: 1600)

`--height HEIGHT`
  Output image height in pixels (default: 1600)

`--x1 X1`
  Bottom-left X coordinate in the output image (default: 80)

`--y1 Y1`
  Bottom-left Y coordinate in the output image  (default: 990)

`--x2 X2`
  Bottom-right X coordinate in the output image  (default: 1400)

`--y2 Y2`
  Bottom-right Y coordinate in the output image  (default: 1350)

`--x3 X3`
  Top-right X coordinate in the output image  (default: 1560)

`--y3 Y3`
  Top-right Y coordinate in the output image  (default: 250)

`--x4 X4`
  Top-left X coordinate in the output image  (default: 40)

`--y4 Y4`
  Top-left Y coordinate in the output image  (default: 510)

## ARGUMENTS

`input`
  Input image filename. Supports common image formats including PNG, JPEG, TIFF, etc.

`output`
  Output image filename. The format is determined by the file extension.

## EXAMPLES

Transform an image using default quadrilateral coordinates:
```
piranesi.py input.png output.png
```

Specify custom quadrilateral coordinates:
```
piranesi.py --x1 100 --y1 800 --x2 1200 --y2 900 --x3 1300 --y3 300 --x4 200 --y4 200 input.jpg output.jpg
```

Change output dimensions:
```
piranesi.py --width 2000 --height 2000 input.png output.png
```

![Input image](main-street.webp)

![Output image](main-street-piranesi.webp)

## ALGORITHM

The transformation uses a non-linear quadrilateral mapping that:

1. Calculates line equations for each side of the quadrilateral
2. Finds vanishing points through line intersections
3. Calculates scale factors using distance ratios between points
4. Maps source coordinates to destination coordinates using these factors
5. Uses bilinear interpolation for smooth pixel transitions

For reverse mapping (to find source pixel coordinates from destination coordinates), the script uses an iterative numerical approximation.

## NOTES

- Alpha channel is preserved when present in the input image
- Areas outside the defined quadrilateral are rendered transparent
- If the output format doesn't support alpha (e.g., JPEG), the image is converted to RGB
- A 64x64 grid is used for precalculated source coordinates to optimize performance

## AUTHOR

(C) 2025 Bruno Postle <bruno@postle.net>

## LICENSE

GNU General Public License version 3 or later.

## SEE ALSO

For more information about Piranesi's perspective technique:

https://github.com/brunopostle/piranesi/blob/main/doc/README.md

Original link: https://medium.com/@brunopostle/piranesis-perspective-trick-6bcd7a754da9
