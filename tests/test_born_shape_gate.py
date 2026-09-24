"""Гейтът мери формата, родена от случай, по нейната ДЕФИНИЦИЯ.

ЗАЩО. Измерено на „Тест-003“ на 22.09.2026, преди тази промяна: трите
кобилици, които минават портата, влизат в описа с ИЗМЕРЕНИ от чертежа числа —
``50 · 16,9706 · 60 · 16,9706 · 50`` при надписани 50/17/60. Прочитът от
котите вече се смяташе и стоеше в реда като предложение, но се подминаваше,
защото ``validate_production_segment_evidence`` минаваше през заключения
``master_engine_v031.parameters_from_segments``, който отказва с „Неподдържан
MASTER код: F020“ — и портата падаше на геометрията.

Тоест дефектът НЕ беше отказ, а тихо разминаване: позицията минава, но с
числа, различни от надписаните. CLAUDE.md: „Една грешно силна позиция е
основание за неприемане.“

Числата тук са от реалния ред `6A04AB`/`6A04B1` (`2x3N14x195`) и от
публикуваната F020 в ENGINE v0.3.20 — не са измислени за теста.
"""
from __future__ import annotations

import copy

import pytest

from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition
from lion_rebar.export.auto_gate import validate_production_segment_evidence


ПОДПИС = (
    "O|n=5|r=0.25781,0.08750,0.30937,0.08750,0.25781"
    "|a=-45.00,45.00,45.00,-45.00"
)
#: Котите до надписа, както ги е записал пакетният отчет — с чуждите вътре.
КОТИ = [17.0, 50.0, 60.0, 25.0, 35.0, 18.0, 325.0, 12.0]
НАДПИСАНИ = [50.0, 17.0, 60.0, 17.0, 50.0]
#: Начертаното: диагоналът мери 16,97, а проектантът е надписал 17.
ИЗМЕРЕНИ = [50.0, 16.970563, 59.999965, 16.970563, 50.0]


def _кобилица(**промени) -> ShapeDefinition:
    """F020 както е публикувана в ENGINE v0.3.20."""

    завои = [(45.0, "LEFT"), (45.0, "RIGHT"), (45.0, "RIGHT"), (45.0, "LEFT")]
    segments = [
        SegmentDefinition(
            index=индекс,
            length_code=код,
            segment_type="LINE",
            bend_after_deg=завои[индекс - 1][0] if индекс <= len(завои) else None,
            bend_direction=завои[индекс - 1][1] if индекс <= len(завои) else "NONE",
            geometry_method="DIRECT_LENGTH",
            angle_mode="FIXED" if индекс <= len(завои) else "AUTO",
        )
        for индекс, код in enumerate(("L1", "L2", "L3", "L2", "L1"), start=1)
    ]
    данни = {
        "code": "F020",
        "name": "Кобилица",
        "state": "LOCKED",
        "active": True,
        "symmetric": True,
        "mirror_allowed": True,
        "segments": segments,
        "recognition": {
            "born_from_case": True,
            "canonical_pattern": "ABCBA",
            "turn_angles_deg": [-45.0, 45.0, 45.0, -45.0],
            "bend_allowance": {"mode": "ROUND_UP", "step_cm": 5.0},
        },
    }
    данни.update(промени)
    return ShapeDefinition(**данни)


def _ред(segments=None) -> dict:
    сегменти = list(НАДПИСАНИ if segments is None else segments)
    return {
        "annotation_handle": "6A04B1",
        "annotation_text": "2x3N14x195",
        "master_shape_code": "F020",
        "declared_length_cm": 195.0,
        "geometry_length_raw": 193.94109065039964,
        "canonical_signature": ПОДПИС,
        "nearby_dimensions_cm": list(КОТИ),
        "production_segments_cm": list(сегменти),
        "production_segment_evidence": {
            "source": "BORN_FROM_CASE_PRINTED",
            "shape_code": "F020",
            "segments": list(сегменти),
        },
    }


def test_надписаните_размери_минават_с_дефиницията():
    """50 + 17 + 60 + 17 + 50 = 194, закръглено до 195 — затваря."""

    assert validate_production_segment_evidence(_ред(), shape=_кобилица()) is True


def test_без_дефиниция_остава_отказ():
    """Предикатът се вика и от места без engine (Финален контрол).

    Там непозната форма е ОТКАЗ, не догадка — точно каквото беше и досега.
    """

    assert validate_production_segment_evidence(_ред()) is False


def test_измереното_от_чертежа_не_е_доказателство():
    """Същината на поправката: 16,9706 не е кота и не минава.

    Тъкмо тези числа влизаха в описа преди промяната.
    """

    assert (
        validate_production_segment_evidence(_ред(ИЗМЕРЕНИ), shape=_кобилица())
        is False
    )


def test_участък_без_своя_кота_отказва():
    """Размер не се досмята наум от остатъка."""

    ред = _ред()
    ред["nearby_dimensions_cm"] = [50.0, 60.0]
    assert validate_production_segment_evidence(ред, shape=_кобилица()) is False


def test_подправеният_прочит_не_минава():
    """Гейтът преизвежда прочита и не вярва на писателя."""

    ред = _ред([50.0, 25.0, 60.0, 25.0, 50.0])
    ред["declared_length_cm"] = 210.0
    assert validate_production_segment_evidence(ред, shape=_кобилица()) is False


def test_сборът_трябва_да_затваря():
    ред = _ред()
    ред["declared_length_cm"] = 260.0
    assert validate_production_segment_evidence(ред, shape=_кобилица()) is False


def test_надвишаването_остава_отказ():
    """Асиметрията не се мени: добавка има само в посока недостиг."""

    ред = _ред()
    ред["declared_length_cm"] = 185.0
    assert validate_production_segment_evidence(ред, shape=_кобилица()) is False


@pytest.mark.parametrize("състояние", ["DRAFT", "RETIRED"])
def test_неодобрена_форма_не_доказва_нищо(състояние):
    """Чернова форма не може да мине производствения гейт."""

    assert (
        validate_production_segment_evidence(
            _ред(), shape=_кобилица(state=състояние)
        )
        is False
    )


def test_неактивна_форма_не_доказва_нищо():
    assert (
        validate_production_segment_evidence(_ред(), shape=_кобилица(active=False))
        is False
    )


def test_формата_трябва_да_е_същата():
    """Дефиниция на друга форма не доказва този ред."""

    assert (
        validate_production_segment_evidence(_ред(), shape=_кобилица(code="F021"))
        is False
    )


def test_източникът_важи_само_за_родена_форма():
    """Форма, която не е родена от случай, няма това правило."""

    форма = _кобилица()
    форма.recognition = {
        key: value
        for key, value in форма.recognition.items()
        if key != "born_from_case"
    }
    assert validate_production_segment_evidence(_ред(), shape=форма) is False


def test_образецът_на_симетрия_трябва_да_съвпада():
    """``ABCBA`` трябва да падне върху ``L1 L2 L3 L2 L1``.

    Форма с пет РАЗЛИЧНИ параметъра описва друго тяло, дори да има същия
    брой участъци и да затваря със същия сбор.
    """

    форма = _кобилица()
    for индекс, сегмент in enumerate(форма.segments, start=1):
        сегмент.length_code = f"L{индекс}"
    assert validate_production_segment_evidence(_ред(), shape=форма) is False


def test_стъпката_за_закръгляне_идва_от_формата():
    """Формата обявява стъпката си и тя се уважава пред общото правило.

    От 22.09.2026 закръглянето важи за ВСЯКА форма с огъвка, а изключение е
    само правият прът (``engine.bend_allowance.STRAIGHT_SHAPES``). Затова
    липсващо обявяване не отнема закръглянето — общото правило пак дава
    стъпка 5. Обявена ДРУГА стъпка обаче трябва да надделее: при 10 cm
    сборът 194 сочи 200, а надписът е 195, и случаят пада.
    """

    форма = _кобилица()
    форма.recognition = dict(форма.recognition)
    форма.recognition["bend_allowance"] = {"mode": "ROUND_UP", "step_cm": 10.0}
    assert validate_production_segment_evidence(_ред(), shape=форма) is False

    # Същият ред с обявената 5 cm минава — разликата е само в стъпката.
    assert validate_production_segment_evidence(_ред(), shape=_кобилица()) is True


def test_заключените_форми_не_минават_през_дефиницията():
    """F001..F010 остават на заключения MASTER, каквото и да носи формата.

    Подадена дефиниция не може да отвори път около него: F006 с шест равни
    сегмента е невалидно стреме и си остава отказ.
    """

    ред = {
        "master_shape_code": "F006",
        "declared_length_cm": 120.0,
        "production_segments_cm": [20.0] * 6,
        "production_segment_evidence": {
            "source": "F006_PRINTED_MASTER_EXACT",
            "shape_code": "F006",
            "segments": [20.0] * 6,
        },
    }
    форма = _кобилица(code="F006")
    assert validate_production_segment_evidence(ред) is False
    assert validate_production_segment_evidence(ред, shape=форма) is False


def test_верижните_форми_остават_на_заключения_MASTER():
    """F011..F019 се смятат от MASTER модула, не от дефиницията.

    Допускът там е 0,11 cm, а през Engine дефиницията би станал 0,51. Първата
    редакция на тази промяна пращаше всичко извън F001..F010 през дефиницията
    и мълчаливо разхлабваше стълбищните форми.

    Случаят: три сегмента със сбор 101,7 при надписани 102 — недостиг 0,3 cm.
    Закръглянето сочи 105, тоест не спасява. През MASTER: отказ. През
    дефиницията би минал по плоския допуск.
    """

    сегменти = [30.0, 41.7, 30.0]
    ред = {
        "master_shape_code": "F012",
        "declared_length_cm": 102.0,
        "canonical_turn_angles_deg": [29.0, 90.0],
        "production_segments_cm": list(сегменти),
        "production_segment_evidence": {
            "source": "STRICT_LOCAL_DIMENSIONS",
            "shape_code": "F012",
            "segments": list(сегменти),
        },
    }
    форма = _кобилица(code="F012")
    форма.segments = форма.segments[:3]
    for индекс, сегмент in enumerate(форма.segments, start=1):
        сегмент.index = индекс
        сегмент.length_code = f"L{индекс}"

    # Подадена дефиниция НЕ отваря път около заключената формула.
    assert validate_production_segment_evidence(ред, shape=форма) is False
    assert validate_production_segment_evidence(ред) is False


def test_редът_и_доказателството_трябва_да_сочат_едно():
    """Доказателство с други сегменти от реда не е доказателство."""

    ред = _ред()
    ред["production_segment_evidence"] = copy.deepcopy(
        ред["production_segment_evidence"]
    )
    ред["production_segment_evidence"]["segments"] = [50.0, 18.0, 60.0, 18.0, 50.0]
    assert validate_production_segment_evidence(ред, shape=_кобилица()) is False
