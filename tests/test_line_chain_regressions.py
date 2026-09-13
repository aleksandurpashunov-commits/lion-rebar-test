from __future__ import annotations

from dataclasses import dataclass

from lion_rebar.dxf.line_chain_v015 import LineChainIndex, build_connected_chains


@dataclass(frozen=True)
class Primitive:
    handle: str
    layer: str
    start: tuple[float, float]
    end: tuple[float, float]
    entity_type: str = "LINE"

    @property
    def segments(self):
        return ((self.start[0], self.start[1], self.end[0], self.end[1]),)


def test_three_segment_bar_is_reconstructed_regardless_of_line_direction():
    primitives = [
        Primitive("LEFT", "ARM_Bars", (0.0, 25.0), (0.0, 0.0)),
        Primitive("MAIN", "ARM_Bars", (100.0, 0.0), (0.0, 0.0)),
        Primitive("RIGHT", "ARM_Bars", (100.0, 25.0), (100.0, 0.0)),
    ]

    chains = build_connected_chains(
        primitives,
        endpoint_tolerance=0.01,
        max_segments=6,
        max_paths_per_component=100,
    )

    assert any({item.handle for item in chain} == {"LEFT", "MAIN", "RIGHT"} for chain in chains)


def test_late_component_is_not_lost_after_hundreds_of_earlier_components():
    primitives: list[Primitive] = []
    for index in range(600):
        x = index * 1000.0
        primitives.extend(
            [
                Primitive(f"{index}_A", "ARM_Bars", (x, 0.0), (x + 100.0, 0.0)),
                Primitive(f"{index}_B", "ARM_Bars", (x + 100.0, 0.0), (x + 100.0, 25.0)),
            ]
        )

    target_x = 600 * 1000.0
    primitives.extend(
        [
            Primitive("TARGET_MAIN", "ARM_Bars", (target_x, 0.0), (target_x + 240.0, 0.0)),
            Primitive("TARGET_LEFT", "ARM_Bars", (target_x, 0.0), (target_x, 25.0)),
            Primitive("TARGET_RIGHT", "ARM_Bars", (target_x + 240.0, 25.0), (target_x + 240.0, 0.0)),
        ]
    )

    index = LineChainIndex(primitives, endpoint_tolerance=0.01, spatial_cell_size=100.0)
    chains = index.query(
        probe_points=[(target_x + 120.0, 20.0)],
        search_distance=100.0,
        max_segments=6,
        max_paths_per_component=200,
    )

    assert any(
        {item.handle for item in chain} == {"TARGET_MAIN", "TARGET_LEFT", "TARGET_RIGHT"}
        for chain in chains
    )
