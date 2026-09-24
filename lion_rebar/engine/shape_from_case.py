"""Нова MASTER форма, родена от ОТКАЗАН случай в чертежа.

ЗАЩО. Собственик, 22.09.2026, за кобилицата в `тест-001`: „това е Кобилица,
но много сложно се чертае в нашия конструктор форми; искам да мога да го
маркирам и да го пусна за одобрение“.

Днес ``EngineStudio.new_shape`` започва от празен лист: код ``F0NN``, име
„НОВА ФОРМА“ и ЕДИН сегмент. Операторът въвежда всяка дължина и всеки ъгъл на
ръка, а после в matcher теста пише примерните размери по памет.

А пакетният отчет вече ДЪРЖИ истината. За всяка от петте кобилици в
`тест-001` там стои::

    O|n=5|r=0.25781,0.08750,0.30937,0.08750,0.25781|a=-45.00,45.00,45.00,-45.00

Пет участъка, нула дъги, симетрични отношения и едни и същи четири ъгъла.
Формата е изчислена — липсва ѝ само име.

СЕМЕЙСТВОТО НЕ Е ТОЧНИТЕ ОТНОШЕНИЯ
-----------------------------------
Петте кобилици имат РАЗЛИЧНИ отношения (0,258/0,088/0,309 срещу
0,291/0,107/0,204), защото формата е параметрична: ``A`` е кракът, ``C`` е
средата, ``B`` е рамото, с което кобилицата прескача. Общото им е броят
участъци, ъглите и ОБРАЗЕЦЪТ НА СИМЕТРИЯ ``ABCBA``. Затова групирането е по
него, а не по сигнатурата дума по дума.

РАЗМЕРИТЕ СЕ ЧЕТАТ, НЕ СЕ ИЗМИСЛЯТ
-----------------------------------
Стойността на всеки участък се взема от НАДПИСАНА кота до позицията, а не от
измерената геометрия: отношението посочва коя кота е коя. Участък без своя
кота НЕ се попълва по измереното — предложението се отказва. Това е същото
правило, по което работи и останалата част от програмата: отказът е по-добър
от догадката.

Измереното в `тест-001` (пет случая, четири размера)::

    надпис        A    B    C   2A+2B+C   надпис L
    2x3N14x195   50   17   60      194      195
    2x4N14x175   50   18   35      171      175
    2x3N14x210   50   23   60      206      210
    2x4N14x185   50   24   35      183      185

Уравнението затваря при 5 от 5 — със закръгляне нагоре до кратно на 5 cm
(``engine.bend_allowance``). Без него нито един случай не минава.

КАКВО НЕ ПРАВИ ТОЗИ МОДУЛ
--------------------------
Не публикува и не активира нищо. Ражда ЧЕРНОВА: тя никога не минава
автоматично одобрение, а пътят чернова -> тестове -> одобрение -> публикуване
остава непроменен (``docs/ENGINE_UPGRADE_AND_APPROVAL_BG.md``).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from lion_rebar.engine.bend_allowance import (
    BEND_ROUNDING_STEP_CM,
    segments_match_total,
)
from lion_rebar.engine.models import SegmentDefinition, ShapeDefinition

#: Две отношения са един и същ параметър, ако се различават под това.
#: Отношенията са дялове от единица, тоест 0,015 е 1,5% от дължината.
RATIO_EQUALITY = 0.015

#: Ъглите се закръглят до градус, преди да се сравняват: DXF ги дава с
#: плаваща точка, а проектантът чертае кръгли ъгли.
ANGLE_EQUALITY_DEG = 2.0

#: Колко може да се разминат изчисленият участък и надписаната кота, за да се
#: приеме, че котата Е този участък. Измерено в `тест-001`: най-голямото
#: разминаване при петте кобилици е 0,64 cm.
DIMENSION_SNAP_CM = 1.2

#: Най-малкият смислен производствен размер.
MIN_SEGMENT_CM = 2.0

#: Буквите на параметрите по реда на първата им поява.
_LETTERS = "ABCDEFGH"

_SIGNATURE_RE = re.compile(
    r"^(?P<closed>[OC])\|n=(?P<count>\d+)\|r=(?P<ratios>[-0-9.,]+)\|a=(?P<angles>[-0-9.,]*)$"
)


class ShapeFromCaseError(RuntimeError):
    """Предложението не може да се роди от този случай."""


@dataclass(frozen=True)
class CaseEvidence:
    """Един отказан случай — това, което чертежът вече е казал за него."""

    annotation_handle: str
    annotation_text: str
    declared_length_cm: float
    diameter_mm: float | None
    ratios: tuple[float, ...]
    turn_angles_deg: tuple[float, ...]
    geometry_length_raw: float
    printed_dimensions_cm: tuple[float, ...]
    geometry_handles: tuple[str, ...] = ()
    closed: bool = False
    project_name: str = ""
    #: Завоите, прочетени от САМАТА начертана полилиния, в реда на върховете ѝ.
    #: Празно, когато геометрията не е подадена.
    drawn_turn_angles_deg: tuple[float, ...] = ()

    @property
    def segment_count(self) -> int:
        return len(self.ratios)


@dataclass(frozen=True)
class CaseValues:
    """Размерите на един случай, прочетени от котите."""

    case: CaseEvidence
    segment_values_cm: tuple[float, ...]
    parameter_values_cm: dict[str, float]
    total_cm: float
    closes_exactly: bool
    closes_with_rounding: bool

    @property
    def closes(self) -> bool:
        return self.closes_exactly or self.closes_with_rounding


@dataclass(frozen=True)
class ShapeProposal:
    """Скелетът на формата, изведен от случаите."""

    pattern: str
    segment_count: int
    turn_angles_deg: tuple[float, ...]
    parameter_codes: tuple[str, ...]
    length_codes: tuple[str, ...]
    values: tuple[CaseValues, ...]
    rounding_step_cm: float | None
    symmetric: bool
    closed: bool
    projects: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def case_count(self) -> int:
        return len(self.values)

    @property
    def project_count(self) -> int:
        return len({name for name in self.projects if name})

    @property
    def equation(self) -> str:
        """``L = 2A + 2B + C`` — прочетено от повторенията на параметрите."""

        брой: dict[str, int] = {}
        for code in self.length_codes:
            брой[code] = брой.get(code, 0) + 1
        части = []
        for code in self.parameter_codes:
            n = брой.get(code, 0)
            части.append(code if n == 1 else f"{n}{code}")
        опашка = ""
        if self.rounding_step_cm:
            опашка = (f", закръглена нагоре до кратно на "
                      f"{self.rounding_step_cm:g} cm")
        return "L = " + " + ".join(части) + опашка


def parse_signature(signature: str) -> tuple[bool, tuple[float, ...], tuple[float, ...]]:
    """Разлага каноничната сигнатура на затвореност, отношения и ъгли."""

    match = _SIGNATURE_RE.match(" ".join(str(signature or "").split()))
    if match is None:
        raise ShapeFromCaseError("Сигнатурата не е в познатия вид.")
    ratios = tuple(float(x) for x in match.group("ratios").split(",") if x)
    angles_raw = match.group("angles")
    angles = tuple(float(x) for x in angles_raw.split(",") if x) if angles_raw else ()
    if len(ratios) != int(match.group("count")):
        raise ShapeFromCaseError("Броят отношения не съвпада с обявения.")
    if len(ratios) < 2:
        raise ShapeFromCaseError("Форма с по-малко от два участъка не се ражда тук.")
    if angles and len(angles) != len(ratios) - 1:
        raise ShapeFromCaseError("Броят ъгли не отговаря на броя участъци.")
    return match.group("closed") == "C", ratios, angles


def symmetry_pattern(ratios: Sequence[float],
                     tolerance: float = RATIO_EQUALITY) -> str:
    """``ABCBA`` — кои участъци са един и същ параметър.

    Буквите се раздават по реда на първата поява, затова образецът е
    независим от размера: две кобилици с различни размери дават един образец.
    """

    букви: list[str] = []
    представители: list[float] = []
    for стойност in ratios:
        за_кого = None
        for индекс, представител in enumerate(представители):
            if abs(представител - стойност) <= tolerance:
                за_кого = индекс
                break
        if за_кого is None:
            представители.append(float(стойност))
            за_кого = len(представители) - 1
        if за_кого >= len(_LETTERS):
            raise ShapeFromCaseError("Твърде много различни параметри за една форма.")
        букви.append(_LETTERS[за_кого])
    return "".join(букви)


def family_key(case: CaseEvidence) -> tuple:
    """Ключ на СЕМЕЙСТВОТО: брой участъци, образец и ъгли.

    Нарочно НЕ включва точните отношения: формата е параметрична и два
    различни размера на една кобилица трябва да попаднат заедно.
    """

    return (
        case.segment_count,
        symmetry_pattern(case.ratios),
        tuple(round(float(a) / ANGLE_EQUALITY_DEG) for a in case.turn_angles_deg),
        bool(case.closed),
    )


def case_from_report_row(row: Mapping[str, Any], *, project_name: str = "",
                         geometry_points: Sequence[Sequence[float]] | None = None,
                         ) -> CaseEvidence | None:
    """Прави случай от ред на пакетния отчет, или ``None``, ако не става.

    Отказва при: липсваща сигнатура, вече разпозната MASTER форма,
    неразрешени криви и липсваща обявена дължина. Дъгите се отказват, защото
    отношенията тогава описват дъга, а не прав участък.
    """

    if str(row.get("master_shape_code") or "").strip():
        return None
    if (row.get("unresolved_curve_count") or 0) or (row.get("source_curve_count") or 0):
        return None
    signature = str(row.get("canonical_signature") or "")
    if not signature:
        return None
    try:
        closed, ratios, angles = parse_signature(signature)
    except ShapeFromCaseError:
        return None
    try:
        declared = float(row.get("declared_length_cm"))
        raw = float(row.get("geometry_length_raw"))
    except (TypeError, ValueError):
        return None
    if declared <= 0 or raw <= 0:
        return None
    try:
        diameter = float(row.get("diameter_mm"))
    except (TypeError, ValueError):
        diameter = None
    printed = []
    for item in row.get("nearby_dimensions_cm") or ():
        try:
            printed.append(float(item))
        except (TypeError, ValueError):
            continue
    return CaseEvidence(
        annotation_handle=str(row.get("annotation_handle") or ""),
        annotation_text=" ".join(str(row.get("annotation_text") or "").split()),
        declared_length_cm=declared,
        diameter_mm=diameter,
        ratios=ratios,
        turn_angles_deg=angles,
        geometry_length_raw=raw,
        printed_dimensions_cm=tuple(printed),
        geometry_handles=tuple(str(h) for h in (row.get("candidate_handles") or ())),
        closed=closed,
        project_name=project_name,
        drawn_turn_angles_deg=turn_angles_from_points(geometry_points or ()),
    )


def turn_angles_from_points(points: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """Завоите между съседните участъци на НАЧЕРТАНАТА полилиния.

    ЗАЩО. Каноничната сигнатура е ориентационно нормализирана и НЕ пази накъде
    прескача прътът. Собственик, 22.09.2026, за родената кобилица: „трябваше
    да е начертана наопаки… това 60 L3 трябва да е отгоре“.

    Чертежът го казва еднозначно. Измерено на `6A04AB` в `тест-001`, в реда на
    върховете: 0°, +45°, 0°, −45°, 0° — тоест завои ЛЯВО, ДЯСНО, ДЯСНО, ЛЯВО,
    а средният участък е ОТГОРЕ. Същата редица излиза и при `6A04B9`, който е
    начертан вертикално (90°, 135°, 90°, 45°, 90°): завоите са спрямо хода,
    затова завъртането на чертежа не ги мени.

    Обръщането на реда на върховете обаче ги ОГЛЕДАЛВА — затова родената форма
    остава с ``mirror_allowed``. Тук се взема редът, в който проектантът е
    начертал пръта.
    """

    върхове = [(float(p[0]), float(p[1])) for p in points if len(p) >= 2]
    ъгли = []
    for a, b in zip(върхове, върхове[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if math.hypot(dx, dy) <= 0:
            continue
        ъгли.append(math.degrees(math.atan2(dy, dx)))
    завои = []
    for преди, след in zip(ъгли, ъгли[1:]):
        завой = (след - преди + 180.0) % 360.0 - 180.0
        завои.append(round(завой, 1))
    return tuple(завои)


def can_seed_new_shape(row: Mapping[str, Any] | None) -> bool:
    """Може ли този ред изобщо да роди форма — за включването на бутона.

    Нарочно е ЕВТИН: само дали има годна сигнатура и няма вече форма. Дали
    размерите се четат и дали уравнението затваря се решава от
    ``propose_shape``, защото иска котите и останалите случаи.
    """

    if not isinstance(row, Mapping):
        return False
    return case_from_report_row(row) is not None


def collect_cases(rows: Iterable[Mapping[str, Any]], *,
                  project_name: str = "",
                  points_by_handle: Mapping[str, Sequence[Sequence[float]]] | None = None,
                  ) -> dict[tuple, list[CaseEvidence]]:
    """Групира отказаните редове по семейство.

    ``points_by_handle`` дава върховете на начертаните полилинии по DXF
    handle. С тях посоката на огъвките идва от чертежа, а не от
    нормализирания подпис.
    """

    точки = dict(points_by_handle or {})
    групи: dict[tuple, list[CaseEvidence]] = {}
    for row in rows:
        върхове = None
        for handle in row.get("candidate_handles") or ():
            върхове = точки.get(str(handle))
            if върхове:
                break
        case = case_from_report_row(row, project_name=project_name,
                                    geometry_points=върхове)
        if case is None:
            continue
        групи.setdefault(family_key(case), []).append(case)
    return групи


def read_segment_values(
    ratios: Sequence[float],
    geometry_length_raw: float,
    printed_dimensions_cm: Sequence[float],
) -> tuple[float, ...] | None:
    """Стойностите на участъците, прочетени от НАДПИСАНИТЕ коти.

    Отношението казва колко е участъкът приблизително; надписаната кота казва
    колко е точно. Участък без кота в обсега ``DIMENSION_SNAP_CM`` връща
    ``None`` — размер не се измисля от измереното.

    Публична е, защото по същото правило се четат и позициите при анализа:
    форма, родена от четене на котите, не бива после да се мери другояче
    (``batch_geometry_report._apply_born_from_case_production_sizes``).
    """

    if not printed_dimensions_cm:
        return None
    стойности: list[float] = []
    for дял in ratios:
        груба = float(дял) * float(geometry_length_raw)
        близка = min(printed_dimensions_cm, key=lambda k: abs(k - груба))
        if abs(близка - груба) > DIMENSION_SNAP_CM:
            return None
        if близка < MIN_SEGMENT_CM:
            return None
        стойности.append(float(близка))
    return tuple(стойности)


def _read_segment_values(case: CaseEvidence) -> tuple[float, ...] | None:
    return read_segment_values(case.ratios, case.geometry_length_raw,
                               case.printed_dimensions_cm)


def _consistent_with_pattern(pattern: str, values: Sequence[float]) -> bool:
    """Повтореният параметър трябва да има една и съща прочетена стойност."""

    първи: dict[str, float] = {}
    for буква, стойност in zip(pattern, values):
        if буква in първи:
            if abs(първи[буква] - стойност) > 0.51:
                return False
        else:
            първи[буква] = float(стойност)
    return True


def propose_shape(cases: Sequence[CaseEvidence], *,
                  rounding_step_cm: float | None = BEND_ROUNDING_STEP_CM) -> ShapeProposal:
    """Скелетът на формата от един или няколко случая от едно семейство.

    Отказва (``ShapeFromCaseError``), когато: случаите не са от едно
    семейство; някой случай няма надписани коти за всичките си участъци;
    повтореният параметър излиза с различни стойности; или уравнението не
    затваря към обявената дължина дори с добавката за огъване.
    """

    if not cases:
        raise ShapeFromCaseError("Няма случаи, от които да се роди форма.")
    ключове = {family_key(case) for case in cases}
    if len(ключове) != 1:
        raise ShapeFromCaseError(
            "Случаите не са от едно семейство — форма не се ражда от смес.")

    образец = symmetry_pattern(cases[0].ratios)
    букви: list[str] = []
    for буква in образец:
        if буква not in букви:
            букви.append(буква)
    length_codes = tuple(f"L{букви.index(буква) + 1}" for буква in образец)
    parameter_codes = tuple(f"L{индекс + 1}" for индекс in range(len(букви)))

    измерени: list[CaseValues] = []
    for case in cases:
        стойности = _read_segment_values(case)
        if стойности is None:
            raise ShapeFromCaseError(
                f"{case.annotation_text}: не всеки участък има своя надписана кота. "
                "Размер не се извежда от измереното.")
        if not _consistent_with_pattern(образец, стойности):
            raise ShapeFromCaseError(
                f"{case.annotation_text}: повтореният параметър излиза с различни "
                "стойности — формата не е симетрична, както изглежда.")
        сбор = float(sum(стойности))
        точно = segments_match_total(None, сбор, case.declared_length_cm)
        със_закръгляне = bool(rounding_step_cm) and segments_match_total(
            None, сбор, case.declared_length_cm, rounding_step=rounding_step_cm)
        if not (точно or със_закръгляне):
            raise ShapeFromCaseError(
                f"{case.annotation_text}: {сбор:g} cm не се затварят към "
                f"{case.declared_length_cm:g} cm. Формата не се ражда.")
        по_параметри = {
            f"L{букви.index(буква) + 1}": стойност
            for буква, стойност in zip(образец, стойности)
        }
        измерени.append(CaseValues(
            case=case,
            segment_values_cm=стойности,
            parameter_values_cm=по_параметри,
            total_cm=сбор,
            closes_exactly=точно,
            closes_with_rounding=със_закръгляне and not точно,
        ))

    # Завоите идват от ЧЕРТЕЖА, когато геометрията е подадена: каноничната
    # сигнатура е ориентационно нормализирана и не пази накъде прескача
    # прътът. Собственик, 22.09.2026: „трябваше да е начертана наопаки…
    # това 60 L3 трябва да е отгоре“.
    #
    # Иска се СЪГЛАСИЕ между случаите: различни завои значат, че прътите не са
    # чертани в един и същ ход, и тогава подписът остава единственото общо.
    начертани = {
        case.drawn_turn_angles_deg
        for case in cases
        if len(case.drawn_turn_angles_deg) == len(образец) - 1
    }
    завои = (next(iter(начертани)) if len(начертани) == 1
             else tuple(cases[0].turn_angles_deg))

    нужно_закръгляне = any(v.closes_with_rounding for v in измерени)
    return ShapeProposal(
        pattern=образец,
        segment_count=len(образец),
        turn_angles_deg=завои,
        parameter_codes=parameter_codes,
        length_codes=length_codes,
        values=tuple(измерени),
        rounding_step_cm=rounding_step_cm if нужно_закръгляне else None,
        symmetric=образец == образец[::-1] and len(set(образец)) < len(образец),
        closed=bool(cases[0].closed),
        projects=tuple(case.project_name for case in cases),
    )


#: Четирите начални посоки на редактора (``headingDegrees``).
_НАЧАЛНИ_ПОСОКИ = (("RIGHT", 0.0), ("UP", 90.0), ("LEFT", 180.0), ("DOWN", -90.0))


def initial_direction_for(proposal: ShapeProposal) -> str:
    """Началната посока, при която силуетът ляга НАЙ-ШИРОКО.

    ЗАЩО. Собственик, 23.09.2026, за родената F021: „обърни я един път
    наляво“. Дотук ``build_draft_shape`` изобщо не задаваше начална посока и
    всяка родена форма падаше на подразбирането ``RIGHT`` — каквато и да е
    формата. „Кобилица-1“ така излизаше отвесна: 17 cm широка и 97 висока.

    Описът чете по редове и рисунката стои в клетка, висока 112 pt — отвесен
    прът в нея не се вижда. Затова изборът е механичен: върти се на
    четвъртинки и се взема тази с най-голям размах по хоризонталата.

    Посоката се ЗАПИСВА в дефиницията (същото поле като падащото меню
    „Начална посока“ в редактора), а не се налага при рисуване. Така
    операторът може да я смени, и то се пази.
    """

    if not proposal.values:
        return "RIGHT"
    дължини = list(proposal.values[0].segment_values_cm)
    завои = [float(a) for a in proposal.turn_angles_deg]
    най_добра, най_широка = "RIGHT", -1.0
    for име, старт in _НАЧАЛНИ_ПОСОКИ:
        посока = старт
        x = 0.0
        xs = [0.0]
        for индекс, дължина in enumerate(дължини):
            x += float(дължина) * math.cos(math.radians(посока))
            xs.append(x)
            if индекс < len(завои):
                # Положителният ъгъл е обратно на часовниковата, тоест НАЛЯВО —
                # същата уговорка, с която се пишат и ``bend_direction``.
                посока += завои[индекс]
        широчина = max(xs) - min(xs)
        if широчина > най_широка + 1e-9:
            най_широка, най_добра = широчина, име
    return най_добра


def build_draft_shape(proposal: ShapeProposal, *, code: str, name: str) -> ShapeDefinition:
    """Чернова форма от предложението — с доказателството в нея.

    Формата излиза ``DRAFT`` и отключена. Пътят до производството остава
    непроменен: тестове, одобрение, публикуване.
    """

    код = str(code or "").strip().upper()
    if not код:
        raise ShapeFromCaseError("Формата иска код.")
    име = " ".join(str(name or "").split())
    if not име:
        raise ShapeFromCaseError("Формата иска име.")

    segments: list[SegmentDefinition] = []
    for индекс, length_code in enumerate(proposal.length_codes, start=1):
        завой = (proposal.turn_angles_deg[индекс - 1]
                 if индекс - 1 < len(proposal.turn_angles_deg) else None)
        segments.append(SegmentDefinition(
            index=индекс,
            length_code=length_code,
            segment_type="LINE",
            bend_after_deg=None if завой is None else round(abs(float(завой)), 1),
            # ЦЕЛИТЕ думи, както ги пише ENGINE-ът: 27 пъти „LEFT“, 19 „RIGHT“,
            # 18 „NONE“ в публикувания v0.3.15. Първата редакция пишеше „L“ и
            # „R“; ``shape_preview`` проверява ``direction in {"LEFT",
            # "НАЛЯВО"}``, затова „L“ ставаше надясно и формата се навиваше на
            # спирала вместо кобилица. Положителният ъгъл е обратно на
            # часовниковата стрелка, тоест НАЛЯВО.
            bend_direction="NONE" if завой is None else ("LEFT" if завой > 0 else "RIGHT"),
            geometry_method="DIRECT_LENGTH",
            angle_mode="FIXED" if завой is not None else "AUTO",
        ))

    recognition: dict[str, Any] = {
        "canonical_pattern": proposal.pattern,
        "turn_angles_deg": [round(float(a), 1) for a in proposal.turn_angles_deg],
        "born_from_case": True,
    }
    if proposal.rounding_step_cm:
        recognition["bend_allowance"] = {
            "mode": "ROUND_UP",
            "step_cm": float(proposal.rounding_step_cm),
        }

    произход = ", ".join(
        f"{v.case.annotation_text} ({v.case.annotation_handle})" for v in proposal.values
    )
    notes = (
        f"Родена от отказан случай на {proposal.case_count} позиции "
        f"в {proposal.project_count or 1} чертеж(а). {proposal.equation}. "
        f"Случаи: {произход}."
    )

    форма = ShapeDefinition(
        code=код,
        name=име,
        state="DRAFT",
        revision=1,
        locked=False,
        # Силуетът ляга по хоризонталата, за да се вижда в описа. Виж
        # ``initial_direction_for``; стойността е данни и операторът може да я
        # смени в редактора или в Engine Studio.
        initial_direction=initial_direction_for(proposal),
        # ``active`` НЕ е лостът за одобрение — лостът е ``state``.
        #
        # Първата редакция раждаше формата с ``active=False`` „за сигурност“.
        # Резултатът: собственикът я одобри и публикува, версия 0.3.19 стана
        # PRODUCTION, а формата остана мълчаливо неизползвана, защото
        # ``active_production_shapes`` филтрира точно по този флаг. Така стои и
        # F010 — одобрена и неактивна от месеци.
        #
        # Черновата не влиза в производството, защото ЧЕРНОВИЯТ engine не е
        # активният; това е гейтът. Формата, която операторът е одобрил и
        # публикувал, трябва да работи. Ако все пак иска да я паркира, вече
        # има отметка „Активна“ в Engine Studio.
        active=True,
        closed=proposal.closed,
        symmetric=proposal.symmetric,
        # Огледалото Е допустимо, и това не е отстъпка.
        #
        # Каноничната сигнатура е ОРИЕНТАЦИОННО НОРМАЛИЗИРАНА — самият отчет
        # го обявява с ``orientation_invariant: true``. Форма, родена от такъв
        # подпис, няма право да твърди ръка. Същото прави и ENGINE-ът: 18 от
        # 19 публикувани форми са с ``mirror_allowed``; изключението е F008
        # (столче), при което горе и долу в плочата са различни.
        mirror_allowed=True,
        notes=notes,
        segments=segments,
        recognition=recognition,
    )

    # Контролните тестове минават през КАНОНИЧНИЯ строител на engine-а.
    #
    # Първата редакция ги пишеше на ръка с ключове ``id`` и речник за
    # размерите. ``run_engine_tests`` обаче чете ``test_id`` и ``shape_code``,
    # а размерите очаква като низ „L1=50; L2=17“. Без ``shape_code``
    # ``find_shape("")`` връща нищо и публикуването падаше с пет пъти
    # „FAILED_TEST … Форма не е намерена“ — точно толкова, колкото са
    # случаите. Затова тук не се измисля формат: ползва се
    # ``test_runner.build_control_test``, а произходът се ДОПИСВА към него.
    from lion_rebar.engine.test_runner import build_control_test

    tests: list[dict[str, Any]] = []
    for индекс, стойности in enumerate(proposal.values, start=1):
        try:
            тест = build_control_test(
                форма,
                {код: round(стойност, 3)
                 for код, стойност in стойности.parameter_values_cm.items()},
                test_id=f"{код}-CASE-{индекс:02d}",
            )
        except (ValueError, KeyError) as exc:
            raise ShapeFromCaseError(
                f"Контролният тест за {стойности.case.annotation_text} не се "
                f"построява: {exc}"
            ) from exc
        тест.update({
            "source": "BORN_FROM_CASE",
            "annotation_text": стойности.case.annotation_text,
            "annotation_handle": стойности.case.annotation_handle,
            "project_name": стойности.case.project_name,
            "segment_lengths_cm": [round(v, 3) for v in стойности.segment_values_cm],
            "production_length_cm": round(стойности.case.declared_length_cm, 3),
            "closes_exactly": bool(стойности.closes_exactly),
        })
        tests.append(тест)
    форма.tests = tests
    return форма
