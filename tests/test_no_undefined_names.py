"""Защита срещу недефинирани имена в production кода.

ЗАЩО СЪЩЕСТВУВА
---------------
Независим одит на rc21 (P0-1) намери `segments_match_total(code, ...)` в
`excel_export.py`, където `code` не съществува — Excel export можеше да
се срине с NameError при валиден проект.

Тестът е статичен анализ, който не изисква ruff и работи офлайн.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

_BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}


def _defined_in(function: ast.AST) -> set[str]:
    """Имена, които са дефинирани вътре във функцията."""

    names = set()
    args = getattr(function, "args", None)
    if args is not None:
        for group in (args.args, args.posonlyargs, args.kwonlyargs):
            names.update(argument.arg for argument in group)
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(item.id for item in ast.walk(target)
                             if isinstance(item, ast.Name))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            names.update(item.id for item in ast.walk(node.target)
                         if isinstance(item, ast.Name))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            names.update(item.id for item in ast.walk(node.target)
                         if isinstance(item, ast.Name))
        elif isinstance(node, ast.withitem) and node.optional_vars:
            names.update(item.id for item in ast.walk(node.optional_vars)
                         if isinstance(item, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0]
                         for alias in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.NamedExpr):
            names.update(item.id for item in ast.walk(node.target)
                         if isinstance(item, ast.Name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Lambda):
            for group in (node.args.args, node.args.kwonlyargs):
                names.update(argument.arg for argument in group)
        elif isinstance(node, ast.Global) or isinstance(node, ast.Nonlocal):
            names.update(node.names)
    return names


def _module_level(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(item.id for item in ast.walk(node)
                         if isinstance(item, ast.Name))
        elif isinstance(node, (ast.AnnAssign,)):
            names.update(item.id for item in ast.walk(node.target)
                         if isinstance(item, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0]
                         for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Try):
            for handler in node.handlers:
                for inner in handler.body:
                    if isinstance(inner, (ast.Import, ast.ImportFrom)):
                        names.update((alias.asname or alias.name).split(".")[0]
                                     for alias in inner.names)
            for inner in node.body:
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    names.update((alias.asname or alias.name).split(".")[0]
                                 for alias in inner.names)
    return names


def _undefined_in_module(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_names = _module_level(tree) | _BUILTINS
    problems: list[str] = []
    for function in [node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        available = module_names | _defined_in(function) | {"self", "cls"}
        # Обхващащите функции: аргументите и локалните им са видими вътре.
        for parent in ast.walk(tree):
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if function is not parent and function in ast.walk(parent):
                    available |= _defined_in(parent)
        # Вложените функции и comprehension-ите въвеждат свои имена, които
        # външният обход не вижда; те се събират тук.
        for node in ast.walk(function):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                available |= _defined_in(node)
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                                   ast.GeneratorExp)):
                for generator in node.generators:
                    available |= {item.id for item in ast.walk(generator.target)
                                  if isinstance(item, ast.Name)}
        for node in ast.walk(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in available:
                    problems.append(
                        f"{path.name}:{node.lineno} {function.name}(): {node.id}")
    return problems


def test_no_undefined_names_in_export_layer():
    """Export слоят е мястото, където дефектът се появи."""

    problems: list[str] = []
    for path in sorted(Path("lion_rebar/export").glob("*.py")):
        problems.extend(_undefined_in_module(path))
    assert not problems, "недефинирани имена:\n" + "\n".join(problems)


def test_no_undefined_names_in_dxf_layer():
    problems: list[str] = []
    for path in sorted(Path("lion_rebar/dxf").glob("*.py")):
        problems.extend(_undefined_in_module(path))
    assert not problems, "недефинирани имена:\n" + "\n".join(problems)


def test_no_undefined_names_in_review_and_engine():
    problems: list[str] = []
    for folder in ("lion_rebar/review", "lion_rebar/engine", "lion_rebar/project"):
        for path in sorted(Path(folder).glob("*.py")):
            problems.extend(_undefined_in_module(path))
    assert not problems, "недефинирани имена:\n" + "\n".join(problems)
