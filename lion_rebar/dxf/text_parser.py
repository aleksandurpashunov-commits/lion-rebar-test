from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedRebarText:
    original: str
    normalized: str
    count: int | None
    diameter_mm: float | None
    length_cm: float | None
    matched_pattern: str | None
    confidence: float
    count_factors: tuple[int, ...] = ()
    length_expression: str | None = None
    base_length_symbols: tuple[str, ...] = ()
    fixed_addition_cm: float | None = None
    warnings: tuple[str, ...] = ()


# General form examples:
#   32N10x350
#   N10x141
#   4x2N12x(L0+2x100)
_GENERAL_N_PATTERN = re.compile(
    r"(?<![A-Za-zА-Яа-я0-9_])"
    r"(?:(?P<count_expr>\d+(?:\s*[xX×*]\s*\d+)*)\s*)?"
    r"(?:N|Ø|Ф)\s*(?P<diameter>\d+(?:[.,]\d+)?)\s*"
    r"[xX×*]\s*(?P<length_expr>\([^\r\n)]{1,120}\)|[A-Za-zА-Яа-я][A-Za-zА-Яа-я0-9_]*(?:\s*[+\-]\s*[^\s,;]+)*|\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

_COUNT_DIAMETER_L_PATTERN = re.compile(
    r"(?<!\d)(?P<count>\d+)\s*(?:N|Ø|Ф)\s*"
    r"(?P<diameter>\d+(?:[.,]\d+)?)\s*(?:[,;:]?\s*L\s*=\s*|\s+)"
    r"(?P<length>\d+(?:[.,]\d+)?)(?!\d)",
    re.IGNORECASE,
)

_COUNT_DIAMETER_ONLY_PATTERN = re.compile(
    r"(?<!\d)(?P<count>\d+)\s*(?:N|Ø|Ф)\s*"
    r"(?P<diameter>\d+(?:[.,]\d+)?)(?!\d)",
    re.IGNORECASE,
)

_SPACING_PATTERN = re.compile(
    r"^\s*[sSсС]\s*=\s*(?P<spacing>\d+(?:[.,]\d+)?)\s*(?:cm|см)?\s*$",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    normalized = value.replace("\n", " ").replace("\r", " ")
    normalized = normalized.replace("⌀", "Ø").replace("×", "x")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def parse_rebar_text(value: str) -> ParsedRebarText:
    original = value
    normalized = normalize_text(value)

    general = _GENERAL_N_PATTERN.search(normalized)
    if general:
        factors = _parse_count_factors(general.group("count_expr"))
        count = _product(factors) if factors else None
        diameter = _to_float(general.group("diameter"))
        raw_expression = _strip_outer_parentheses(general.group("length_expr"))
        expression = _normalize_expression(raw_expression)
        length_cm = _numeric_expression_value(expression)
        symbol_scan = re.sub(r"(?<=\d)x(?=\d)", "*", expression, flags=re.IGNORECASE)
        symbols = tuple(
            sorted(set(re.findall(r"[A-Za-zА-Яа-я][A-Za-zА-Яа-я0-9_]*", symbol_scan)))
        )
        fixed_addition = _fixed_addition(expression) if symbols else None

        warnings: list[str] = []
        if count is None:
            warnings.append("Липсва брой в избрания текст.")
        if symbols:
            warnings.append(
                "Дължината съдържа променлива и не може да се изчисли без размер от чертежа."
            )

        if symbols:
            pattern_name = "COUNT_EXPR_N_DIAMETER_X_LENGTH_EXPR" if factors else "N_DIAMETER_X_LENGTH_EXPR"
            confidence = 0.94 if factors else 0.82
        else:
            pattern_name = "COUNT_N_DIAMETER_X_LENGTH" if factors else "N_DIAMETER_X_LENGTH"
            confidence = 0.98 if factors else 0.88

        return ParsedRebarText(
            original=original,
            normalized=normalized,
            count=count,
            diameter_mm=diameter,
            length_cm=length_cm,
            matched_pattern=pattern_name,
            confidence=confidence,
            count_factors=factors,
            length_expression=expression,
            base_length_symbols=symbols,
            fixed_addition_cm=fixed_addition,
            warnings=tuple(warnings),
        )

    match = _COUNT_DIAMETER_L_PATTERN.search(normalized)
    if match:
        count = int(match.group("count"))
        diameter = _to_float(match.group("diameter"))
        length = _to_float(match.group("length"))
        return ParsedRebarText(
            original=original,
            normalized=normalized,
            count=count,
            diameter_mm=diameter,
            length_cm=length,
            matched_pattern="COUNT_DIAMETER_L_LENGTH",
            confidence=0.98,
            count_factors=(count,),
            length_expression=_format_number(length),
        )

    match = _COUNT_DIAMETER_ONLY_PATTERN.search(normalized)
    if match:
        count = int(match.group("count"))
        diameter = _to_float(match.group("diameter"))
        return ParsedRebarText(
            original=original,
            normalized=normalized,
            count=count,
            diameter_mm=diameter,
            length_cm=None,
            matched_pattern="COUNT_DIAMETER_ONLY",
            confidence=0.72,
            count_factors=(count,),
            warnings=("Липсва дължина в избрания текст.",),
        )

    return ParsedRebarText(
        original=original,
        normalized=normalized,
        count=None,
        diameter_mm=None,
        length_cm=None,
        matched_pattern=None,
        confidence=0.0,
    )


def parse_spacing_text(value: str) -> float | None:
    match = _SPACING_PATTERN.match(normalize_text(value))
    return _to_float(match.group("spacing")) if match else None


def looks_like_rebar_text(value: str) -> bool:
    return parse_rebar_text(value).matched_pattern is not None


def _parse_count_factors(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    parts = re.split(r"\s*[xX×*]\s*", value)
    try:
        factors = tuple(int(part) for part in parts if part)
    except ValueError:
        return ()
    return factors if factors and all(factor > 0 for factor in factors) else ()


def _product(values: tuple[int, ...]) -> int:
    result = 1
    for value in values:
        result *= value
    return result


def _strip_outer_parentheses(value: str) -> str:
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        return value[1:-1].strip()
    return value


def _normalize_expression(value: str) -> str:
    value = value.replace("×", "x").replace("*", "x").replace(",", ".")
    return re.sub(r"\s+", "", value)


def _numeric_expression_value(expression: str) -> float | None:
    arithmetic = re.sub(r"(?<=\d)x(?=\d)", "*", expression, flags=re.IGNORECASE)
    if re.search(r"[A-Za-zА-Яа-я_]", arithmetic):
        return None
    if not re.fullmatch(r"[0-9.+\-*/()]+", arithmetic):
        return None
    try:
        node = ast.parse(arithmetic, mode="eval")
        return float(_evaluate_numeric_ast(node.body))
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError):
        return None


def _evaluate_numeric_ast(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate_numeric_ast(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
    ):
        left = _evaluate_numeric_ast(node.left)
        right = _evaluate_numeric_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        return left / right
    raise ValueError("Unsupported arithmetic expression")


def _fixed_addition(expression: str) -> float | None:
    # Conservative rule: sum only top-level +/- terms that contain no symbols.
    # Example L0+2x100 -> 200. More complex symbolic algebra is intentionally not guessed.
    terms = re.findall(r"[+\-]?[^+\-]+", expression)
    total = 0.0
    found = False
    for term in terms:
        symbol_scan = re.sub(r"(?<=\d)x(?=\d)", "*", term, flags=re.IGNORECASE)
        if re.search(r"[A-Za-zА-Яа-я_]", symbol_scan):
            continue
        sign = -1.0 if term.startswith("-") else 1.0
        clean = term.lstrip("+-")
        value = _numeric_expression_value(clean)
        if value is None:
            continue
        total += sign * value
        found = True
    return total if found else None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _format_number(value: float | None) -> str | None:
    if value is None:
        return None
    return str(int(value)) if float(value).is_integer() else str(value)
