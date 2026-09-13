from __future__ import annotations

"""Reliable reconstruction of separate DXF LINE chains for v0.1.15.

The legacy v0.1.14 implementation stopped after a layer-wide path limit. That
made identical shapes work or fail depending on their order in the DXF. This
module builds an endpoint graph with a spatial index and enumerates paths only
around the selected annotation, so later bars are not silently skipped.
"""

from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
import hashlib
import math
from typing import Any, Sequence

PATCH_VERSION = "0.1.16"
_CACHE_LIMIT = 3

# Signature -> immutable graph topology. Primitive objects are intentionally not
# cached because find_geometry_candidates recreates them on every call.
_GRAPH_CACHE: "OrderedDict[str, tuple[tuple[tuple[int, int], ...], dict[int, tuple[int, ...]], tuple[frozenset[int], ...]]]" = OrderedDict()


@dataclass(frozen=True)
class NearGapEdge:
    """One auditable LINE edge used by the controlled one-gap fallback."""

    handle: str
    layer: str
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass(frozen=True)
class NearGapBridge:
    """The only bridge that can join two otherwise strict path components.

    The normal endpoint graph is intentionally left unchanged.  A caller may
    use this record only as review evidence; it is not an automatic approval.
    """

    edge_handles: tuple[str, str]
    points: tuple[tuple[float, float], tuple[float, float]]
    gap_raw: float
    strict_component_handles: tuple[tuple[str, ...], tuple[str, ...]]
    strict_tolerance_raw: float
    max_gap_raw: float


def audit_unique_one_gap_bridge(
    edges: Sequence[NearGapEdge],
    *,
    strict_tolerance_raw: float,
    max_gap_raw: float,
    neighbourhood_edges: Sequence[NearGapEdge] | None = None,
) -> NearGapBridge | None:
    """Return one safe near-gap bridge, otherwise ``None``.

    Safety gates:

    * every selected edge is a non-zero LINE on the same layer;
    * the strict graph has exactly two unbranched open path components;
    * exactly one pair of terminal endpoints is within the near-gap limit;
    * no omitted neighbouring edge can continue or compete at any terminal.

    This deliberately does not modify or relax the normal endpoint tolerance.
    """

    strict = max(float(strict_tolerance_raw), 1e-9)
    maximum = float(max_gap_raw)
    selected = tuple(edges)
    if (
        len(selected) < 2
        or not math.isfinite(maximum)
        or maximum <= strict
        or any(
            not edge.handle
            or not edge.layer
            or not _finite_point(edge.start)
            or not _finite_point(edge.end)
            or math.dist(edge.start, edge.end) <= 1e-9
            for edge in selected
        )
    ):
        return None

    layers = {edge.layer.casefold() for edge in selected}
    if len(layers) != 1:
        return None
    selected_keys = {_near_gap_edge_key(edge) for edge in selected}
    if len(selected_keys) != len(selected):
        return None

    topology = _near_gap_topology(selected, tolerance=strict)
    if topology is None or len(topology["components"]) != 2:
        return None

    component_terminals: list[tuple[int, ...]] = []
    component_handles: list[tuple[str, ...]] = []
    for component in topology["components"]:
        node_degrees: dict[int, int] = defaultdict(int)
        for edge_index in component:
            left_node, right_node = topology["edge_nodes"][edge_index]
            node_degrees[left_node] += 1
            node_degrees[right_node] += 1
        if any(degree > 2 for degree in node_degrees.values()):
            return None
        terminals = tuple(
            sorted(node for node, degree in node_degrees.items() if degree == 1)
        )
        if len(terminals) != 2 or any(
            degree not in {1, 2} for degree in node_degrees.values()
        ):
            return None
        component_terminals.append(terminals)
        component_handles.append(
            tuple(sorted(selected[index].handle for index in component))
        )

    bridge_options: list[
        tuple[
            float,
            int,
            int,
            tuple[float, float],
            tuple[float, float],
        ]
    ] = []
    for left_node in component_terminals[0]:
        for right_node in component_terminals[1]:
            for left_token in topology["node_tokens"][left_node]:
                for right_token in topology["node_tokens"][right_node]:
                    left_point = topology["points"][left_token]
                    right_point = topology["points"][right_token]
                    gap = math.dist(left_point, right_point)
                    if strict < gap <= maximum:
                        bridge_options.append(
                            (
                                gap,
                                left_token,
                                right_token,
                                left_point,
                                right_point,
                            )
                        )

    # Nodes normally contain one token at an open end.  Deduplicate defensively
    # before enforcing the "one and only one bridge" rule.
    unique_options: dict[tuple[int, int], tuple] = {}
    for option in bridge_options:
        key = tuple(sorted((option[1], option[2])))
        previous = unique_options.get(key)
        if previous is None or option[0] < previous[0]:
            unique_options[key] = option
    if len(unique_options) != 1:
        return None
    option = next(iter(unique_options.values()))

    # A subset of a longer chain, or a competing nearby bar end, is unsafe.
    selected_terminal_points = tuple(
        topology["points"][token]
        for terminals in component_terminals
        for node in terminals
        for token in topology["node_tokens"][node]
    )
    for neighbour in neighbourhood_edges or ():
        if neighbour.layer.casefold() not in layers:
            continue
        if _near_gap_edge_key(neighbour) in selected_keys:
            continue
        if not _finite_point(neighbour.start) or not _finite_point(neighbour.end):
            continue
        if any(
            math.dist(terminal, endpoint) <= maximum
            for terminal in selected_terminal_points
            for endpoint in (neighbour.start, neighbour.end)
        ):
            return None

    left_edge_index = option[1] // 2
    right_edge_index = option[2] // 2
    return NearGapBridge(
        edge_handles=(
            selected[left_edge_index].handle,
            selected[right_edge_index].handle,
        ),
        points=(option[3], option[4]),
        gap_raw=float(option[0]),
        strict_component_handles=(
            component_handles[0],
            component_handles[1],
        ),
        strict_tolerance_raw=strict,
        max_gap_raw=maximum,
    )


def _near_gap_topology(
    edges: Sequence[NearGapEdge],
    *,
    tolerance: float,
) -> dict[str, Any] | None:
    points = tuple(point for edge in edges for point in (edge.start, edge.end))
    parent = list(range(len(points)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            if math.dist(points[left], points[right]) <= tolerance:
                union(left, right)

    roots = sorted({find(index) for index in range(len(points))})
    root_to_node = {root: index for index, root in enumerate(roots)}
    node_tokens_mut: dict[int, list[int]] = defaultdict(list)
    for token in range(len(points)):
        node_tokens_mut[root_to_node[find(token)]].append(token)
    edge_nodes = tuple(
        (root_to_node[find(index * 2)], root_to_node[find(index * 2 + 1)])
        for index in range(len(edges))
    )
    if any(left == right for left, right in edge_nodes):
        return None

    node_edges: dict[int, set[int]] = defaultdict(set)
    for edge_index, (left, right) in enumerate(edge_nodes):
        node_edges[left].add(edge_index)
        node_edges[right].add(edge_index)
    components: list[tuple[int, ...]] = []
    remaining = set(range(len(edges)))
    while remaining:
        seed = min(remaining)
        stack = [seed]
        component: set[int] = set()
        while stack:
            edge_index = stack.pop()
            if edge_index in component:
                continue
            component.add(edge_index)
            remaining.discard(edge_index)
            for node in edge_nodes[edge_index]:
                stack.extend(node_edges[node] - component)
        components.append(tuple(sorted(component)))
    components.sort(key=lambda component: tuple(edges[index].handle for index in component))
    return {
        "points": points,
        "edge_nodes": edge_nodes,
        "node_tokens": {
            node: tuple(sorted(tokens))
            for node, tokens in node_tokens_mut.items()
        },
        "components": tuple(components),
    }


def _near_gap_edge_key(edge: NearGapEdge) -> tuple:
    return (
        edge.handle,
        edge.layer.casefold(),
        tuple(round(float(value), 9) for value in edge.start),
        tuple(round(float(value), 9) for value in edge.end),
    )


def _finite_point(point: tuple[float, float]) -> bool:
    return len(point) == 2 and all(math.isfinite(float(value)) for value in point)


class _LayerChainIndex:
    def __init__(self, primitives: Sequence[Any], tolerance: float, cell_size: float):
        self.primitives = tuple(primitives)
        self.edge_nodes, self.node_edges, self.components = _graph_for(self.primitives, tolerance)
        edge_to_component: list[int] = [-1] * len(self.primitives)
        for component_index, component in enumerate(self.components):
            for edge_index in component:
                edge_to_component[edge_index] = component_index
        self.edge_to_component = tuple(edge_to_component)
        self.cell_size = max(float(cell_size), 1e-6)
        spatial_mut: dict[tuple[int, int], set[int]] = defaultdict(set)
        for edge_index, primitive in enumerate(self.primitives):
            segments = getattr(primitive, "segments", ()) or ()
            if not segments:
                start = _point(getattr(primitive, "start", None))
                end = _point(getattr(primitive, "end", None))
                if start is None or end is None:
                    continue
                segments = ((start[0], start[1], end[0], end[1]),)
            for segment in segments:
                if not isinstance(segment, (tuple, list)) or len(segment) < 4:
                    continue
                x1, y1, x2, y2 = map(float, segment[:4])
                gx0 = math.floor(min(x1, x2) / self.cell_size)
                gx1 = math.floor(max(x1, x2) / self.cell_size)
                gy0 = math.floor(min(y1, y2) / self.cell_size)
                gy1 = math.floor(max(y1, y2) / self.cell_size)
                # Rebar lines can be long. Index every crossed cell so a text near
                # the middle of a bar still finds it without scanning the layer.
                for gx in range(gx0, gx1 + 1):
                    for gy in range(gy0, gy1 + 1):
                        spatial_mut[(gx, gy)].add(edge_index)
        self.spatial = {cell: tuple(sorted(values)) for cell, values in spatial_mut.items()}

    def nearby_edges(
        self,
        probes: Sequence[tuple[float, float]],
        radius: float,
    ) -> set[int]:
        radius = max(float(radius), 0.0)
        candidates: set[int] = set()
        span = max(1, math.ceil(radius / self.cell_size))
        for probe in probes:
            gx = math.floor(probe[0] / self.cell_size)
            gy = math.floor(probe[1] / self.cell_size)
            for dx in range(-span, span + 1):
                for dy in range(-span, span + 1):
                    candidates.update(self.spatial.get((gx + dx, gy + dy), ()))
        return {
            index
            for index in candidates
            if _primitive_distance_to_probes(self.primitives[index], probes) <= radius
        }


class LineChainIndex:
    """Reusable endpoint graph and spatial index for one imported DXF.

    Batch analysis builds this object once. Every annotation then queries only
    nearby seed lines while still expanding through the complete connected
    component. This removes the O(annotations × all-lines) graph rebuild that
    previously made the application appear frozen.
    """

    def __init__(
        self,
        primitives: Sequence[Any],
        *,
        endpoint_tolerance: float,
        spatial_cell_size: float = 100.0,
    ):
        self.endpoint_tolerance = max(float(endpoint_tolerance), 1e-9)
        by_layer: dict[str, list[Any]] = defaultdict(list)
        for primitive in primitives:
            if (
                str(getattr(primitive, "entity_type", "")).upper() == "LINE"
                and _point(getattr(primitive, "start", None)) is not None
                and _point(getattr(primitive, "end", None)) is not None
            ):
                by_layer[str(getattr(primitive, "layer", "")).casefold()].append(primitive)
        self.layers = tuple(
            _LayerChainIndex(values, self.endpoint_tolerance, spatial_cell_size)
            for _layer, values in sorted(by_layer.items())
            if len(values) >= 2
        )

    def query(
        self,
        *,
        probe_points: Sequence[tuple[float, float]],
        search_distance: float,
        max_segments: int,
        max_paths_per_component: int,
    ) -> list[tuple[Any, ...]]:
        probes = tuple(point for value in probe_points if (point := _point(value)) is not None)
        if not probes:
            return []
        radius = max(float(search_distance), 0.0)
        max_segments = max(2, min(int(max_segments), 24))
        max_paths_per_component = max(64, min(int(max_paths_per_component), 10000))
        output: list[tuple[Any, ...]] = []
        seen_global: set[tuple[str, ...]] = set()

        for layer_index in self.layers:
            seed_edges = layer_index.nearby_edges(probes, radius)
            if not seed_edges:
                continue
            component_ids = sorted(
                {
                    layer_index.edge_to_component[edge]
                    for edge in seed_edges
                    if 0 <= layer_index.edge_to_component[edge] < len(layer_index.components)
                }
            )
            for component_id in component_ids:
                component_edges = set(layer_index.components[component_id])
                if len(component_edges) < 2:
                    continue
                component_seeds = component_edges.intersection(seed_edges)
                if not component_seeds:
                    continue
                chains = _paths_containing_seeds(
                    layer_index.primitives,
                    layer_index.edge_nodes,
                    layer_index.node_edges,
                    component_edges,
                    component_seeds,
                    max_segments=max_segments,
                    max_paths=max_paths_per_component,
                )
                for chain in chains:
                    key = tuple(sorted(_handle(item) for item in chain))
                    if key in seen_global:
                        continue
                    seen_global.add(key)
                    output.append(chain)
        return output


def build_connected_chains(
    primitives: Sequence[Any],
    *,
    endpoint_tolerance: float,
    max_segments: int,
    max_paths_per_component: int,
    probe_points: Sequence[tuple[float, float]] | None = None,
    search_distance: float | None = None,
) -> list[tuple[Any, ...]]:
    """Backward-compatible one-shot chain query.

    Interactive single-position analysis can keep using this function. Batch
    analysis uses :class:`LineChainIndex` directly and reuses the index.
    """

    probes = tuple(_point(point) for point in (probe_points or ()))
    probes = tuple(point for point in probes if point is not None)
    index = LineChainIndex(
        primitives,
        endpoint_tolerance=endpoint_tolerance,
        spatial_cell_size=max(
            50.0,
            min(200.0, float(search_distance) if search_distance is not None else 100.0),
        ),
    )
    if probes and search_distance is not None:
        return index.query(
            probe_points=probes,
            search_distance=float(search_distance),
            max_segments=max_segments,
            max_paths_per_component=max_paths_per_component,
        )

    # Compatibility path used by older tests and tools: enumerate each component
    # without a spatial filter. The component cap remains local, never layer-wide.
    output: list[tuple[Any, ...]] = []
    seen_global: set[tuple[str, ...]] = set()
    for layer_index in index.layers:
        for component in layer_index.components:
            component_edges = set(component)
            if len(component_edges) < 2:
                continue
            chains = _paths_containing_seeds(
                layer_index.primitives,
                layer_index.edge_nodes,
                layer_index.node_edges,
                component_edges,
                component_edges,
                max_segments=max_segments,
                max_paths=max_paths_per_component,
            )
            for chain in chains:
                key = tuple(sorted(_handle(item) for item in chain))
                if key in seen_global:
                    continue
                seen_global.add(key)
                output.append(chain)
    return output


def clear_graph_cache() -> None:
    _GRAPH_CACHE.clear()


def _graph_for(
    primitives: Sequence[Any], tolerance: float
) -> tuple[tuple[tuple[int, int], ...], dict[int, tuple[int, ...]], tuple[frozenset[int], ...]]:
    signature = _signature(primitives, tolerance)
    cached = _GRAPH_CACHE.get(signature)
    if cached is not None:
        _GRAPH_CACHE.move_to_end(signature)
        return cached

    nodes: list[tuple[float, float]] = []
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    edge_nodes: list[tuple[int, int]] = []
    node_edges_mut: dict[int, list[int]] = defaultdict(list)
    cell = tolerance

    def node_for(point: tuple[float, float]) -> int:
        gx = math.floor(point[0] / cell)
        gy = math.floor(point[1] / cell)
        best: int | None = None
        best_distance = math.inf
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for candidate_index in grid.get((gx + dx, gy + dy), ()):
                    candidate = nodes[candidate_index]
                    distance = math.hypot(point[0] - candidate[0], point[1] - candidate[1])
                    if distance <= tolerance and distance < best_distance:
                        best = candidate_index
                        best_distance = distance
        if best is not None:
            return best
        index = len(nodes)
        nodes.append(point)
        grid[(gx, gy)].append(index)
        return index

    for edge_index, primitive in enumerate(primitives):
        start = _point(getattr(primitive, "start", None))
        end = _point(getattr(primitive, "end", None))
        if start is None or end is None:
            # build_connected_chains filters these, so this is defensive only.
            edge_nodes.append((-1, -1))
            continue
        left = node_for(start)
        right = node_for(end)
        edge_nodes.append((left, right))
        node_edges_mut[left].append(edge_index)
        node_edges_mut[right].append(edge_index)

    for values in node_edges_mut.values():
        values.sort(key=lambda edge: _primitive_sort_key(primitives[edge]))

    components: list[frozenset[int]] = []
    visited: set[int] = set()
    for start_edge in range(len(primitives)):
        if start_edge in visited:
            continue
        queue = deque([start_edge])
        component: set[int] = set()
        while queue:
            edge = queue.popleft()
            if edge in visited:
                continue
            visited.add(edge)
            component.add(edge)
            for node in edge_nodes[edge]:
                for neighbour in node_edges_mut.get(node, ()):
                    if neighbour not in visited:
                        queue.append(neighbour)
        components.append(frozenset(component))

    node_edges = {node: tuple(values) for node, values in node_edges_mut.items()}
    result = (tuple(edge_nodes), node_edges, tuple(components))
    _GRAPH_CACHE[signature] = result
    _GRAPH_CACHE.move_to_end(signature)
    while len(_GRAPH_CACHE) > _CACHE_LIMIT:
        _GRAPH_CACHE.popitem(last=False)
    return result


def _paths_containing_seeds(
    primitives: Sequence[Any],
    edge_nodes: Sequence[tuple[int, int]],
    node_edges: dict[int, tuple[int, ...]],
    component_edges: set[int],
    seed_edges: set[int],
    *,
    max_segments: int,
    max_paths: int,
) -> list[tuple[Any, ...]]:
    """Enumerate bounded simple paths by expanding from both ends of a seed."""

    output: list[tuple[Any, ...]] = []
    seen_paths: set[tuple[str, ...]] = set()
    seen_states: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()

    ordered_seeds = sorted(seed_edges, key=lambda edge: _primitive_sort_key(primitives[edge]))
    queue: deque[tuple[tuple[int, ...], tuple[int, ...], frozenset[int]]] = deque()
    for seed in ordered_seeds:
        left, right = edge_nodes[seed]
        queue.append(((seed,), (left, right), frozenset({seed})))
        # The reverse state matters when branches differ at the two ends.
        if left != right:
            queue.append(((seed,), (right, left), frozenset({seed})))

    while queue and len(output) < max_paths:
        path_edges, path_nodes, used_edges = queue.popleft()
        state_key = (path_edges, path_nodes)
        if state_key in seen_states:
            continue
        seen_states.add(state_key)

        if len(path_edges) >= 2:
            chain = tuple(primitives[index] for index in path_edges)
            key = tuple(sorted(_handle(item) for item in chain))
            if key not in seen_paths:
                seen_paths.add(key)
                output.append(chain)
                if len(output) >= max_paths:
                    break

        if len(path_edges) >= max_segments:
            continue

        left_node = path_nodes[0]
        right_node = path_nodes[-1]

        # Expand left. Keep deterministic ordering and prefer straighter
        # continuation first, which reaches normal rebar chains quickly.
        left_options = [
            edge
            for edge in node_edges.get(left_node, ())
            if edge in component_edges and edge not in used_edges
        ]
        left_options.sort(
            key=lambda edge: _continuation_key(
                primitives, edge_nodes, edge, left_node, path_nodes[1] if len(path_nodes) > 1 else None
            )
        )
        for edge in left_options:
            target = _other_node(edge_nodes[edge], left_node)
            if target is None:
                continue
            if target in path_nodes and target != right_node:
                continue
            queue.append(
                (
                    (edge,) + path_edges,
                    (target,) + path_nodes,
                    used_edges | {edge},
                )
            )

        # Expand right.
        right_options = [
            edge
            for edge in node_edges.get(right_node, ())
            if edge in component_edges and edge not in used_edges
        ]
        right_options.sort(
            key=lambda edge: _continuation_key(
                primitives, edge_nodes, edge, right_node, path_nodes[-2] if len(path_nodes) > 1 else None
            )
        )
        for edge in right_options:
            target = _other_node(edge_nodes[edge], right_node)
            if target is None:
                continue
            if target in path_nodes and target != left_node:
                continue
            queue.append(
                (
                    path_edges + (edge,),
                    path_nodes + (target,),
                    used_edges | {edge},
                )
            )

    return output


def _continuation_key(
    primitives: Sequence[Any],
    edge_nodes: Sequence[tuple[int, int]],
    edge: int,
    junction: int,
    previous_node: int | None,
) -> tuple[float, tuple[str, float, float, float, float]]:
    if previous_node is None:
        return (0.0, _primitive_sort_key(primitives[edge]))
    target = _other_node(edge_nodes[edge], junction)
    if target is None:
        return (math.inf, _primitive_sort_key(primitives[edge]))
    # Node coordinates are not stored in the cached graph. Direction ranking is
    # therefore stable by primitive metadata; correctness does not depend on it.
    return (0.0, _primitive_sort_key(primitives[edge]))


def _primitive_distance_to_probes(
    primitive: Any, probes: Sequence[tuple[float, float]]
) -> float:
    segments = getattr(primitive, "segments", ()) or ()
    if not segments:
        start = _point(getattr(primitive, "start", None))
        end = _point(getattr(primitive, "end", None))
        if start is None or end is None:
            return math.inf
        segments = ((start[0], start[1], end[0], end[1]),)
    return min(
        _distance_point_to_segment(probe, segment)
        for probe in probes
        for segment in segments
        if isinstance(segment, (tuple, list)) and len(segment) >= 4
    )


def _distance_point_to_segment(
    point: tuple[float, float], segment: Sequence[float]
) -> float:
    px, py = point
    x1, y1, x2, y2 = map(float, segment[:4])
    dx = x2 - x1
    dy = y2 - y1
    denominator = dx * dx + dy * dy
    if denominator <= 1e-18:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / denominator
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _other_node(edge_nodes: tuple[int, int], node: int) -> int | None:
    left, right = edge_nodes
    if left == node:
        return right
    if right == node:
        return left
    return None


def _point(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _handle(primitive: Any) -> str:
    handle = str(getattr(primitive, "handle", "") or "")
    if handle:
        return handle
    start = _point(getattr(primitive, "start", None)) or (0.0, 0.0)
    end = _point(getattr(primitive, "end", None)) or (0.0, 0.0)
    return f"@{start[0]:.6f},{start[1]:.6f}>{end[0]:.6f},{end[1]:.6f}"


def _primitive_sort_key(primitive: Any) -> tuple[str, float, float, float, float]:
    start = _point(getattr(primitive, "start", None)) or (0.0, 0.0)
    end = _point(getattr(primitive, "end", None)) or (0.0, 0.0)
    return (_handle(primitive), start[0], start[1], end[0], end[1])


def _signature(primitives: Sequence[Any], tolerance: float) -> str:
    digest = hashlib.blake2b(digest_size=20)
    digest.update(f"{PATCH_VERSION}|{tolerance:.12g}|{len(primitives)}".encode())
    for primitive in primitives:
        start = _point(getattr(primitive, "start", None)) or (0.0, 0.0)
        end = _point(getattr(primitive, "end", None)) or (0.0, 0.0)
        digest.update(
            (
                f"|{_handle(primitive)}|{getattr(primitive, 'layer', '')}|"
                f"{start[0]:.9f},{start[1]:.9f}|{end[0]:.9f},{end[1]:.9f}"
            ).encode("utf-8", errors="replace")
        )
    return digest.hexdigest()
