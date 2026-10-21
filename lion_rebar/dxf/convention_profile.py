"""Паспорт на чертежа: предлага ролите по СЪДЪРЖАНИЕ, не по име на слоя.

Защо: днес ролята на слоя се познава почти само по име — в
``session/layer_classifier.py`` 14 от 16 правила гледат името. Това работи за
офисите, чиито имена са изброени в ``recognition_policy``, и се чупи при първия
друг. Измерено на „Вертикали до +8.90“ (``docs/SLOEVE_I_ZRENIE_BG.md``): от 47
слоя **27 са UNKNOWN**, а сред тях са носителите на решаващото доказателство —
``0.4`` с позиционните кръгчета и ``Layer1``/``Layer2`` със заглавията на зоните.

Този модул НИЩО не решава и нищо не променя в разпознаването. Той **предлага**,
с доказателство на български и три примерни handle-а, за да потвърди операторът
веднъж (Етап 2 от ``docs/ROADMAP_PRODUCT_BG.md``). Изрично:

* не връща ``NOISE`` и не пипа видимостта, ``default_visible`` и
  ``filter_analysis_entities`` — падането на обект от анализа е промяна в
  инженерния резултат, а тук нищо не бива да се променя;
* всяко правило отказва при нула или две съвпадения. „Най-близкото“ не е
  отговор — повтарящият се дефект в проекта е близост, приета за принадлежност;
* отказите се връщат заедно с причината си, защото това е екранът, от който
  операторът има нужда: кажи какво не разбра и защо.

Праговете тук са **измерени**, не избрани. Виж докстринга на всяко правило.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from lion_rebar.dxf.models import DxfEntityRecord
from lion_rebar.dxf.recognition_policy import (
    DEFAULT_RECOGNITION_POLICY,
    RecognitionPolicy,
)
from lion_rebar.dxf.text_parser import looks_like_rebar_text, parse_rebar_text

CONVENTION_PROFILE_SCHEMA = "lion-rebar-convention-profile/v1"
PROPOSER_VERSION = "1.0"

#: Ролите, които този модул умее да предложи. Нарочно НЕ включват ``NOISE``.
PROPOSABLE_ROLES = (
    "POSITION_KEY",
    "ZONE_TITLE",
    "POSITION_ANNOTATION",
    "POSITION_LABEL_BLOCK",
    "REBAR_GEOMETRY",
)

#: Как ролите се казват на оператора. Едно място, за да няма два превода.
ROLE_NAMES: dict[str, str] = {
    "POSITION_KEY": "позиционен ключ в кръгче",
    "ZONE_TITLE": "заглавие на зона",
    "POSITION_ANNOTATION": "позиционни надписи",
    "POSITION_LABEL_BLOCK": "надписи в блокове",
    "REBAR_GEOMETRY": "армировъчна геометрия",
}

#: Позиционен ключ: „цифра + буква“ — 6a, 4c, 12b.
POSITION_KEY_PATTERN = re.compile(r"^\d{1,2}[A-Za-zА-Яа-я]$")

#: Ключът стои ВЪТРЕ в кръгчето си. Измерено 2026-09-20: в ``s-10`` 106 ключа са
#: на 7,8–9,3 единици от център при радиус 10,0; в ``s-11`` 225 ключа са на
#: 6,7–10,8 при радиуси 8,3 и 10,0 — най-голямото отношение е **1,08**. Трите
#: фалшиви кандидата на слой ``0`` в ``s-10`` са на 138–694 единици, тоест над
#: 13× радиуса. Прагът е отношение, не разстояние, за да не зависи от единиците.
KEY_INSIDE_CIRCLE_RATIO = 1.25

#: Под този брой ключове слоят не се предлага — за да не се предлага върху шум.
#: Решаващата проверка е кръгчето, не бройката (измерените слоеве носят 106 и 225).
MIN_POSITION_KEYS = 5

#: Дял на ключовете, които трябва да са в кръгче, и дял на ключовете сред всички
#: надписи на слоя. Измерено: 106 от 106 и 225 от 225, тоест 1,00 в двата случая.
KEY_INSIDE_SHARE = 0.8
KEY_TEXT_SHARE = 0.6

#: Заглавие на зона: дълъг надпис с „бр.“ и размер „35/35“, или препратка
#: „(Сх.арм.2)“. Измерено: ``_text`` в ``s-1`` носи 19 такива, ``KOL`` в ``s-10`` — 3.
ZONE_SIZE_PATTERN = re.compile(r"\d{2,3}\s*[/xX]\s*\d{2,3}")
ZONE_SCHEME_PATTERN = re.compile(r"\(\s*[Сс]х\.?\s*арм", re.IGNORECASE)
MIN_ZONE_TITLES = 3

#: Позиционни надписи като текст: дял на разпознатите като армировъчни.
MIN_POSITION_TEXTS = 5
POSITION_TEXT_SHARE = 0.5

#: Под този дял слоят дори не се отчита като отказ. Иначе екранът на оператора
#: се пълни с безсмислици: измерено, ``ARMTXT1`` в „Плоча_кота 14.90“ носи 2
#: армировъчни надписа от 468 — това не е „почти надписов слой“, това е слоят с
#: котите. Отказ се съобщава само когато правилото е било близо.
POSITION_TEXT_NEAR_MISS_SHARE = 0.15

#: Стандартните диаметри. Признакът за надпис-блок: точно ЕДИН атрибут, чиито
#: числови стойности са само от този набор. Измерено 2026-09-20 върху пет реални
#: блокови семейства: ``ARM_N`` в ``s-8`` (FI = 10/12/14), ``ARM_NX`` в ``s-10``
#: и ``s-11``, ``ARM_N`` в ``s-11`` — разпознати; ``OS100`` (оси) и
#: ``elevation_constr`` — отказани. И един отказ по двусмислие: ``ARM_NXX`` в
#: ``s-10``, където броят ``BJ`` има стойности {6, 20, 25, 28} — всички случайно
#: от набора — тоест два тага отговарят и правилото НЕ избира.
STANDARD_DIAMETERS_MM = (6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40)

#: Диаметърът в един чертеж е малко на брой различни стойности. Измерено: 2–6.
MAX_DIAMETER_VALUES = 8

#: Блоковото семейство трябва да носи един и същ набор тагове. Измерено: 1,00 в
#: петте реални семейства (294 от 294, 105 от 105, 195 от 195, 38 от 38, 25 от 25).
BLOCK_TAG_SET_SHARE = 0.9
MIN_LABEL_BLOCKS = 3

#: Колко примера се показват на оператора за всяко предложение.
SAMPLE_COUNT = 3

# --------------------------------------------------------------------------- #
# Армировъчен слой по съдържание
#
# Признакът: до надписа стои обект, дълъг точно колкото ПИШЕ в него. Само
# съвпадение по дължина НЕ стига — измерено 2026-09-20 в „KOFR_B“ слоят
# ``Plumbing`` дава 729 съвпадения при 18× превес, защото тръбите случайно са с
# армировъчни дължини. Затова съвпадението трябва да е ЛОКАЛНО: обектът да е в
# радиуса на политиката около своя надпис. С това условие ``Plumbing`` изчезва
# напълно, а остават:
#
#   KOFR_B:      Колони 101 надписа (контрола 2) -> 50,5x;  BARS 170 (30) -> 5,7x
#   Плоча 14.90: Arm - N12 104 (0);  Arm - N14 22 (0);  Arm - N10 6 (0)
#   d-2 (ASD):   три слоя с 74 (1), 52 (0) и 46 (0)
#   s-11:        Stremena 14 (0);  Horiz.arm 4 (0)
#   шум:         korekciq2 2, otvori 1, Stairs & Landings 1
#
# ``s-1`` и ``s-10`` дават нула и това е ВЯРНО: там надписът е разгънатата
# дължина, а стремето е начертано като правоъгълник, тоест нито един обект не е
# дълъг колкото надписа. Правилото мълчи, вместо да гадае.

#: Допуск при сравнение на дължини — 1% или поне 1 cm.
GEOMETRY_LENGTH_TOLERANCE = 0.01

#: Контролата против случайно съвпадение: същата сметка срещу дължина + това.
#: Не е праг, а контрола — стойността е избрана само да не съвпада системно с
#: реални размери. Измерените контроли излизат 0–2 при 4–170 истински.
GEOMETRY_CONTROL_SHIFT_CM = 13.0

#: Минимум надписи с локално съвпадение и минимален превес над контролата.
#: Измерено: истинските слоеве дават 4–170 при превес 5,7–104; шумът дава 1–2.
MIN_GEOMETRY_MATCHES = 5
MIN_GEOMETRY_CONTROL_RATIO = 4.0

#: Отказ се съобщава само когато правилото е било близо — иначе екранът се пълни.
GEOMETRY_NEAR_MISS_MATCHES = 2


@dataclass(frozen=True)
class ProfileProposal:
    """Предложена роля за един слой или едно блоково семейство."""

    target_kind: str
    layer: str
    role: str
    confidence: float
    evidence: str
    sample_handles: tuple[str, ...] = ()
    block_name: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_kind": self.target_kind,
            "layer": self.layer,
            "block_name": self.block_name,
            "role": self.role,
            "confidence": round(float(self.confidence), 3),
            "evidence": self.evidence,
            "sample_handles": list(self.sample_handles),
            "counts": dict(self.counts),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ProfileRefusal:
    """Правило, което е било близо до съвпадение, но отказва — и защо."""

    target_kind: str
    layer: str
    role: str
    reason: str
    block_name: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    #: Примерни handle-и. Отказ без тях е твърдение; с тях операторът може да
    #: намери обектите в чертежа и да провери сам.
    sample_handles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_kind": self.target_kind,
            "layer": self.layer,
            "block_name": self.block_name,
            "role": self.role,
            "reason": self.reason,
            "counts": dict(self.counts),
            "sample_handles": list(self.sample_handles),
        }


def propose_convention_profile(
    entities: Iterable[DxfEntityRecord],
    *,
    policy: RecognitionPolicy | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Предлага профил на чертожната конвенция по съдържанието на обектите.

    Връща предложения и откази. Нищо не се прилага — прилагането е след
    потвърждение от оператора.
    """

    записи = list(entities)
    by_layer: dict[str, list[DxfEntityRecord]] = defaultdict(list)
    for entity in записи:
        by_layer[str(entity.layer or "0")].append(entity)

    circles = _circles(записи)

    proposals: list[ProfileProposal] = []
    refusals: list[ProfileRefusal] = []

    for layer in sorted(by_layer, key=lambda value: value.casefold()):
        records = by_layer[layer]
        for rule in (
            _propose_position_key_layer,
            _propose_zone_title_layer,
            _propose_position_annotation_layer,
        ):
            proposal, refusal = rule(layer, records, circles=circles)
            if proposal is not None:
                proposals.append(proposal)
            if refusal is not None:
                refusals.append(refusal)

    for block_name, records in sorted(
        _block_families(записи).items(), key=lambda item: (-len(item[1]), item[0])
    ):
        proposal, refusal = _propose_label_block_family(block_name, records)
        if proposal is not None:
            proposals.append(proposal)
        if refusal is not None:
            refusals.append(refusal)

    геометрични, геометрични_откази = _propose_rebar_geometry_layers(
        записи,
        policy=policy or DEFAULT_RECOGNITION_POLICY,
    )
    proposals.extend(геометрични)
    refusals.extend(геометрични_откази)

    допълнителни_откази, отхвърлени = _geometry_evidence_verdicts(
        записи,
        policy=policy or DEFAULT_RECOGNITION_POLICY,
        предложени={
            proposal.layer
            for proposal in proposals
            if proposal.target_kind == "LAYER"
        },
        вече_отказани={
            refusal.layer for refusal in refusals if refusal.target_kind == "LAYER"
        },
    )
    # Присъдата ``not_rebar`` на разделителя бие правилото само по дължина:
    # предложението се маха и на негово място остава отказът с доказателството.
    proposals = [
        proposal
        for proposal in proposals
        if not (proposal.role == "REBAR_GEOMETRY" and proposal.layer in отхвърлени)
    ]
    refusals.extend(допълнителни_откази)

    role_counts = Counter(proposal.role for proposal in proposals)
    return {
        "schema": CONVENTION_PROFILE_SCHEMA,
        "proposer_version": PROPOSER_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_sha256": source_sha256,
        "summary": {
            "layer_count": len(by_layer),
            "proposal_count": len(proposals),
            "refusal_count": len(refusals),
            "role_counts": dict(sorted(role_counts.items())),
        },
        "proposals": [proposal.to_dict() for proposal in proposals],
        "refusals": [refusal.to_dict() for refusal in refusals],
    }


def proposals_by_role(payload: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in payload.get("proposals", [])
        if isinstance(row, dict) and str(row.get("role") or "") == role
    ]


# --------------------------------------------------------------------------- #
# правила


def _propose_position_key_layer(
    layer: str,
    records: Sequence[DxfEntityRecord],
    *,
    circles: Sequence[tuple[float, float, float]],
) -> tuple[ProfileProposal | None, ProfileRefusal | None]:
    """Позиционен ключ: кратък надпис „цифра + буква“ ВЪТРЕ в кръгче.

    Решаващата проверка е геометрична, не текстова: надписът трябва да лежи в
    своето кръгче (разстояние до центъра под ``KEY_INSIDE_CIRCLE_RATIO`` пъти
    радиуса). Затова правилото не зависи от единиците на чертежа и отделя
    ключовете от случайните кратки надписи другаде в листа.
    """

    texts = _texts(records)
    keys = [
        (entity, text)
        for entity, text in texts
        if POSITION_KEY_PATTERN.match(text)
    ]
    if len(keys) < MIN_POSITION_KEYS:
        return None, None

    inside = [
        (entity, text)
        for entity, text in keys
        if _inside_a_circle(entity, circles)
    ]
    counts = {
        "texts": len(texts),
        "key_like": len(keys),
        "inside_circle": len(inside),
    }
    inside_share = len(inside) / len(keys)
    text_share = len(keys) / max(1, len(texts))
    if inside_share < KEY_INSIDE_SHARE:
        return None, ProfileRefusal(
            target_kind="LAYER",
            layer=layer,
            role="POSITION_KEY",
            reason=(
                f"{len(keys)} надписа са по образеца „цифра + буква“, но само "
                f"{len(inside)} стоят в кръгче — ключът се познава по кръгчето, "
                f"не по образеца"
            ),
            counts=counts,
        )
    if text_share < KEY_TEXT_SHARE:
        return None, ProfileRefusal(
            target_kind="LAYER",
            layer=layer,
            role="POSITION_KEY",
            reason=(
                f"{len(keys)} ключа в кръгче, но слоят носи и {len(texts) - len(keys)} "
                f"други надписа — смесен слой, иска потвърждение"
            ),
            counts=counts,
        )
    return (
        ProfileProposal(
            target_kind="LAYER",
            layer=layer,
            role="POSITION_KEY",
            confidence=min(0.95, 0.6 + inside_share * 0.35),
            evidence=(
                f"{len(keys)} надписа по образеца „цифра + буква“, "
                f"{len(inside)} от тях стоят в кръгче"
            ),
            sample_handles=_samples(entity for entity, _text in inside),
            counts=counts,
        ),
        None,
    )


def _propose_zone_title_layer(
    layer: str,
    records: Sequence[DxfEntityRecord],
    *,
    circles: Sequence[tuple[float, float, float]],
) -> tuple[ProfileProposal | None, ProfileRefusal | None]:
    """Заглавие на зона: надпис с „бр.“ и размер, или препратка „(Сх.арм.N)“.

    Измерено: ``_text`` в ``s-1`` носи 19 такива надписа, ``KOL`` в ``s-10`` — 3.
    Границата на зоната е семантична; по близост не се намира (измерено в
    ``docs/SLOEVE_I_ZRENIE_BG.md``: при праг 60 само 55 от 125 позиции падат в
    правилната група, при 80 зоните вече се сливат).
    """

    texts = _texts(records)
    titles = [
        (entity, text)
        for entity, text in texts
        if ("бр." in text and ZONE_SIZE_PATTERN.search(text))
        or ZONE_SCHEME_PATTERN.search(text)
    ]
    if not titles:
        return None, None
    counts = {"texts": len(texts), "zone_titles": len(titles)}
    if len(titles) < MIN_ZONE_TITLES:
        return None, ProfileRefusal(
            target_kind="LAYER",
            layer=layer,
            role="ZONE_TITLE",
            reason=(
                f"само {len(titles)} надписа изглеждат като заглавие на зона — "
                f"под {MIN_ZONE_TITLES} не се предлага"
            ),
            counts=counts,
        )
    пример = titles[0][1]
    return (
        ProfileProposal(
            target_kind="LAYER",
            layer=layer,
            role="ZONE_TITLE",
            confidence=0.9,
            evidence=(
                f"{len(titles)} надписа с „бр.“ и размер или с препратка "
                f"„(Сх.арм.N)“, например „{пример[:48]}“"
            ),
            sample_handles=_samples(entity for entity, _text in titles),
            counts=counts,
        ),
        None,
    )


def _propose_position_annotation_layer(
    layer: str,
    records: Sequence[DxfEntityRecord],
    *,
    circles: Sequence[tuple[float, float, float]],
) -> tuple[ProfileProposal | None, ProfileRefusal | None]:
    """Позиционни надписи като текст: „брой N диаметър x дължина“.

    Ползва същия прочит като production кода (``looks_like_rebar_text``), за да
    няма втора, разминаваща се дефиниция за това какво е армировъчен надпис.
    """

    texts = _texts(records)
    if len(texts) < MIN_POSITION_TEXTS:
        return None, None
    rebar = [(entity, text) for entity, text in texts if looks_like_rebar_text(text)]
    if not rebar:
        return None, None
    share = len(rebar) / len(texts)
    counts = {"texts": len(texts), "rebar_texts": len(rebar)}
    if share < POSITION_TEXT_NEAR_MISS_SHARE:
        return None, None
    if share < POSITION_TEXT_SHARE:
        return None, ProfileRefusal(
            target_kind="LAYER",
            layer=layer,
            role="POSITION_ANNOTATION",
            reason=(
                f"{len(rebar)} от {len(texts)} надписа се четат като армировъчни "
                f"({share:.0%}) — смесен слой, иска потвърждение"
            ),
            counts=counts,
        )
    return (
        ProfileProposal(
            target_kind="LAYER",
            layer=layer,
            role="POSITION_ANNOTATION",
            confidence=min(0.95, 0.7 + share * 0.25),
            evidence=(
                f"{len(rebar)} от {len(texts)} надписа се четат като армировъчни "
                f"({share:.0%})"
            ),
            sample_handles=_samples(entity for entity, _text in rebar),
            counts=counts,
        ),
        None,
    )


def _propose_label_block_family(
    block_name: str,
    records: Sequence[DxfEntityRecord],
) -> tuple[ProfileProposal | None, ProfileRefusal | None]:
    """Надпис-блок: семейство с един атрибут, чиито стойности са само диаметри.

    Така надписът се разпознава, без да знаем как офисът е нарекъл таговете —
    ``BJ/FI/L`` при един, нещо друго при следващия. Признакът е стойностите, не
    имената.

    Семейството се групира по ИМЕ НА БЛОК, не по слой: таговете принадлежат на
    дефиницията на блока, а слоят на вставката е случаен. Измерено в ``s-11``:
    ``ARM_NX`` стои на два слоя и разделено по слоеве по-малката част излизаше
    двусмислена, макар обединените стойности да сочат еднозначно.

    Отказва при нула и при две съвпадения. Измереният случай на двусмислие е
    реален: в ``s-10`` блокът ``ARM_NXX`` има брой ``BJ`` със стойности
    {6, 20, 25, 28} — всички случайно от набора диаметри — тоест два тага
    отговарят и правилото не избира вместо оператора.
    """

    layers = Counter(str(entity.layer or "") for entity in records)
    layer = layers.most_common(1)[0][0] if layers else ""

    with_attributes = [
        (entity, attrs)
        for entity, attrs in ((entity, _attributes(entity)) for entity in records)
        if attrs
    ]
    if len(with_attributes) < MIN_LABEL_BLOCKS:
        return None, None

    tag_sets = Counter(tuple(sorted(attrs)) for _entity, attrs in with_attributes)
    dominant, dominant_count = tag_sets.most_common(1)[0]
    counts = {
        "inserts": len(records),
        "with_attributes": len(with_attributes),
        "dominant_tag_set": dominant_count,
    }
    if dominant_count / len(with_attributes) < BLOCK_TAG_SET_SHARE:
        return None, ProfileRefusal(
            target_kind="BLOCK",
            layer=layer,
            block_name=block_name,
            role="POSITION_LABEL_BLOCK",
            reason=(
                f"вставките носят различни набори тагове ({len(tag_sets)} различни) — "
                f"не е едно семейство"
            ),
            counts=counts,
        )

    numeric: dict[str, list[int]] = defaultdict(list)
    for _entity, attrs in with_attributes:
        for tag, value in attrs.items():
            число = _integer(value)
            if число is not None:
                numeric[tag].append(число)

    кандидати = [
        (tag, sorted(set(values)))
        for tag, values in sorted(numeric.items())
        if values
        and all(value in STANDARD_DIAMETERS_MM for value in values)
        and len(set(values)) <= MAX_DIAMETER_VALUES
    ]
    if not кандидати:
        return None, ProfileRefusal(
            target_kind="BLOCK",
            layer=layer,
            block_name=block_name,
            role="POSITION_LABEL_BLOCK",
            reason=(
                f"нито един от таговете {', '.join(dominant) or '(няма)'} не носи "
                f"само стандартни диаметри — не изглежда позиционен надпис"
            ),
            counts=counts,
        )
    if len(кандидати) > 1:
        имена = ", ".join(tag for tag, _values in кандидати)
        return None, ProfileRefusal(
            target_kind="BLOCK",
            layer=layer,
            block_name=block_name,
            role="POSITION_LABEL_BLOCK",
            reason=(
                f"два или повече тага носят само стандартни диаметри ({имена}) — "
                f"кой е диаметърът решава операторът"
            ),
            counts=counts,
        )

    tag, values = кандидати[0]
    return (
        ProfileProposal(
            target_kind="BLOCK",
            layer=layer,
            block_name=block_name,
            role="POSITION_LABEL_BLOCK",
            confidence=0.9,
            evidence=(
                f"{len(with_attributes)} вставки с един и същ набор тагове "
                f"({', '.join(dominant)}); стойностите на „{tag}“ са "
                f"{', '.join(str(value) for value in values)} — всички от "
                f"стандартния набор диаметри"
            ),
            sample_handles=_samples(entity for entity, _attrs in with_attributes),
            counts=counts,
            details={
                "diameter_tag": tag,
                "diameter_values": values,
                "layers": sorted(layers),
            },
        ),
        None,
    )


def _propose_rebar_geometry_layers(
    records: Sequence[DxfEntityRecord],
    *,
    policy: RecognitionPolicy,
) -> tuple[list[ProfileProposal], list[ProfileRefusal]]:
    """Армировъчен слой: до надписа стои обект с точно надписаната дължина.

    Правилото е кръстосано (над всички слоеве наведнъж), защото признакът е
    връзка между надпис и геометрия, а не свойство на един слой.

    Двете условия заедно: **съвпадение по дължина** и **в радиуса на
    политиката**. Само първото не стига — виж бележката при константите, където
    е измерено как слой с водопровод дава 18× превес без второто условие.

    Контролата (дължина + ``GEOMETRY_CONTROL_SHIFT_CM``) се мери за всеки слой и
    влиза в доказателството, за да се вижда, че съвпадението не е случайно.
    """

    радиус = float(getattr(policy, "max_search_distance_raw", 90.0) or 90.0)
    ако_клетка = max(100.0, радиус)

    геометрия: list[tuple[str, float, list[tuple[float, float]], str]] = []
    решетка: dict[tuple[int, int], list[int]] = defaultdict(list)
    for entity in records:
        точки = _polyline_points(entity)
        if len(точки) < 2:
            continue
        дължина = sum(
            math.dist(точки[индекс], точки[индекс + 1])
            for индекс in range(len(точки) - 1)
        )
        if дължина <= 5.0:
            continue
        индекс = len(геометрия)
        геометрия.append(
            (str(entity.layer or ""), дължина, точки, str(entity.handle or ""))
        )
        for x, y in точки:
            решетка[(int(x // ако_клетка), int(y // ако_клетка))].append(индекс)

    if not геометрия:
        return [], []

    попадения: Counter[str] = Counter()
    контрола: Counter[str] = Counter()
    примери: dict[str, list[str]] = defaultdict(list)
    надписи = 0
    for entity in records:
        текст = str(entity.text or "").strip()
        if not текст or not looks_like_rebar_text(текст):
            continue
        прочит = parse_rebar_text(текст)
        дължина = getattr(прочит, "length_cm", None)
        точка = _point(
            (entity.geometry or {}).get("insert")
            or (entity.geometry or {}).get("position")
        )
        if not дължина or точка is None:
            continue
        надписи += 1
        цел = float(дължина)
        контролна = цел + GEOMETRY_CONTROL_SHIFT_CM
        gx, gy = int(точка[0] // ако_клетка), int(точка[1] // ако_клетка)
        близки: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                близки.update(решетка.get((gx + dx, gy + dy), ()))
        съвпаднали: set[str] = set()
        съвпаднали_контрола: set[str] = set()
        for индекс in близки:
            слой, дълж, точки, handle = геометрия[индекс]
            if min(math.dist(точка, върх) for върх in точки) > радиус:
                continue
            if _близки_дължини(дълж, цел):
                съвпаднали.add(слой)
                if len(примери[слой]) < SAMPLE_COUNT:
                    примери[слой].append(handle)
            elif _близки_дължини(дълж, контролна):
                съвпаднали_контрола.add(слой)
        for слой in съвпаднали:
            попадения[слой] += 1
        for слой in съвпаднали_контрола:
            контрола[слой] += 1

    proposals: list[ProfileProposal] = []
    refusals: list[ProfileRefusal] = []
    for слой, брой in попадения.most_common():
        контролни = контрола[слой]
        превес = брой / max(1, контролни)
        counts = {
            "annotations_with_length": надписи,
            "local_length_matches": брой,
            "control_matches": контролни,
        }
        if брой >= MIN_GEOMETRY_MATCHES and превес >= MIN_GEOMETRY_CONTROL_RATIO:
            proposals.append(
                ProfileProposal(
                    target_kind="LAYER",
                    layer=слой,
                    role="REBAR_GEOMETRY",
                    confidence=min(0.95, 0.7 + min(превес, 20.0) / 80.0),
                    evidence=(
                        f"{брой} надписа имат до себе си обект от този слой с точно "
                        f"своята надписана дължина (до {радиус:.0f} единици); при "
                        f"контролната проверка с изместена дължина — {контролни}"
                    ),
                    sample_handles=tuple(примери.get(слой, ())),
                    counts=counts,
                )
            )
        elif брой >= GEOMETRY_NEAR_MISS_MATCHES:
            refusals.append(
                ProfileRefusal(
                    target_kind="LAYER",
                    layer=слой,
                    role="REBAR_GEOMETRY",
                    reason=(
                        f"само {брой} надписа намират дължината си тук"
                        + (
                            f", а контролата дава {контролни} — превесът "
                            f"{превес:.1f}x не убеждава"
                            if контролни
                            else f" — под {MIN_GEOMETRY_MATCHES}"
                        )
                    ),
                    counts=counts,
                )
            )
    return proposals, refusals


def _geometry_evidence_verdicts(
    записи: Sequence[DxfEntityRecord],
    *,
    policy: RecognitionPolicy,
    предложени: set[str],
    вече_отказани: set[str],
) -> tuple[list[ProfileRefusal], set[str]]:
    """Слоевете, които геометричният различител НЕ приема — на екрана.

    Връща отказите за екрана и имената на слоевете с присъда ``not_rebar``,
    чието предложение трябва да отпадне.

    ЗАЩО. ``dxf/rebar_geometry_evidence.py`` мери всеки слой и връща три
    присъди: ``rebar``, ``not_rebar`` и ``unknown``. Днес до разпознаването
    стига САМО ``rebar`` (``learning/integration.discover_rebar_geometry_layers``
    задържа него), а другите две се изхвърлят мълчаливо. Така операторът не
    научава нито че геометрията Я ИМА, нито защо не е приета — а точно това е
    екранът „Какво програмата НЕ разбра“. Самият модул го знае: докстрингът на
    ``RebarGeometryEvidence.refused_layers`` казва „екранът на оператора има
    нужда точно от тях“, но никой не го викаше.

    Нищо не се предлага и нищо не се прилага. Присъдата, обяснението и
    примерните handle-и са СМЕТНАТИ ОТ МОДУЛА — тук не се въвежда нов праг и
    не се пише нов текст за доказателство.

    Един ред на слой: за какво правилото в този модул вече е казало нещо, тук
    се мълчи. ЕДНО изключение — присъдата ``not_rebar``. Правилото тук мери САМО
    дължина и точно затова предлага ``d-2`` „Slab contour“ за армировъчен слой;
    измерено, обявяването му влошава проекта (124 → 105 преминали позиции,
    анализът от 6,3 s на 128,9 s). Структурната присъда бие мярката по дължина:
    предложението отпада и на негово място застава отказът с доказателството.
    Иначе екранът щеше да предлага за приемане точно слоя, който собственият
    разделител на програмата отказва.

    Измерено 2026-10-05 върху 13-те проекта в ``C:\\lr_exp\\proekti`` (11-те
    чертежа на корпуса плюс ``s-1`` и ``т-1``): добавя **12 реда общо**, най-
    много 5 в един чертеж (``Тест-003``), в седем проекта — нула. Разпределение:
    1 ``not_rebar`` (``d-2`` „Slab contour“ — 74 надписа съвпадат по дължина, но
    148 от 148 заявени обекта са звена от верига) и 11 ``unknown`` с тънко
    доказателство. Отпада точно ЕДНО предложение в целия корпус — същият слой.
    Заливане няма, защото ``evaluate_rebar_geometry`` изобщо не отчита слой без
    нито едно съвпадение по дължина.
    """

    # Внасят се тук, а не горе: ``geometry_candidates`` внася този модул по
    # друга линия и внасянето на ниво модул става кръгово.
    from lion_rebar.dxf.geometry_candidates import _rebar_geometry_layer_role
    from lion_rebar.dxf.rebar_geometry_evidence import (
        VERDICT_NOT_REBAR,
        VERDICT_REBAR,
        evaluate_rebar_geometry,
    )

    доказателство = evaluate_rebar_geometry(записи, policy=policy)

    откази: list[ProfileRefusal] = []
    отхвърлени: set[str] = set()
    for име, ред in sorted(доказателство.layers.items()):
        if ред.verdict == VERDICT_REBAR:
            continue
        ако_отхвърлен = ред.verdict == VERDICT_NOT_REBAR
        if ако_отхвърлен:
            отхвърлени.add(име)
        if име in вече_отказани:
            continue
        if име in предложени and not ако_отхвърлен:
            continue
        познат = _rebar_geometry_layer_role(име, policy) is not None
        произход = (
            "речникът вече познава слоя и разпознаването го ползва — "
            "съдържанието на ТОЗИ чертеж не го потвърждава"
            if познат
            else "речникът не познава слоя — геометрията му не влиза в "
            "разпознаването"
        )
        counts = {
            ключ: int(стойност)
            for ключ, стойност in (ред.signals or {}).items()
            if isinstance(стойност, int) and not isinstance(стойност, bool)
        }
        counts["annotations_with_length"] = int(доказателство.annotations_with_length)
        откази.append(
            ProfileRefusal(
                target_kind="LAYER",
                layer=име,
                role="REBAR_GEOMETRY",
                reason=f"{ред.why}; {произход}",
                counts=counts,
                sample_handles=tuple(ред.sample_handles),
            )
        )
    return откази, отхвърлени


# --------------------------------------------------------------------------- #
# помощни


def _близки_дължини(измерена: float, цел: float) -> bool:
    return abs(измерена - цел) <= max(1.0, цел * GEOMETRY_LENGTH_TOLERANCE)


def _polyline_points(entity: DxfEntityRecord) -> list[tuple[float, float]]:
    """Върховете на линеен обект, както екстракторът ги пази."""

    geometry = entity.geometry or {}
    for ключ in ("points", "vertices", "polyline_points"):
        сурови = geometry.get(ключ)
        if isinstance(сурови, list) and len(сурови) >= 2:
            точки = [
                точка
                for точка in (_point(стойност) for стойност in сурови)
                if точка is not None
            ]
            if len(точки) >= 2:
                return точки
    начало = _point(geometry.get("start"))
    край = _point(geometry.get("end"))
    if начало is not None and край is not None:
        return [начало, край]
    return []


def _texts(records: Sequence[DxfEntityRecord]) -> list[tuple[DxfEntityRecord, str]]:
    изход: list[tuple[DxfEntityRecord, str]] = []
    for entity in records:
        if str(entity.entity_type or "") not in ("TEXT", "MTEXT"):
            continue
        текст = str(entity.text or (entity.geometry or {}).get("text") or "").strip()
        if текст:
            изход.append((entity, текст))
    return изход


def _circles(records: Iterable[DxfEntityRecord]) -> list[tuple[float, float, float]]:
    изход: list[tuple[float, float, float]] = []
    for entity in records:
        if str(entity.entity_type or "") != "CIRCLE":
            continue
        geometry = entity.geometry or {}
        center = geometry.get("center")
        радиус = geometry.get("radius")
        точка = _point(center)
        if точка is None or not радиус:
            continue
        try:
            изход.append((точка[0], точка[1], float(радиус)))
        except (TypeError, ValueError):
            continue
    return изход


def _inside_a_circle(
    entity: DxfEntityRecord,
    circles: Sequence[tuple[float, float, float]],
) -> bool:
    geometry = entity.geometry or {}
    точка = _point(geometry.get("insert") or geometry.get("position"))
    if точка is None:
        return False
    for x, y, радиус in circles:
        if радиус <= 0:
            continue
        if math.hypot(точка[0] - x, точка[1] - y) <= радиус * KEY_INSIDE_CIRCLE_RATIO:
            return True
    return False


def _point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _attributes(entity: DxfEntityRecord) -> dict[str, str]:
    """Атрибутите на INSERT, както ги пази екстракторът — списък под ``geometry``."""

    сурови = (entity.geometry or {}).get("attributes")
    изход: dict[str, str] = {}
    if isinstance(сурови, list):
        for запис in сурови:
            if isinstance(запис, dict) and запис.get("tag") is not None:
                изход[str(запис["tag"]).strip().upper()] = str(запис.get("text") or "").strip()
    elif isinstance(сурови, dict):
        for таг, стойност in сурови.items():
            изход[str(таг).strip().upper()] = str(стойност or "").strip()
    return изход


def _block_families(
    records: Iterable[DxfEntityRecord],
) -> dict[str, list[DxfEntityRecord]]:
    """Вставките по ИМЕ НА БЛОК — таговете са на дефиницията, не на слоя."""

    families: dict[str, list[DxfEntityRecord]] = defaultdict(list)
    for entity in records:
        if str(entity.entity_type or "") != "INSERT":
            continue
        име = str(entity.block_name or "")
        if име:
            families[име].append(entity)
    return families


def _integer(value: str) -> int | None:
    текст = str(value or "").replace(",", ".").strip()
    if not текст:
        return None
    try:
        число = float(текст)
    except ValueError:
        return None
    return int(round(число)) if abs(число - round(число)) < 1e-6 else None


def _samples(entities: Iterable[DxfEntityRecord]) -> tuple[str, ...]:
    изход: list[str] = []
    for entity in entities:
        handle = str(entity.handle or "")
        if handle:
            изход.append(handle)
        if len(изход) >= SAMPLE_COUNT:
            break
    return tuple(изход)
