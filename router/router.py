from __future__ import print_function

import heapq
from copy import deepcopy
from collections import defaultdict
import random

import numpy as np
from scipy.spatial.distance import cityblock

from util.blocks import block_names

class Router:
    def __init__(self, blif, pregenerated_cells):
        self.blif = blif
        self.pregenerated_cells = pregenerated_cells

    def extract_pin_locations(self, placements):
        net_pins = defaultdict(list)

        # For each wire, locate its pins according to the placement
        for placement in placements:
            # Do the cell lookup
            rotation = placement["turns"]
            cell_name = placement["name"]
            cell = self.pregenerated_cells[cell_name][rotation]

            yy, zz, xx = placement["placement"]

            for pin, d in cell.ports.iteritems():
                (y, z, x) = d["coordinates"]
                facing = d["facing"]
                coord = (y + yy, z + zz, x + xx)
                net_name = placement["pins"][pin]
                net_pins[net_name].append(coord)

        return net_pins
        
    def create_net_segments(self, pin_locations):

        def minimum_spanning_tree(net_pins):
            """
            For a given set of (y, z, x) coordinates given by net_pins, compute
            the segments that form the minimum spanning tree, using Kruskal's
            algorithm.
            """

            # Create sets for each of the pins
            sets = []
            for pin in net_pins:
                s = set([pin])
                sets.append(s)

            # Compute the weight matrix
            weights = {}
            for pin in net_pins:
                for pin2 in net_pins:
                    if pin == pin2:
                        continue
                    # Weight each pin distance based on the Manhattan
                    # distance.
                    weights[(pin, pin2)] = cityblock(pin, pin2)

            def find_set(u):
                for i, s in enumerate(sets):
                    if u in s:
                        return i
                return -1

            # Create spanning tree!
            A = set()
            for u, v in sorted(weights, key=weights.get):
                u_i = find_set(u)
                v_i = find_set(v)
                if u_i != v_i:
                    A.add((u, v))
                    # Union the two sets
                    sets[v_i] |= sets[u_i]
                    sets.pop(u_i)

            return A

        def extend_pin(coord, facing):
            """
            Returns the coordinates of the pin based moving in the
            direction given by "facing".
            """
            (y, z, x) = coord

            if facing == "north":
                z -= 1
            elif facing == "west":
                x -= 1
            elif facing == "south":
                z += 1
            elif facing == "east":
                x += 1

            return (y, z, x)

        net_segments = {}
        for net, pin_list in pin_locations.iteritems():
            if len(pin_list) < 2:
                continue
            net_segments[net] = minimum_spanning_tree(pin_list)

        return net_segments

    def dumb_route(self, a, b):
        """
        Routes, on one Y layer only, the path between a and b, going
        east/west, then north/south, ignorant of all intervening objects.
        """
        ay, az, ax = a
        by, bz, bx = b

        net = []

        # Move horizontally from a to b
        start, stop = min(ax, bx), max(ax, bx) + 1
        for x in xrange(start, stop):
            coord = (ay, az, x)
            net.append(coord)

        # Move vertically from bx to b
        start, stop = min(az, bz), max(az, bz) + 1
        for z in xrange(start, stop):
            coord = (ay, z, bx)
            net.append(coord)

        return net

    def net_to_wire_and_violation(self, net, dimensions, pins):
        """
        Converts a realized net, which is a list of block positions from
        one pin to another, into two matrices:
        - wire: the redstone + stone block
        - violation: the places where this redstone may possibly transmit

        The net is the list of where the _redstone_ is.
        """
        wire = np.zeros(dimensions, dtype=np.int8)
        violation = np.zeros(dimensions, dtype=np.bool)

        redstone = block_names.index("redstone_wire")
        stone = block_names.index("stone")

        violation_directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]

        for coord in net:
            y, z, x = coord
            # Generate the wire itself
            wire[y, z, x] = redstone
            wire[y - 1, z, x] = stone

            # Generate the violation matrix, unless it's a pin
            if coord not in pins:
                for vy in [0, -1]:
                    for vz, vx in violation_directions:
                        try:
                            violation[y + vy, z + vz, x + vx] = True
                        except IndexError:
                            pass

        # Remove "wire" from the violation matrix so that it doesn't
        # violate itself
        for y, z, x in net:
            violation[y, z, x] = False
            violation[y - 1, z, x] = False

        return wire, violation

    def compute_net_violations(self, violation, occupieds):
        """
        For each non-zero entry, see if there is anything in "occupieds".
        """
        violations = np.logical_and(violation, occupieds)
        return sum(violations.flat)

    def initial_routing(self, placements, layout_dimensions):
        """
        For all nets, produce a dumb initial routing.

        The returned routing dictionary is of the structure:
        { net name:
            { pins: [(y, z, x) tuples]
              segments: [
                { pins: [(ay, az, ax), (by, bz, bx)],
                  net: [path of redstone],
                  wire: [path of redstone and blocks],
                  violation: [violation matrix]
                }
              ]
            }
        }
        """
        routings = {}

        pin_locations = self.extract_pin_locations(placements)
        net_segments = self.create_net_segments(pin_locations)

        for net_name, segment_endpoints in net_segments.iteritems():
            segments = []
            for a, b in segment_endpoints:
                net = self.dumb_route(a, b)
                w, v = self.net_to_wire_and_violation(net, layout_dimensions, [a, b])
                segment = {"pins": [a, b], "net": net, "wire": w, "violation": v}
                segments.append(segment)

            net_pins = pin_locations[net_name]
            routings[net_name] = {"pins": net_pins, "segments": segments}

        return routings

    def generate_usage_matrix(self, placed_layout, routing, exclude=[]):
        usage_matrix = np.copy(placed_layout)
        for net_name, d in routing.iteritems():
            for i, segment in enumerate(d["segments"]):
                if (net_name, i) in exclude:
                    continue
                else:
                    usage_matrix = np.logical_or(usage_matrix, segment["wire"])

        return usage_matrix

    def score_routing(self, routing, layout, usage_matrix):
        """
        For the given layout, and the routing, produce the score of the
        routing.

        The score is composed of its constituent nets' scores, and the
        score of each net is based on the number of violations it has,
        the number of vias and pins and the ratio of its actual length
        and the lower bound on its length.

        layout is the 3D matrix produced by the placer.
        """
        alpha = 3
        beta = 0.1
        gamma = 1

        net_scores = {}
        net_num_violations = {}

        # Score each net segment in the entire net
        for net_name, d in routing.iteritems():
            net_scores[net_name] = []
            net_num_violations[net_name] = []

            for i, segment in enumerate(d["segments"]):
                routed_net = segment["net"]

                # Violations
                violation_matrix = segment["violation"]
                violations = self.compute_net_violations(violation_matrix, usage_matrix)
                net_num_violations[net_name].append(violations)

                # Number of vias and pins
                vias = 0
                num_pins = 2
                pins_vias = vias - num_pins

                # Lower length bound
                lower_length_bound = max(1, cityblock(segment["pins"][0], segment["pins"][1]))
                length_ratio = len(routed_net) / lower_length_bound

                score = (alpha * violations) + (beta * pins_vias) + (gamma * length_ratio)

                net_scores[net_name].append(score)

        # print(routing)
        # print(net_scores)
        return net_scores, net_num_violations

    def normalize_net_scores(self, net_scores, norm_margin=0.1):
        """
        Normalize scores to [norm_margin, 1-norm_margin].
        """
        scores = sum(net_scores.itervalues(), [])
        min_score, max_score = min(scores), max(scores)
        norm_range = 1.0 - 2*norm_margin
        scale = norm_range / (max_score - min_score)

        normalized_scores = {}
        for net_name, scores in net_scores.iteritems():
            new_net_scores = [norm_margin + score * scale for score in scores]
            normalized_scores[net_name] = new_net_scores

        return normalized_scores

    def natural_selection(self, net_scores):
        """
        natural_selection() selects which nets and net segments to rip up
        and replace. It returns a list of (net name, index) tuples, in
        which the index represents the net to replace.
        """
        rip_up = []
        for net_name, norm_scores in net_scores.iteritems():
            for i, norm_score in enumerate(norm_scores):
                x = random.random()
                if x < norm_score:
                    rip_up.append((net_name, i))

        return rip_up

    def maze_route(self, a, b, placed_layout, usage_matrix):
        """
        Given two pins to re-route, find the best path using Lee's maze
        routing algorithm.
        """
        cost_matrix = np.full_like(placed_layout, -1, dtype=np.int)
        backtrace_matrix = np.zeros_like(cost_matrix, dtype=np.int)

        def in_bounds(coord, layout_dimensions):
            y, z, x = coord
            height, width, length = layout_dimensions
            if 0 <= y < height and 0 <= z < width and 0 <= x < length:
                return True
            return False

        def violating(coord):
            if coord in [a, b]:
                return False

            violation_directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            for dy in [0, -1]:
                for dz, dx in violation_directions:
                    y, z, x = coord
                    new_coord = (y + dy, z + dz, x + dx)
                    if not in_bounds(new_coord, placed_layout.shape):
                        continue

                    if new_coord in [a, b]:
                        continue

                    if usage_matrix[new_coord]:
                        return True

            return False

        # Possible list of movements
        EAST = 1
        MOVE_EAST = (0, 0, 1)
        NORTH = 2
        MOVE_NORTH = (0, 1, 0)
        WEST = 3
        MOVE_WEST = (0, 0, -1)
        SOUTH = 4
        MOVE_SOUTH = (0, -1, 0)
        UP = 5
        MOVE_UP = (3, 0, 0)
        DOWN = 6
        MOVE_DOWN = (-3, 0, 0)

        # Backtrace is the way you go from the start.
        movements = [MOVE_EAST, MOVE_NORTH, MOVE_WEST, MOVE_SOUTH, MOVE_UP, MOVE_DOWN]
        backtraces = [WEST, SOUTH, EAST, NORTH, DOWN, UP]
        costs = [1, 1, 1, 1, 3, 3]

        violation_cost = 1000

        # Start breadth-first with a
        visited = set()
        min_dist_heap = []
        cost_matrix[a] = 0
        heapq.heappush(min_dist_heap, (0, a))
        visited_size = len(visited)

        while len(min_dist_heap) > 0:
            # print("{} -> {}".format(len(to_visit), len(visited)))
            _, location = heapq.heappop(min_dist_heap)
            visited.add(location)

            # For each candidate movement
            for movement, backtrace, movement_cost in zip(movements, backtraces, costs):
                dy, dz, dx = movement
                y, z, x = location
                new_location = (y + dy, z + dz, x + dx)

                if not in_bounds(new_location, placed_layout.shape):
                    continue

                if new_location in visited:
                    continue

                if violating(new_location):
                    new_location_cost = cost_matrix[location] + violation_cost
                else:
                    new_location_cost = cost_matrix[location] + movement_cost

                # print(location, cost_matrix[location], "->", new_location, cost_matrix[new_location], new_location_cost)
                if cost_matrix[new_location] == -1 or new_location_cost < cost_matrix[new_location]:
                    cost_matrix[new_location] = new_location_cost
                    backtrace_matrix[new_location] = backtrace

                if new_location not in [entry[1] for entry in min_dist_heap]:
                    heapq.heappush(min_dist_heap, (new_location_cost, new_location))


        # Backtrace, if a path found
        backtrace_movements = [MOVE_WEST, MOVE_SOUTH, MOVE_EAST, MOVE_NORTH, MOVE_DOWN, MOVE_UP]
        if b in visited:
            net = [b]
            while net[-1] != a:
                movement = backtrace_movements[backtraces.index(backtrace_matrix[net[-1]])]
                dy, dz, dx = movement
                y, z, x = net[-1]
                back_location = (y + dy, z + dz, x + dx)
                net.append(back_location)

            print("Net score:", cost_matrix[b], " Length:", len(net))
            return net
        else:
            print("No path between {} and {} found!".format(a, b))
            # print(cost_matrix[1])
            # print(backtrace_matrix[1])
            return None

    def re_route(self, initial_routing, placed_layout):
        """
        re_route() produces new routings until there are no more net
        violations that cause the routing to be infeasible.
        """
        usage_matrix = self.generate_usage_matrix(placed_layout, initial_routing)

        # Score the initial routing
        net_scores, net_violations = self.score_routing(initial_routing, placed_layout, usage_matrix)
        num_violations = sum(sum(net_violations.itervalues(), []))
        iterations = 0

        routing = deepcopy(initial_routing)

        try:
            while num_violations > 0:
                print("Iteration:", iterations, " Violations:", num_violations)

                # Normalize net scores
                normalized_scores = self.normalize_net_scores(net_scores)

                # Select nets to rip-up and re-route
                rip_up = self.natural_selection(normalized_scores)

                # Re-route these nets
                usage_matrix = self.generate_usage_matrix(placed_layout, routing, exclude=rip_up)

                print("Re-routing", len(rip_up), "nets")
                for net_name, i in sorted(rip_up, key=lambda x: normalized_scores[x[0]][x[1]], reverse=True):
                    a, b = routing[net_name]["segments"][i]["pins"]
                    new_net = self.maze_route(a, b, placed_layout, usage_matrix)
                    routing[net_name]["segments"][i]["net"] = new_net

                    w, v = self.net_to_wire_and_violation(new_net, placed_layout.shape, [a, b])
                    routing[net_name]["segments"][i]["wire"] = w
                    routing[net_name]["segments"][i]["violation"] = v

                    # Re-add this net to the usage matrix
                    usage_matrix = np.logical_or(usage_matrix, w)

                # Re-score this net
                net_scores, net_violations = self.score_routing(routing, placed_layout, usage_matrix)
                num_violations = sum(sum(net_violations.itervalues(), []))
                iterations += 1
                print()
        except KeyboardInterrupt:
            pass

        return routing

    def serialize_routing(self, original_routing, shape, f):
        """
        Return the routing without the wire or the violation matrices
        (which can't be serialized as-is and takes too much space
        anyway).
        """
        import json
        routing = deepcopy(original_routing)
        for net_name, net in routing.iteritems():
            for i, segment in enumerate(net["segments"]):
                del segment["violation"]
                del segment["wire"]

        json.dump(routing, f)
        f.write("\n")
        json.dump(shape, f)

    def deserialize_routing(self, f):
        import json
        routing = json.loads(f.readline())
        shape = json.loads(f.readline())
        for net_name, net in routing.iteritems():
            for i, segment in enumerate(net["segments"]):
                a, b = segment["pins"]
                n = segment["net"]
                w, v = self.net_to_wire_and_violation(n, shape, [a, b])
                segment["wire" ] = w
                segment["violation"] = v

        return routing

    def extract(self, routing, placed_layout):
        """
        Place the wires and vias specified by routing.
        """
        routed_layout = np.copy(placed_layout)
        for net_name, d in routing.iteritems():
            for segment in d["segments"]:
                for y, z, x in segment["net"]:
                    # Place redstone
                    routed_layout[y, z, x] = 55

                    # Place material underneath
                    if y == 4:
                        routed_layout[y-1, z, x] = 5
                    elif y == 1:
                        routed_layout[y-1, z, x] = 1

        return routed_layout
