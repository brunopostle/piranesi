#!/usr/bin/env python3
import re
import sys
import math
import argparse
from PIL import Image, ImageChops, ImageDraw

# This script performs Piranesi's perspective transformation of images using
# quadrilateral mapping. This mapping is not the same as conventional affine
# perspective transform. It reads an image from an input file and outputs a
# transformed image to an output file where the image is mapped to a quadrilateral
# defined by four points.
#
# https://medium.com/@brunopostle/piranesis-perspective-trick-6bcd7a754da9
#
# (C) 2025 Bruno Postle <bruno@postle.net>
#
# Piranesi is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Piranesi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.


def main():
    # Default values
    width = 1600
    height = 1600
    x1, y1 = 80, 990  # bottom-left
    x2, y2 = 1400, 1350  # bottom-right
    x3, y3 = 1560, 250  # top-right
    x4, y4 = 40, 510  # top-left

    # Parse command line options
    parser = argparse.ArgumentParser(description="Perspective transformation")
    parser.add_argument("--width", type=int, default=width, help="Output width")
    parser.add_argument("--height", type=int, default=height, help="Output height")
    parser.add_argument("--x1", type=int, default=x1, help="Bottom-left X coordinate")
    parser.add_argument("--y1", type=int, default=y1, help="Bottom-left Y coordinate")
    parser.add_argument("--x2", type=int, default=x2, help="Bottom-right X coordinate")
    parser.add_argument("--y2", type=int, default=y2, help="Bottom-right Y coordinate")
    parser.add_argument("--x3", type=int, default=x3, help="Top-right X coordinate")
    parser.add_argument("--y3", type=int, default=y3, help="Top-right Y coordinate")
    parser.add_argument("--x4", type=int, default=x4, help="Top-left X coordinate")
    parser.add_argument("--y4", type=int, default=y4, help="Top-left Y coordinate")
    parser.add_argument("input", help="Input image filename")
    parser.add_argument("output", help="Output image filename")
    args = parser.parse_args()

    # Define the four corners of the distorted quadrilateral
    a = [args.x1, args.y1]
    b = [args.x2, args.y2]
    c = [args.x3, args.y3]
    d = [args.x4, args.y4]

    quad_points = [a, b, c, d]

    # Calculate line equations for each side of the quadrilateral
    line_ab = points_2line(a, b)  # Top edge
    line_bc = points_2line(b, c)  # Right edge
    line_cd = points_2line(c, d)  # Bottom edge
    line_da = points_2line(d, a)  # Left edge

    # Find vanishing points by calculating line intersections
    point_cd = line_intersection(line_bc, line_da)  # Right-left intersection
    point_da = line_intersection(line_ab, line_cd)  # Top-bottom intersection

    # Calculate scale factors for each edge using the ratio of distances
    # These values are used in the transformation to maintain perspective
    k_ab = distance_2d(point_da, b) / distance_2d(point_da, a)
    k_bc = distance_2d(point_cd, b) / distance_2d(point_cd, c)
    k_cd = distance_2d(point_da, c) / distance_2d(point_da, d)
    k_da = distance_2d(point_cd, a) / distance_2d(point_cd, d)

    # Create a function to calculate forward mapping using closures for context
    def _forward(x, y):
        # Calculate relative positions along each edge using scale factors
        x_ab = ((k_ab ** x) - 1) / (k_ab - 1)  # Top edge position
        x_cd = ((k_cd ** x) - 1) / (k_cd - 1)  # Bottom edge position
        y_bc = ((k_bc ** y) - 1) / (k_bc - 1)  # Right edge position
        y_da = ((k_da ** y) - 1) / (k_da - 1)  # Left edge position

        # Calculate edge vectors
        vec_ab = subtract_2d(b, a)  # Top edge vector
        vec_cd = subtract_2d(c, d)  # Bottom edge vector
        vec_bc = subtract_2d(b, c)  # Right edge vector
        vec_da = subtract_2d(a, d)  # Left edge vector

        # Calculate points along each edge based on the relative positions
        point_x_ab = add_2d(a, scale_2d(vec_ab, x_ab))  # Point on top edge
        point_x_cd = add_2d(d, scale_2d(vec_cd, x_cd))  # Point on bottom edge
        point_y_bc = add_2d(c, scale_2d(vec_bc, y_bc))  # Point on right edge
        point_y_da = add_2d(d, scale_2d(vec_da, y_da))  # Point on left edge

        # Create lines connecting the points
        line_x = points_2line(point_x_ab, point_x_cd)  # Vertical line at x position
        line_y = points_2line(point_y_bc, point_y_da)  # Horizontal line at y position

        # Find the intersection of the two lines - this is our transformed point
        coor = line_intersection(line_x, line_y)

        return coor[0], coor[1]

    # Create a function to calculate reverse mapping using closures
    def _reverse(x_r, y_r):
        # Convert from pixel coordinates to normalized coordinates (0-1)
        x_r_norm = x_r / args.width
        y_r_norm = y_r / args.height

        x, y = 0.5, 0.5  # Initial guess for source coordinates
        step = 0.001  # Step size for numerical approximation

        last_x, last_y = x, y

        # Run up to 10 iterations to refine the approximation
        for _ in range(11):
            # Get destination coordinates using current guess
            x_r0, y_r0 = _forward(x, y)

            # Convert to normalized coordinates for comparison
            x_r0_norm = x_r0 / args.width
            y_r0_norm = y_r0 / args.height

            # Calculate approximate partial derivatives
            x_r1, y_r1 = _forward(x + step, y)
            x_r2, y_r2 = _forward(x, y + step)

            # Convert to normalized coordinates
            x_r1_norm = x_r1 / args.width
            y_r1_norm = y_r1 / args.height
            x_r2_norm = x_r2 / args.width
            y_r2_norm = y_r2 / args.height

            # Avoid division by zero
            if abs(x_r1_norm - x_r0_norm) < 1e-10 or abs(y_r2_norm - y_r0_norm) < 1e-10:
                return x, y

            # Calculate step multipliers based on difference from target
            x_mult = (x_r_norm - x_r0_norm) / (x_r1_norm - x_r0_norm)
            y_mult = (y_r_norm - y_r0_norm) / (y_r2_norm - y_r0_norm)

            # Update guess with scaled step
            x = x + (x_mult * step / 2)
            y = y + (y_mult * step / 2)

            # Clamp values to valid range (0-1)
            x = max(0, min(1, x))
            y = max(0, min(1, y))

            # Stop if convergence is reached
            if (abs(x - last_x) < 0.0000001) and (abs(y - last_y) < 0.0000001):
                break

            # Save current values for next iteration
            last_x, last_y = x, y

        return x, y

    # Read input image
    input_image = Image.open(args.input)
    size_x, size_y = input_image.size

    # Convert to RGBA if not already
    if input_image.mode != "RGBA":
        input_image = input_image.convert("RGBA")

    # Generate and write output image
    output_image = transform_image(
        args.width, args.height, size_x, size_y, input_image, _reverse, quad_points
    )

    # convert to RBG if necessary
    no_alpha_pattern = re.compile(
        r".*\.(jpe?g|bmp|pcx|p[bgp]m|eps|pdf|dcx|jfif)$", re.IGNORECASE
    )
    if no_alpha_pattern.match(args.output):
        output_image = output_image.convert("RGB")

    output_image.save(args.output)


# Image I/O Functions (using PIL)


def transform_image(width, height, size_x, size_y, input_image, reverse_func, quad_points):
    # Build a 64x64 grid of precalculated source coordinates
    grid_size = 64
    grid = [[None for _ in range(grid_size + 1)] for _ in range(grid_size + 1)]

    for grid_y in range(grid_size + 1):
        for grid_x in range(grid_size + 1):
            x_pix = int(grid_x * (width - 1) / grid_size)
            y_pix = int(grid_y * (height - 1) / grid_size)
            src_x, src_y = reverse_func(x_pix, y_pix)
            grid[grid_y][grid_x] = (src_x * size_x, src_y * size_y)

    # Build PIL mesh: each grid cell maps a destination rectangle to a source quad.
    # PIL MESH source quad corner order: upper-left, lower-left, lower-right, upper-right.
    mesh_data = []
    for gy in range(grid_size):
        for gx in range(grid_size):
            x0 = gx * width // grid_size
            y0 = gy * height // grid_size
            x1 = (gx + 1) * width // grid_size
            y1 = (gy + 1) * height // grid_size
            ul = grid[gy][gx]
            ll = grid[gy + 1][gx]
            lr = grid[gy + 1][gx + 1]
            ur = grid[gy][gx + 1]
            mesh_data.append((
                (x0, y0, x1, y1),
                (ul[0], ul[1], ll[0], ll[1], lr[0], lr[1], ur[0], ur[1]),
            ))

    warped = input_image.transform(
        (width, height), Image.Transform.MESH, mesh_data, Image.Resampling.BICUBIC
    )

    # Build an anti-aliased quad mask: draw at 4x resolution then Lanczos-downscale.
    scale = 4
    mask_big = Image.new("L", (width * scale, height * scale), 0)
    ImageDraw.Draw(mask_big).polygon(
        [(p[0] * scale, p[1] * scale) for p in quad_points], fill=255
    )
    mask = mask_big.resize((width, height), Image.Resampling.LANCZOS)

    r, g, b, a = warped.split()
    return Image.merge("RGBA", (r, g, b, ImageChops.multiply(a, mask)))


# Geometric utility functions

# Create a line equation (y = ax + b) from two points
def points_2line(coor_0, coor_1):
    # Calculate direction vector
    vector = subtract_2d(coor_1, coor_0)

    # Avoid division by zero by setting a very small value
    if not vector[0]:
        vector[0] = 0.00000000001

    # Calculate slope (a) and y-intercept (b)
    a = vector[1] / vector[0]
    b = coor_0[1] - (coor_0[0] * a)

    return {"a": a, "b": b}


# Find intersection of two lines given by their equations
def line_intersection(line_0, line_1):
    # Handle parallel lines (return center point as fallback)
    if line_0["a"] == line_1["a"]:
        return [0.5, 0.5]

    # Calculate intersection point
    x = (line_1["b"] - line_0["b"]) / (line_0["a"] - line_1["a"])
    y = (line_0["a"] * x) + line_0["b"]

    return [x, y]


# Subtract two 2D vectors
def subtract_2d(a, b):
    return [a[0] - b[0], a[1] - b[1]]


# Add two 2D vectors
def add_2d(a, b):
    return [a[0] + b[0], a[1] + b[1]]


# Calculate Euclidean distance between two 2D points
def distance_2d(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


# Scale a 2D vector by a factor
def scale_2d(vector, factor):
    return [vector[0] * factor, vector[1] * factor]


if __name__ == "__main__":
    main()
