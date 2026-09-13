from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Literal

from lion_rebar.config.constants import (
    ENGINE_SCHEMA,
    VALID_ENGINE_STATES,
    VALID_SHAPE_STATES,
)
from lion_rebar.engine.models import EnginePackage, ShapeDefinition
from lion_rebar.engine.shape_index import (
    canonical_semantic_signature_from_shape,
    shape_semantic_domains_overlap,
    turn_constraints_from_shape,
)
from lion_rebar.engine.test_runner import (
    calculate_shape_segment_lengths,
    parse_dimensions,
    run_engine_tests,
)
from lion_rebar.utils.files import canonical_json_bytes, sha256_bytes

Severity = Literal["ERROR", "WARNING", "INFO"]
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
SHAPE_CODE_RE = re.compile(r"^F\d{3,}$", re.IGNORECASE)
FAMILY_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]*$", re.IGNORECASE)
REVIEWED_COLLISION_MODE = "PROFILE_OR_MANUAL_REVIEW"


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    path: str = ""


@dataclass(frozen=True)
class ShapeSemanticCollision:
    """Two active shapes with the same canonical engineering semantics."""

    kind: str
    shape_codes: tuple[str, str]
    semantic_signature: str
    canonical_code: str | None = None
    family_id: str | None = None


def shape_fingerprint(shape: ShapeDefinition) -> str:
    data = shape.to_dict()
    for key in ("state", "revision", "locked", "controlled_revision"):
        data.pop(key, None)
    return sha256_bytes(canonical_json_bytes(data))


def validate_engine(
    engine: EnginePackage,
    *,
    parent: EnginePackage | None = None,
    for_publish: bool = False,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if engine.schema != ENGINE_SCHEMA:
        issues.append(
            ValidationIssue("ERROR", "SCHEMA", f"Неподдържана схема: {engine.schema}.", "schema")
        )
    if not engine.engine_id.strip():
        issues.append(ValidationIssue("ERROR", "ENGINE_ID", "Липсва engine_id.", "engine_id"))
    if not SEMVER_RE.match(engine.version):
        issues.append(
            ValidationIssue(
                "ERROR",
                "VERSION",
                "Версията трябва да е Semantic Version, например 0.3.2.",
                "version",
            )
        )
    if engine.state not in VALID_ENGINE_STATES:
        issues.append(
            ValidationIssue("ERROR", "STATE", f"Невалиден статус на engine: {engine.state}.", "state")
        )
    if for_publish and engine.state not in {"DRAFT", "PUBLISHED"}:
        issues.append(
            ValidationIssue("ERROR", "PUBLISH_STATE", "Само чернова може да бъде публикувана.", "state")
        )

    shape_codes: set[str] = set()
    shape_by_code = {
        shape.code.strip().upper(): shape
        for shape in engine.shapes
        if shape.code.strip()
    }
    for index, shape in enumerate(engine.shapes):
        path = f"shapes[{index}]"
        code = shape.code.strip().upper()
        if not SHAPE_CODE_RE.match(code):
            issues.append(
                ValidationIssue("ERROR", "SHAPE_CODE", f"Невалиден код на форма: {shape.code}.", f"{path}.code")
            )
        if code in shape_codes:
            issues.append(
                ValidationIssue("ERROR", "DUPLICATE_SHAPE", f"Дублиран код на форма: {code}.", f"{path}.code")
            )
        shape_codes.add(code)
        if not shape.name.strip():
            issues.append(ValidationIssue("ERROR", "SHAPE_NAME", f"{code}: липсва име.", f"{path}.name"))
        if shape.state not in VALID_SHAPE_STATES:
            issues.append(
                ValidationIssue("ERROR", "SHAPE_STATE", f"{code}: невалиден статус {shape.state}.", f"{path}.state")
            )
        if shape.revision < 1:
            issues.append(
                ValidationIssue("ERROR", "SHAPE_REVISION", f"{code}: ревизията трябва да е ≥ 1.", f"{path}.revision")
            )
        family_id = shape.family_id.strip().upper()
        if family_id and not FAMILY_ID_RE.match(family_id):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "INVALID_FAMILY_ID",
                    (
                        f"{code}: family_id може да съдържа само букви, цифри, "
                        "точка, двоеточие, тире и долна черта."
                    ),
                    f"{path}.family_id",
                )
            )
        if not (1 <= len(shape.segments) <= int(engine.settings.get("max_segments", 12))):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "SEGMENT_COUNT",
                    f"{code}: броят на сегментите трябва да е между 1 и {engine.settings.get('max_segments', 12)}.",
                    f"{path}.segments",
                )
            )
        segment_indexes: set[int] = set()
        length_codes: set[str] = set()
        for segment_index, segment in enumerate(shape.segments):
            segment_path = f"{path}.segments[{segment_index}]"
            if segment.index in segment_indexes:
                issues.append(
                    ValidationIssue("ERROR", "DUPLICATE_SEGMENT", f"{code}: дублиран № сегмент {segment.index}.", segment_path)
                )
            segment_indexes.add(segment.index)
            if segment.index < 1:
                issues.append(
                    ValidationIssue("ERROR", "SEGMENT_INDEX", f"{code}: сегментът трябва да има номер ≥ 1.", segment_path)
                )
            if not segment.length_code.strip():
                issues.append(
                    ValidationIssue("ERROR", "LENGTH_CODE", f"{code}: липсва код на дължина.", segment_path)
                )
            elif segment.length_code.upper() in length_codes:
                issues.append(
                    ValidationIssue(
                        "INFO",
                        "REPEATED_LENGTH_CODE",
                        (
                            f"{code}: кодът {segment.length_code} се използва от "
                            "повече от един сегмент и се прилага автоматично като "
                            "задължително равенство."
                        ),
                        segment_path,
                    )
                )
            length_codes.add(segment.length_code.upper())
            if segment.fixed_length_cm is not None and segment.fixed_length_cm <= 0:
                issues.append(
                    ValidationIssue("ERROR", "FIXED_LENGTH", f"{code}: фиксираната дължина трябва да е > 0.", segment_path)
                )
            if segment.dimension_offset < 0:
                issues.append(
                    ValidationIssue("ERROR", "DIM_OFFSET", f"{code}: отместването на размера не може да е отрицателно.", segment_path)
                )
            issues.extend(
                _validate_segment_angle_semantics(
                    code,
                    segment,
                    segment_path,
                )
            )

        issues.extend(
            _validate_parameter_semantics(
                shape,
                path=path,
            )
        )

        if for_publish and shape.active and shape.state not in {"APPROVED", "LOCKED"}:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "UNAPPROVED_SHAPE",
                    f"{code}: активната форма трябва да е APPROVED или LOCKED преди публикуване.",
                    f"{path}.state",
                )
            )

    issues.extend(
        _validate_shape_identity(
            engine,
            shape_by_code=shape_by_code,
            for_publish=for_publish,
        )
    )

    rule_codes: set[str] = set()
    for index, rule in enumerate(engine.rules):
        code = rule.code.strip().upper()
        if not code:
            issues.append(ValidationIssue("ERROR", "RULE_CODE", "Правило без код.", f"rules[{index}].code"))
        elif code in rule_codes:
            issues.append(
                ValidationIssue("ERROR", "DUPLICATE_RULE", f"Дублиран код на правило: {code}.", f"rules[{index}].code")
            )
        rule_codes.add(code)

    sheet_names = [category.sheet_name for category in engine.categories if category.enabled]
    if len(sheet_names) != len(set(sheet_names)):
        issues.append(ValidationIssue("ERROR", "DUPLICATE_SHEET", "Има дублирани имена на Excel листове.", "categories"))

    if parent is not None:
        parent_shapes = {shape.code.upper(): shape for shape in parent.shapes}
        current_shapes = {shape.code.upper(): shape for shape in engine.shapes}
        for old_code, old_shape in parent_shapes.items():
            if old_shape.locked and old_shape.active and old_code not in current_shapes:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "MISSING_LOCKED_SHAPE",
                        f"Заключената форма {old_shape.code} липсва. За извеждане от употреба я запази и маркирай RETIRED чрез контролирана ревизия.",
                        f"shapes.{old_shape.code}",
                    )
                )
        for shape in engine.shapes:
            old = parent_shapes.get(shape.code.upper())
            if old is None or not old.locked:
                continue
            if shape_fingerprint(old) == shape_fingerprint(shape):
                continue
            revision = shape.controlled_revision or {}
            original_code = str(revision.get("original_code", shape.code)).upper()
            if original_code != shape.code.upper():
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "LOCKED_CODE_CHANGE",
                        f"{shape.code}: кодът на контролирана ревизия не може да се сменя. Създай отделна нова форма.",
                        f"shapes.{shape.code}.code",
                    )
                )
            if shape.revision != old.revision + 1:
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "LOCKED_REVISION_NUMBER",
                        f"{shape.code}: промяна на заключена форма изисква ревизия {old.revision + 1}.",
                        f"shapes.{shape.code}.revision",
                    )
                )
            if not str(revision.get("reason", "")).strip():
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "LOCKED_REVISION_REASON",
                        f"{shape.code}: липсва причина за контролираната ревизия.",
                        f"shapes.{shape.code}.controlled_revision",
                    )
                )
            if not str(revision.get("authorized_by", "")).strip():
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "LOCKED_REVISION_AUTH",
                        f"{shape.code}: липсва автор на контролираната ревизия.",
                        f"shapes.{shape.code}.controlled_revision",
                    )
                )

    if for_publish and engine.production_ready:
        if not engine.shapes:
            issues.append(
                ValidationIssue("ERROR", "NO_SHAPES", "Production engine не може да бъде без форми.", "shapes")
            )
        if bool(engine.settings.get("require_all_tests_to_pass_for_production", True)):
            results = run_engine_tests(engine)
            if not results:
                issues.append(
                    ValidationIssue("ERROR", "NO_TESTS", "Production engine трябва да има контролни тестове.", "tests")
                )
            for result in results:
                if not result.passed:
                    issues.append(
                        ValidationIssue(
                            "ERROR",
                            "FAILED_TEST",
                            f"{result.test_id}: {result.message}",
                            f"tests.{result.test_id}",
                        )
                    )
        recognition_tests = list(engine.tests)
        for item in engine.shapes:
            recognition_tests.extend(item.tests)
        for shape in engine.shapes:
            if (
                not shape.active
                or shape.state not in {"APPROVED", "LOCKED"}
                or _has_recognition_profile(shape, recognition_tests)
            ):
                continue
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "MISSING_RECOGNITION_PROFILE",
                    (
                        f"{shape.code}: липсва валиден контролен тест с размери за matcher-а. "
                        "Добави един успешен тест; профилът се създава автоматично."
                    ),
                    f"shapes.{shape.code}.recognition",
                )
            )
        # Publishing also proves the transform invariants of the matcher.
        # Import locally to keep the engine model/test modules independent from
        # the DXF implementation during normal startup.
        from lion_rebar.engine.recognition_audit import (
            audit_engine_generated_regression,
            audit_engine_recognition,
        )

        audit = audit_engine_recognition(engine)
        for failure in audit.failures[:20]:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "RECOGNITION_AUDIT_FAILED",
                    (
                        f"{failure.shape_code}/{failure.profile_id}: "
                        f"{failure.reason} rotation={failure.rotation_deg:g}°, "
                        f"mirror={'ДА' if failure.mirrored else 'НЕ'}, "
                        f"reverse={'ДА' if failure.reversed_direction else 'НЕ'}, "
                        f"scale={failure.scale:g}."
                    ),
                    f"shapes.{failure.shape_code}.recognition",
                )
            )
        for pair in audit.ambiguous_pairs:
            issues.append(
                ValidationIssue(
                    "WARNING",
                    "RECOGNITION_AMBIGUITY",
                    (
                        "Matcher конфликт между близки MASTER профили: "
                        + " / ".join(pair)
                        + ". Формите се разпознават, но се изпращат за ръчно решение."
                    ),
                    "recognition",
                )
            )

        # A second, deterministic pairwise matrix exercises representation
        # (LWPOLYLINE/LINE chain), parameter changes and safe coordinate noise.
        # Any omitted approved shape or failed case blocks publication.
        generated_audit = audit_engine_generated_regression(engine)
        for failure in generated_audit.failures[:20]:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "GENERATED_TRANSFORMATION_REGRESSION_FAILED",
                    (
                        f"{failure.shape_code}/{failure.profile_id}: "
                        f"{failure.reason} rotation={failure.rotation_deg:g}°, "
                        f"mirror={'ДА' if failure.mirrored else 'НЕ'}, "
                        f"reverse={'ДА' if failure.reversed_direction else 'НЕ'}, "
                        f"scale={failure.scale:g}."
                    ),
                    f"shapes.{failure.shape_code}.recognition",
                )
            )

    return issues


def detect_shape_semantic_collisions(
    engine: EnginePackage,
) -> tuple[ShapeSemanticCollision, ...]:
    """Classify canonical collisions without relying on individual F-codes."""

    active_shapes = [
        shape
        for shape in engine.shapes
        if shape.active and shape.segments
    ]
    shape_by_code = {
        shape.code.strip().upper(): shape
        for shape in active_shapes
        if shape.code.strip()
    }
    groups: dict[str, list[ShapeDefinition]] = defaultdict(list)
    for shape in active_shapes:
        signature = canonical_semantic_signature_from_shape(shape)
        if signature is not None:
            groups[signature.value].append(shape)

    output: list[ShapeSemanticCollision] = []
    for signature_value, shapes in groups.items():
        if len(shapes) < 2:
            continue
        for left, right in combinations(
            sorted(shapes, key=lambda item: item.code.strip().upper()),
            2,
        ):
            left_code = left.code.strip().upper()
            right_code = right.code.strip().upper()
            left_root = _alias_root_code(left_code, shape_by_code)
            right_root = _alias_root_code(right_code, shape_by_code)
            explicit_alias = bool(left.alias_of or right.alias_of)
            if (
                explicit_alias
                and left_root is not None
                and left_root == right_root
            ):
                output.append(
                    ShapeSemanticCollision(
                        kind="ALIAS",
                        shape_codes=(left_code, right_code),
                        semantic_signature=signature_value,
                        canonical_code=left_root,
                    )
                )
                continue
            left_family = left.family_id.strip().upper()
            right_family = right.family_id.strip().upper()
            if left_family and left_family == right_family:
                output.append(
                    ShapeSemanticCollision(
                        kind="FAMILY",
                        shape_codes=(left_code, right_code),
                        semantic_signature=signature_value,
                        family_id=left_family,
                    )
                )
                continue
            output.append(
                ShapeSemanticCollision(
                    kind="DANGEROUS_DUPLICATE",
                    shape_codes=(left_code, right_code),
                    semantic_signature=signature_value,
                )
            )
    return tuple(output)


def _validate_shape_identity(
    engine: EnginePackage,
    *,
    shape_by_code: dict[str, ShapeDefinition],
    for_publish: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    reviewed_pairs, reviewed_pair_issues = _reviewed_collision_resolutions(
        engine,
        shape_by_code=shape_by_code,
    )
    issues.extend(reviewed_pair_issues)
    for index, shape in enumerate(engine.shapes):
        code = shape.code.strip().upper()
        alias = str(shape.alias_of or "").strip().upper()
        if not alias:
            continue
        path = f"shapes[{index}].alias_of"
        if alias == code:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "ALIAS_SELF_REFERENCE",
                    f"{code}: alias_of не може да сочи към същата форма.",
                    path,
                )
            )
            continue
        target = shape_by_code.get(alias)
        if target is None:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "ALIAS_TARGET_MISSING",
                    f"{code}: липсва alias target {alias}.",
                    path,
                )
            )
            continue
        if shape.active and not target.active:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "ALIAS_TARGET_INACTIVE",
                    (
                        f"{code}: активен alias не може да сочи към "
                        f"неактивната форма {alias}."
                    ),
                    path,
                )
            )
        root = _alias_root_code(code, shape_by_code)
        if root is None:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "ALIAS_CYCLE",
                    f"{code}: alias веригата съдържа цикъл.",
                    path,
                )
            )
            continue
        root_shape = shape_by_code.get(root)
        left_signature = canonical_semantic_signature_from_shape(shape)
        root_signature = (
            canonical_semantic_signature_from_shape(root_shape)
            if root_shape is not None
            else None
        )
        if (
            left_signature is not None
            and root_signature is not None
            and left_signature.value != root_signature.value
        ):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "ALIAS_SEMANTICS_MISMATCH",
                    (
                        f"{code}: alias target {root} има различна канонична "
                        "геометрия или параметрични ограничения."
                    ),
                    path,
                )
            )
        left_family = shape.family_id.strip().upper()
        root_family = (
            root_shape.family_id.strip().upper()
            if root_shape is not None
            else ""
        )
        if left_family and root_family and left_family != root_family:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "ALIAS_FAMILY_MISMATCH",
                    (
                        f"{code}: family_id {left_family} не съвпада с "
                        f"canonical target {root} ({root_family})."
                    ),
                    f"shapes[{index}].family_id",
                )
            )

    family_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for shape in engine.shapes:
        if not shape.active or not shape.segments:
            continue
        family_id = shape.family_id.strip().upper()
        if not family_id:
            continue
        signature = canonical_semantic_signature_from_shape(shape)
        if signature is None:
            continue
        family_groups[family_id].append(
            (shape.code.strip().upper(), signature.value)
        )
    for family_id, members in sorted(family_groups.items()):
        signatures = {signature for _code, signature in members}
        if len(signatures) <= 1:
            continue
        codes = ", ".join(sorted(code for code, _signature in members))
        issues.append(
            ValidationIssue(
                "ERROR" if for_publish else "WARNING",
                "FAMILY_SEMANTICS_MISMATCH",
                (
                    f"family_id {family_id} обединява форми с различни "
                    f"канонични семантики: {codes}. Едно family_id може да "
                    "представлява само една инженерна форма."
                ),
                "shapes",
            )
        )

    active_shapes = [
        shape
        for shape in engine.shapes
        if shape.active and shape.segments and shape.code.strip()
    ]
    for left, right in combinations(
        sorted(active_shapes, key=lambda item: item.code.strip().upper()),
        2,
    ):
        left_code = left.code.strip().upper()
        right_code = right.code.strip().upper()
        left_signature = canonical_semantic_signature_from_shape(left)
        right_signature = canonical_semantic_signature_from_shape(right)
        if (
            left_signature is None
            or right_signature is None
            or left_signature.value == right_signature.value
        ):
            # Identical domains are handled once by the canonical collision
            # gate below.
            continue
        left_root = _alias_root_code(left_code, shape_by_code)
        right_root = _alias_root_code(right_code, shape_by_code)
        if left_root is not None and left_root == right_root:
            continue
        left_family = left.family_id.strip().upper()
        right_family = right.family_id.strip().upper()
        if left_family and left_family == right_family:
            # A mismatched family has already received the stronger dedicated
            # FAMILY_SEMANTICS_MISMATCH issue above.
            continue
        if not shape_semantic_domains_overlap(left, right):
            continue
        reviewed = tuple(sorted((left_code, right_code))) in reviewed_pairs
        issues.append(
            ValidationIssue(
                (
                    "WARNING"
                    if reviewed
                    else ("ERROR" if for_publish else "WARNING")
                ),
                (
                    "SEMANTIC_DOMAIN_OVERLAP_REVIEWED"
                    if reviewed
                    else "SEMANTIC_DOMAIN_OVERLAP"
                ),
                (
                    f"{left_code} / {right_code}: еднаквата геометрична "
                    "топология има общ допустим набор от параметри. Matcher-ът "
                    + (
                        "използва отделните контролни профили, а при "
                        "нееднозначност задължително изпраща позицията за "
                        "ръчен преглед. Конфликтът е изрично прегледан и "
                        "разрешен за публикуване."
                        if reviewed
                        else (
                            "не може винаги да избере еднозначно без alias_of, "
                            "общо family_id или изрично прегледано правило "
                            "PROFILE_OR_MANUAL_REVIEW."
                        )
                    )
                ),
                "shapes",
            )
        )

    for collision in detect_shape_semantic_collisions(engine):
        left, right = collision.shape_codes
        if collision.kind == "ALIAS":
            issues.append(
                ValidationIssue(
                    "INFO",
                    "CANONICAL_COLLISION_ALIAS",
                    (
                        f"{left} / {right}: умишлен alias към canonical форма "
                        f"{collision.canonical_code}; конфликтът е разрешен."
                    ),
                    "shapes",
                )
            )
        elif collision.kind == "FAMILY":
            issues.append(
                ValidationIssue(
                    "INFO",
                    "CANONICAL_COLLISION_FAMILY",
                    (
                        f"{left} / {right}: еднаква канонична семантика в "
                        f"декларирано family_id {collision.family_id}."
                    ),
                    "shapes",
                )
            )
        else:
            reviewed = tuple(sorted((left, right))) in reviewed_pairs
            issues.append(
                ValidationIssue(
                    (
                        "WARNING"
                        if reviewed
                        else ("ERROR" if for_publish else "WARNING")
                    ),
                    (
                        "CANONICAL_COLLISION_REVIEWED"
                        if reviewed
                        else "CANONICAL_COLLISION_DANGEROUS"
                    ),
                    (
                        f"{left} / {right}: "
                        + (
                            "канонично еднакви, но конструктивно отделни "
                            "MASTER форми. Matcher-ът използва отделните "
                            "контролни профили, а при равен резултат изисква "
                            "ръчен избор. Конфликтът е изрично прегледан и "
                            "разрешен за публикуване."
                            if reviewed
                            else (
                                "неразличими канонични форми без alias_of, "
                                "общо family_id или изрично прегледано правило "
                                "PROFILE_OR_MANUAL_REVIEW. Публикуването би "
                                "оставило Matcher-а без контролирана политика."
                            )
                        )
                    ),
                    "shapes",
                )
            )
    return issues


def _reviewed_collision_resolutions(
    engine: EnginePackage,
    *,
    shape_by_code: dict[str, ShapeDefinition],
) -> tuple[set[tuple[str, str]], list[ValidationIssue]]:
    """Read owner-approved exceptions for context-distinct geometric overlaps.

    The matcher already keeps close identities ambiguous and routes them to
    manual review.  This declaration only lets a specifically reviewed pair
    pass the publication gate; it never merges F-codes and never changes
    matcher scores.  Any undeclared future collision remains blocking.
    """

    raw = engine.extensions.get("semantic_collision_resolutions", [])
    if raw in (None, ""):
        return set(), []
    if not isinstance(raw, list):
        return set(), [
            ValidationIssue(
                "ERROR",
                "COLLISION_RESOLUTIONS_FORMAT",
                "extensions.semantic_collision_resolutions трябва да е списък.",
                "extensions.semantic_collision_resolutions",
            )
        ]

    reviewed: set[tuple[str, str]] = set()
    issues: list[ValidationIssue] = []
    for index, item in enumerate(raw):
        path = f"extensions.semantic_collision_resolutions[{index}]"
        if not isinstance(item, dict):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "COLLISION_RESOLUTION_FORMAT",
                    "Всеки разрешен конфликт трябва да е JSON обект.",
                    path,
                )
            )
            continue

        shape_codes = item.get("shape_codes", item.get("pair"))
        if not isinstance(shape_codes, (list, tuple)) or len(shape_codes) != 2:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "COLLISION_RESOLUTION_PAIR",
                    "Разрешеният конфликт трябва да съдържа точно два shape_codes.",
                    f"{path}.shape_codes",
                )
            )
            continue
        pair = tuple(
            sorted(str(value).strip().upper() for value in shape_codes)
        )
        if not pair[0] or pair[0] == pair[1]:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "COLLISION_RESOLUTION_PAIR",
                    "shape_codes трябва да са два различни непразни F-кода.",
                    f"{path}.shape_codes",
                )
            )
            continue
        missing = [code for code in pair if code not in shape_by_code]
        if missing:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "COLLISION_RESOLUTION_TARGET_MISSING",
                    "Липсва форма от разрешения конфликт: " + ", ".join(missing),
                    f"{path}.shape_codes",
                )
            )
            continue

        mode = str(item.get("mode", "")).strip().upper()
        if mode != REVIEWED_COLLISION_MODE:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "COLLISION_RESOLUTION_MODE",
                    (
                        f"{pair[0]} / {pair[1]}: mode трябва да е "
                        f"{REVIEWED_COLLISION_MODE}."
                    ),
                    f"{path}.mode",
                )
            )
            continue
        if not bool(item.get("manual_review_on_ambiguity", False)):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "COLLISION_RESOLUTION_REVIEW_GATE",
                    (
                        f"{pair[0]} / {pair[1]}: "
                        "manual_review_on_ambiguity трябва да е true."
                    ),
                    f"{path}.manual_review_on_ambiguity",
                )
            )
            continue
        if not str(item.get("authorized_by", "")).strip():
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "COLLISION_RESOLUTION_AUTH",
                    f"{pair[0]} / {pair[1]}: липсва authorized_by.",
                    f"{path}.authorized_by",
                )
            )
            continue
        if not str(item.get("reason", "")).strip():
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "COLLISION_RESOLUTION_REASON",
                    f"{pair[0]} / {pair[1]}: липсва reason.",
                    f"{path}.reason",
                )
            )
            continue
        if pair in reviewed:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "DUPLICATE_COLLISION_RESOLUTION",
                    f"Дублирано разрешение за {pair[0]} / {pair[1]}.",
                    path,
                )
            )
            continue
        reviewed.add(pair)
    return reviewed, issues


def _alias_root_code(
    code: str,
    shape_by_code: dict[str, ShapeDefinition],
) -> str | None:
    current = str(code).strip().upper()
    visited: set[str] = set()
    while current:
        if current in visited:
            return None
        visited.add(current)
        shape = shape_by_code.get(current)
        if shape is None:
            return None
        target = str(shape.alias_of or "").strip().upper()
        if not target:
            return current
        current = target
    return None


def _validate_parameter_semantics(
    shape: ShapeDefinition,
    *,
    path: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    code = shape.code.strip().upper()
    segments = sorted(shape.segments, key=lambda item: item.index)
    by_index = {int(segment.index): segment for segment in segments}

    by_length_code: dict[str, list] = defaultdict(list)
    for segment in segments:
        length_code = str(segment.length_code or "").strip().upper()
        if length_code:
            by_length_code[length_code].append(segment)
    for length_code, members in by_length_code.items():
        fixed = {
            round(float(item.fixed_length_cm), 9)
            for item in members
            if item.fixed_length_cm is not None
        }
        if len(fixed) > 1:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "REPEATED_LENGTH_CONFLICT",
                    (
                        f"{code}: повтореният код {length_code} има "
                        "противоречиви фиксирани дължини."
                    ),
                    f"{path}.segments",
                )
            )

    has_repeated_length_codes = any(
        len(members) > 1
        for members in by_length_code.values()
    )
    if shape.symmetric and not has_repeated_length_codes:
        for left, right in zip(segments, reversed(segments)):
            if left.index >= right.index:
                break
            if (
                left.fixed_length_cm is not None
                and right.fixed_length_cm is not None
                and not math.isclose(
                    float(left.fixed_length_cm),
                    float(right.fixed_length_cm),
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "SYMMETRY_LENGTH_CONFLICT",
                        (
                            f"{code}: symmetric изисква сегменти "
                            f"{left.index}/{right.index} да са равни."
                        ),
                        f"{path}.symmetric",
                    )
                )

    allowed_relations = {"EQUAL", "EQ", "=", "RATIO"}
    for relation_index, relation in enumerate(shape.parameter_relations):
        relation_path = f"{path}.parameter_relations[{relation_index}]"
        kind = str(relation.kind or "EQUAL").strip().upper()
        if kind not in allowed_relations:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "PARAMETER_RELATION_KIND",
                    f"{code}: неподдържана relation {relation.kind}.",
                    f"{relation_path}.kind",
                )
            )
        left = by_index.get(int(relation.left_segment))
        right = by_index.get(int(relation.right_segment))
        if left is None or right is None or left.index == right.index:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "PARAMETER_RELATION_SEGMENTS",
                    (
                        f"{code}: relation трябва да сочи два различни "
                        "съществуващи сегмента."
                    ),
                    relation_path,
                )
            )
            continue
        if (
            not math.isfinite(float(relation.ratio))
            or float(relation.ratio) <= 0
        ):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "PARAMETER_RELATION_RATIO",
                    f"{code}: relation ratio трябва да е положително число.",
                    f"{relation_path}.ratio",
                )
            )
            continue
        if relation.tolerance is not None and (
            not math.isfinite(float(relation.tolerance))
            or float(relation.tolerance) < 0
        ):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "PARAMETER_RELATION_TOLERANCE",
                    f"{code}: relation tolerance не може да е отрицателен.",
                    f"{relation_path}.tolerance",
                )
            )
        if (
            left.fixed_length_cm is not None
            and right.fixed_length_cm is not None
        ):
            ratio = (
                1.0
                if kind in {"EQUAL", "EQ", "="}
                else float(relation.ratio)
            )
            tolerance = float(relation.tolerance or 1e-9)
            if not math.isclose(
                float(left.fixed_length_cm),
                ratio * float(right.fixed_length_cm),
                rel_tol=0.0,
                abs_tol=tolerance,
            ):
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        "PARAMETER_RELATION_CONFLICT",
                        (
                            f"{code}: фиксираните стойности на сегменти "
                            f"{left.index}/{right.index} нарушават relation."
                        ),
                        relation_path,
                    )
                )

    constraints = turn_constraints_from_shape(shape)
    if any(
        item.parametric and abs(float(item.coefficient)) <= 1e-12
        for item in constraints
    ):
        issues.append(
            ValidationIssue(
                "ERROR",
                "PARAMETRIC_ANGLE_DEGENERATE",
                f"{code}: параметричен ъгъл не може да има нулев коефициент.",
                f"{path}.segments",
            )
        )
    return issues


def _validate_segment_angle_semantics(
    shape_code: str,
    segment,
    path: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    mode = (
        str(segment.angle_mode or "AUTO")
        .strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )
    allowed_modes = {
        "AUTO",
        "FIXED",
        "ФИКСИРАН",
        "ФИКСИРАН_ЪГЪЛ",
        "PARAMETRIC",
        "PARAMETRIC_ANGLE",
        "PARAMETRIC_DIRECTION",
        "ПАРАМЕТРИЧЕН",
        "ПАРАМЕТРИЧЕН_ЪГЪЛ",
    }
    if mode not in allowed_modes:
        issues.append(
            ValidationIssue(
                "ERROR",
                "ANGLE_MODE",
                f"{shape_code}: невалиден angle_mode {segment.angle_mode}.",
                f"{path}.angle_mode",
            )
        )
    minimum = segment.min_angle_deg
    maximum = segment.max_angle_deg
    for key, value in (("min_angle_deg", minimum), ("max_angle_deg", maximum)):
        if value is not None and (
            not math.isfinite(float(value))
            or not (0 < float(value) <= 180)
        ):
            issues.append(
                ValidationIssue(
                    "ERROR",
                    "ANGLE_RANGE",
                    (
                        f"{shape_code}: {key} трябва да е в интервала "
                        "(0, 180] градуса."
                    ),
                    f"{path}.{key}",
                )
            )
    if (
        minimum is not None
        and maximum is not None
        and float(minimum) > float(maximum)
    ):
        issues.append(
            ValidationIssue(
                "ERROR",
                "ANGLE_RANGE_ORDER",
                f"{shape_code}: min_angle_deg е по-голям от max_angle_deg.",
                path,
            )
        )
    return issues


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity == "ERROR" for issue in issues)


def issue_summary(issues: list[ValidationIssue]) -> dict[str, int]:
    summary: dict[str, int] = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for issue in issues:
        summary[issue.severity] = summary.get(issue.severity, 0) + 1
    return summary


def _has_recognition_profile(
    shape: ShapeDefinition,
    tests: list[dict[str, Any]],
) -> bool:
    recognition = shape.recognition if isinstance(shape.recognition, dict) else {}
    for key in (
        "reference_segment_lengths_cm",
        "segment_lengths_cm",
        "reference_segment_profiles",
    ):
        if recognition.get(key):
            return True
    for test in tests:
        shape_code = str(
            test.get("shape_code")
            or test.get("КОД_ФОРМА")
            or ""
        ).strip().upper()
        if shape_code != shape.code.strip().upper():
            continue
        dimensions = parse_dimensions(
            test.get("dimensions", test.get("ОЧАКВАНИ_РАЗМЕРИ_СМ"))
        )
        if not dimensions:
            continue
        try:
            lengths = calculate_shape_segment_lengths(shape, dimensions)
        except ValueError:
            continue
        if len(lengths) == len(shape.segments) and all(value > 0 for value in lengths):
            return True
    return False
