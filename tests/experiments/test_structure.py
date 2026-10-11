"""Structural guard for the experiments package.

This package spent five generations as one experimental tree and grew three
habits that made a symbol's liveness impossible to answer locally:

  R1  modules re-exported each other's symbols so that an old import path kept
      working ("forwarding facades");
  R2  modules imported siblings *inside* functions, which hides the dependency
      from any static reading of the file;
  R3  the module graph contained import cycles.

All three were cleaned up by the 2026-09-27 restructuring (commits
`6417c24`..`5ced71b`). These tests keep them from coming back, because every
later question ("is this still used?", "what does this module depend on?")
depends on them holding.

Only structural facts are asserted, so the tests do not need to agree with any
list of module names.
"""
from __future__ import annotations

import ast
import pathlib

PKG = "tests.experiments"
RUNTIME_PKG = "app.pipeline_e"
PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
RUNTIME_DIR = PACKAGE_DIR.parents[1] / "app/pipeline_e"
SKIP_DIRS = {"runs", "__pycache__", "assets"}


def production_modules() -> dict[str, pathlib.Path]:
    """Fully-qualified name -> path for every non-test module in the package."""
    out: dict[str, pathlib.Path] = {}
    for package, directory in ((PKG, PACKAGE_DIR), (RUNTIME_PKG, RUNTIME_DIR)):
        for path in sorted(directory.rglob("*.py")):
            parts = path.relative_to(directory).parts
            if set(parts[:-1]) & SKIP_DIRS:
                continue
            stem = parts[-1][:-3]
            if stem == "__init__" or stem.startswith("test_"):
                continue
            out[package + "." + ".".join(parts)[:-3].replace("/", ".")] = path
    return out


def module_level_imports(tree: ast.Module) -> dict[str, tuple[str, str | None]]:
    """local name -> (source module, original name or None for a module import).

    Only module-level statements: a re-export is by definition a module-level
    binding, and function-local imports are R2's subject.
    """
    table: dict[str, tuple[str, str | None]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            source = node.module or ""
            if source == PKG:
                for alias in node.names:
                    table[alias.asname or alias.name] = (f"{PKG}.{alias.name}", None)
            elif source.startswith((PKG + ".", RUNTIME_PKG + ".")):
                for alias in node.names:
                    table[alias.asname or alias.name] = (source, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith((PKG + ".", RUNTIME_PKG + ".")):
                    parts = alias.name.split(".")
                    table[alias.asname or parts[-1]] = (alias.name, None)
    return table


def referenced_names(tree: ast.Module) -> set[str]:
    """Every name read in the module body, excluding the import statements."""
    used: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                used.add(child.id)
            elif isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
                used.add(child.value.id)
    return used


def function_local_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """(line, imported module) for every sibling import inside a function."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom):
                source = sub.module or ""
                if source.startswith((PKG + ".", RUNTIME_PKG + ".")) or source == PKG:
                    found.append((sub.lineno, source))
            elif isinstance(sub, ast.Import):
                found.extend(
                    (sub.lineno, alias.name)
                    for alias in sub.names
                    if alias.name.startswith((PKG + ".", RUNTIME_PKG + "."))
                )
    return found


def parse_all() -> dict[str, tuple[pathlib.Path, ast.Module]]:
    return {name: (path, ast.parse(path.read_text(encoding="utf-8")))
            for name, path in production_modules().items()}


def test_no_forwarding_facades() -> None:
    """R1: a module may only import a sibling symbol it uses itself."""
    offenders: list[str] = []
    for name, (path, tree) in parse_all().items():
        used = referenced_names(tree)
        for local, (source, original) in module_level_imports(tree).items():
            if original is None:            # `import tests.experiments.x as y`
                if local not in used:
                    offenders.append(f"{path.name}: unused module import {source} as {local}")
                continue
            if local not in used:
                offenders.append(
                    f"{path.name}: re-exports {original} from {source} "
                    f"(nothing in this module reads {local!r}) — import it where it is used")
    assert not offenders, ("forwarding facades are back:\n  " + "\n  ".join(sorted(offenders)))


def test_no_function_local_sibling_imports() -> None:
    """R2: a sibling import inside a function hides the dependency."""
    offenders: list[str] = []
    for name, (path, tree) in parse_all().items():
        for lineno, source in function_local_imports(tree):
            offenders.append(f"{path.name}:{lineno} imports {source} inside a function — "
                             "move it to module level")
    assert not offenders, ("function-local sibling imports hide the graph:\n  "
                           + "\n  ".join(sorted(offenders)))


def test_function_local_import_detector_covers_both_import_forms() -> None:
    tree = ast.parse(
        "def f():\n"
        "    from tests.experiments import pipeline\n"
        "    import app.pipeline_e.resume_layout.runtime as runtime\n"
    )
    assert function_local_imports(tree) == [
        (2, "tests.experiments"),
        (3, "app.pipeline_e.resume_layout.runtime"),
    ]


def test_import_graph_is_acyclic() -> None:
    """R3: the module graph must be a DAG, so dependencies have a direction."""
    edges: dict[str, set[str]] = {}
    for name, (_path, tree) in parse_all().items():
        targets = {source for source, _original in module_level_imports(tree).values()
                   if source is not None and source != name}
        targets |= {source for _line, source in function_local_imports(tree)
                    if source != name}
        edges[name] = {t for t in targets if t in production_modules()}

    colour: dict[str, int] = {}
    cycles: list[list[str]] = []
    stack: list[str] = []

    def visit(node: str) -> None:
        colour[node] = 1
        stack.append(node)
        for nxt in sorted(edges.get(node, ())):
            state = colour.get(nxt, 0)
            if state == 1:
                cycles.append(stack[stack.index(nxt):] + [nxt])
            elif state == 0:
                visit(nxt)
        stack.pop()
        colour[node] = 2

    for module in sorted(edges):
        if colour.get(module, 0) == 0:
            visit(module)
    assert not cycles, ("the module graph has import cycles:\n  "
                        + "\n  ".join(" -> ".join(c) for c in cycles))


def test_every_production_module_is_importable() -> None:
    """A module that cannot be imported is a module whose graph edges lie."""
    import importlib

    failures: list[str] = []
    for name in sorted(production_modules()):
        try:
            importlib.import_module(name)
        except Exception as error:                      # noqa: BLE001
            failures.append(f"{name}: {type(error).__name__}: {error}")
    assert not failures, "unimportable modules:\n  " + "\n  ".join(failures)
