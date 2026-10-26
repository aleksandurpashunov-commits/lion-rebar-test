# -*- coding: utf-8 -*-
"""Етикетът на прегледа стои до СОБСТВЕНИЯ си надпис, не над чужд прът.

Собственик, 23.10.2026, гледайки третия лист на ``Тест-002``: „тези по малките
цифри, които нямат никаква геометрия — какъв им е случаят?“

Измерено в браузъра: етикетът на ``90N8x110/20`` стоеше на y = −64107,9, а
самият надпис — на −63565,1, и фибата му точно до него. Етикетът се вдигаше
над точката с ``размер × 0,6``, където ``размер`` е 1/110 от ЦЕЛИЯ чертеж; един
далечен обект раздуваше обхвата и отместването ставаше 543 единици — етикетите
„летяха“ над чужди пръти и изглеждаха като надписи без геометрия.

Отместването вече е в мярката на самия надпис (височината на буквите му).
Само картината се мени — анализът, портата и експортът не четат прегледа.
"""
from __future__ import annotations

import re

from lion_rebar.dxf.models import DxfEntityRecord
from tools.recognition_viewer.viewer import Позиция, построй


def _позиция(handle: str, вид: str = "жълто") -> Позиция:
    return Позиция(
        handle=handle, вид=вид, text="90N8x110/20", count=90, diameter=8,
        declared=110, shape_code="F005", shape_name="ФИБА",
        batch_status="СИЛЕН ПО ГЕОМЕТРИЯ", analysis_status="", segments=[],
        turns=[], geometry_length=110, numeric_difference=0,
        candidate_handles=["P1"], candidate_layer="s-Reinf", candidate_score=0,
        ambiguous=[], reasons=[], infos=[], notes=[], gate_reasons=["UNKNOWN_FRAME"],
        frame="",
    )


def _етикет_y(svg: str, handle: str) -> float:
    група = re.search(r'<g id="етикети">(.*?)</g>', svg).group(1)
    y = re.search(r'<text [^>]*y="([-0-9.]+)"[^>]*data-h="%s"' % handle, група)
    return float(y.group(1))


def test_etiketat_e_do_nadpisa_si_dori_pri_ogromen_chertezh():
    записи = [
        DxfEntityRecord("T1", "TEXT", "s-Text",
                        {"insert": [43409.27, 63565.13, 0], "height": 12.0}, "90N8x110/20"),
        DxfEntityRecord("P1", "LWPOLYLINE", "s-Reinf",
                        {"points": [[43429, 63602], [43383, 63602], [43383, 63583], [43429, 63583]]}),
        # Далечен обект: раздува обхвата на чертежа, както в Тест-002.
        DxfEntityRecord("FAR", "LINE", "0",
                        {"start": [-50000, 0, 0], "end": [-49000, 0, 0]}),
    ]

    картина = построй(записи, [_позиция("T1")])

    y = _етикет_y(картина.svg, "T1")
    # Изгледът е с обърнат Y: надписът е на −63565,13. Етикетът е над него, но
    # в рамките на няколко височини на буквите, не на стотици единици.
    assert -63565.13 - 3 * 12.0 <= y <= -63565.13, y
