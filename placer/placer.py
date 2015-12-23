from __future__ import print_function

import random
import numpy as np

from collections import defaultdict
from copy import deepcopy

from util.cell import from_lib

def pregenerate_cells(blif, cell_library):
    """
    Generates each cell and each of its four (yaw) rotations.

    The returned dictionary is indexed by the cell name (e.g., "AND")
    and then by the rotation (0-3).
    """
    cells = {}
    for cell_name, cell_data in cell_library.cells.iteritems():
        # Generate first cell
        cell_rot0 = from_lib(cell_name, cell_data)

        # Generate all four rotations
        cell_rot1 = cell_rot0.rot90()
        cell_rot2 = cell_rot1.rot90()
        cell_rot3 = cell_rot2.rot90()

        cells[cell_name] = [cell_rot0, cell_rot1, cell_rot2, cell_rot3]

    return cells

def initial_placement(blif, pregenerated_cells, dimensions=None):
    """
    Generate an initial stupid placement of cells.
    If dimensions is not specified, then make an educated guess based
    on the cells used.

    The returned dictionary is a list of cells and their placements:
    [ { "name": "AND",
        "placement": (y, z, x),
        "turns": 0 | 1 | 2 | 3,
        "pins": {"A": "rst", ... }
      },

      ...
    ]
    """
    spacing = 1

    # Get the subcircuits from the BLIF
    blif_cells = blif.cells

    # Convert them to references to cells in the cell library, using the
    # first rotation
    cells = [pregenerated_cells[bc["name"]][0] for bc in blif_cells]

    if dimensions is None:
        max_height = max(cell.blocks.shape[0] for cell in cells)

        # Estimate the width by taking the maximum of X or Z of all cells
        # used in the layout
        max_cell_widths = [max(cell.blocks.shape[1], cell.blocks.shape[2]) for cell in cells]

        # Add up the longest dimension, plus one for each cell
        width_estimate = sum(max_cell_widths) + (len(max_cell_widths) * spacing)

        dimensions = (max_height, width_estimate, width_estimate)

        print("Estimating dimensions to be {}".format(dimensions))
    else:
        if len(dimensions) != 3:
            raise ValueError("Dimensions ({}) is not a tuple of length 3".format(dimensions))

    # Lay them out on a line (in the X dimension) with spacing
    anchor = [0, 0, 0]
    x = anchor[2]

    placements = []

    for cell, blif_cell in zip(cells, blif_cells): 
        # Copy the cell anchor except the X dimension
        cell_anchor = [anchor[0], anchor[1], x]

        placement = {"name": cell.name,
                     "placement": cell_anchor,
                     "turns": 0,
                     "pins": blif_cell["pins"]}

        placements.append(placement)

        x += cell.blocks.shape[2] + spacing

    # print(placements)

    return placements, dimensions

def estimate_wire_lengths(blif, pregenerated_cells, placements):
    """
    Given the cells and their placements, determine the estimated wire
    lengths of all nets used by these cells.

    The estimate is the "half-perimeter of the bounding box of the net."
    """

    net_pins = defaultdict(list)

    # For each wire, locate its pins according to the placement
    for blif_cell, placement in zip(blif.cells, placements):
        # Do the cell lookup
        rotation = placement["turns"]
        cell_name = placement["name"]
        cell = pregenerated_cells[cell_name][rotation]

        yy, zz, xx = placement["placement"]

        for y in xrange(cell.ports.shape[0]):
            for z in xrange(cell.ports.shape[1]):
                for x in xrange(cell.ports.shape[2]):
                    port_name = cell.ports[y, z, x]
                    if port_name:
                        net_name = placement["pins"][port_name]
                        coord = (y + yy, z + zz, x + xx)
                        net_pins[net_name].append(coord)

    net_lengths = {}

    # Figure the point-to-point of these pins' locations
    for net, pins in net_pins.iteritems():
        dy = max(c[0] for c in pins) - min(c[0] for c in pins)
        dz = max(c[1] for c in pins) - min(c[1] for c in pins)
        dx = max(c[2] for c in pins) - min(c[2] for c in pins)

        net_lengths[net] = dy + dz + dx

    # print(net_lengths)

    return net_lengths

def compute_occupied_locations(blif, pregenerated_cells, placements, dimensions):

    grid = np.zeros(dimensions, dtype=np.int32)

    for placement in placements:
        # Do the cell lookup
        rotation = placement["turns"]
        cell_name = placement["name"]
        cell = pregenerated_cells[cell_name][rotation]

        yy, zz, xx = placement["placement"]

        for y in xrange(cell.blocks.shape[0]):
            for z in xrange(cell.blocks.shape[1]):
                for x in xrange(cell.blocks.shape[2]):
                    grid[yy + y, zz + z, xx + x] += 1

    # print(grid)

    return grid

def compute_overlap_penalty(grid):
    """
    Given a grid that trocks the number of cells that occupy a given
    coordinate, compute a penalty.

    Obviously, locations with no cells or one cell are not penalized.
    However, if there is more than one cell, penalize by the amount in
    excess of one cell.
    """

    penalty = 0
    for y in xrange(grid.shape[0]):
        for z in xrange(grid.shape[1]):
            for x in xrange(grid.shape[2]):
                v = grid[y, z, x]
                if v > 1:
                    penalty += (v - 1)

    return penalty

def generate(old_placements, method="displace", displace_interchange_ratio=5):
    """
    Given an old placement, generate a new placement by either switching
    the location of two cells or displacing a cell or rotating it.

    method can be "displace" or "reorient".

    displace_interchange_ratio is the ratio of how often you displace
    a cell and how often you interchange it with another cell.
    """
    new_placements = deepcopy(old_placements)

    cellA = random.choice(new_placements)

    interchange = random.random() > (1. / displace_interchange_ratio)
    if interchange:
        cellB = cellA
        while cellB is cellA:
            cellB = random.choice(new_placements)

        print("Interchanging {} (at {}) with {} (at {})".format(cellA["name"], cellA["placement"], cellB["name"], cellB["placement"]))
        cellA["placement"], cellB["placement"] = cellB["placement"], cellA["placement"]
    else: # displace or reorient
        if method == "displace":
            pass
        elif method == "interchange":
            pass
        else:
            raise ValueError("Method must be 'displace' or 'reorient'")

    return new_placements

def score(blif, cells, placements, dimensions):
    estimated_net_lengths = estimate_wire_lengths(blif, cells, placements)
    wire_length_penalty = sum(estimated_net_lengths.values())

    occupied = compute_occupied_locations(blif, cells, placements, dimensions)
    overlap_penalty = compute_overlap_penalty(occupied)

    return wire_length_penalty + overlap_penalty

def create_layout(dimensions, placements, pregenerated_cells):

    grid = np.zeros(dimensions, dtype=np.int8)

    for placement in placements:
        # Do the cell lookup
        rotation = placement["turns"]
        cell_name = placement["name"]
        cell = pregenerated_cells[cell_name][rotation]

        yy, zz, xx = placement["placement"]

        for y in xrange(cell.blocks.shape[0]):
            for z in xrange(cell.blocks.shape[1]):
                for x in xrange(cell.blocks.shape[2]):
                    blockid = cell.blocks[y, z, x]
                    grid[yy + y, zz + z, xx + x] = blockid

    return grid

def shrink_layout(layout):
    """
    Deterimines the smallest 3D array that fits the layout and
    creates a new layout to fit it.
    """
    min_y, min_z, min_x = layout.shape
    max_y, max_z, max_x = [0, 0, 0]

    for y in xrange(layout.shape[0]):
        for z in xrange(layout.shape[1]):
            for x in xrange(layout.shape[2]):
                blockid = layout[y, z, x]
                if blockid > 0:
                    min_y = min(min_y, y)
                    min_z = min(min_z, z)
                    min_x = min(min_x, x)
                    max_y = max(max_y, y)
                    max_z = max(max_z, z)
                    max_x = max(max_x, x)

    dy = max_y - min_y + 1
    dz = max_z - min_z + 1
    dx = max_x - min_x + 1

    shrunk_layout = np.zeros((dy, dz,dx), dtype=np.int8)

    for y in xrange(shrunk_layout.shape[0]):
        for z in xrange(shrunk_layout.shape[1]):
            for x in xrange(shrunk_layout.shape[2]):
                shrunk_layout[y, z, x] = layout[min_y + y, min_z + z, min_x + x]

    return shrunk_layout