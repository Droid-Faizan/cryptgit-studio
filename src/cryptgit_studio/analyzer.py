"""Deterministic, conservative Python AST analysis."""

import ast
import json
import os
import sys
import tokenize
from collections import defaultdict
from pathlib import Path

from .models import (
    AnalysisFinding,
    FunctionSymbol,
    Reference,
    ScanSummary,
    SourceLocation,
)
from .git_history import track_function_history
from .ai_reasoning import interpret_packet
from .evidence_packet import build_evidence_packet
from .ollama import OllamaError
from .scanner import discover_repository
from .telemetry import stage

SCHEMA_VERSION = "1.1"


def _location(relative_file: str, node: ast.AST) -> SourceLocation:
    return SourceLocation(
        file=relative_file,
        start_line=getattr(node, "lineno", 1),
        end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        start_column=getattr(node, "col_offset", 0),
        end_column=getattr(node, "end_col_offset", 0),
    )


def _module_name(path: Path) -> str:
    parts = list(path.parts)
    if parts[-1] == "__init__.py":
        return ".".join(parts[:-1]) or path.stem
    return ".".join(parts).removesuffix(".py")


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_decorator_name(node.value)}.{node.attr}"
    return ast.unparse(node)


def _is_test_file(path: Path) -> bool:
    return (
        path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or "tests" in path.parts
    )


def _collect_exports(tree: ast.Module) -> set[str]:
    exports: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in targets
            ):
                value = node.value
                if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                    exports.update(
                        element.value
                        for element in value.elts
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                    )
    return exports


def _module_map(paths: list[Path]) -> set[str]:
    return {_module_name(path) for path in paths}


def _resolve_import_module(
    current_module: str,
    current_path: Path,
    level: int,
    imported_module: str | None,
) -> str:
    package_parts = (
        current_module.split(".")
        if current_path.name == "__init__.py"
        else current_module.split(".")[:-1]
    )
    if level:
        base = package_parts[: len(package_parts) - (level - 1)]
    else:
        base = []
    return ".".join(part for part in (*base, *(imported_module or "").split(".")) if part)


class _Scope:
    def __init__(
        self,
        parent: "_Scope | None",
        kind: str,
        symbol: str | None,
        bindings: set[str],
    ) -> None:
        self.parent = parent
        self.kind = kind
        self.symbol = symbol
        self.bindings = bindings

    def shadows(self, name: str) -> bool:
        return name in self.bindings


def _binding_names(node: ast.AST) -> set[str]:
    bindings: set[str] = set()
    for child in ast.walk(node):
        if child is node:
            continue
        if child is not node and isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings.add(child.name)
            continue
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
            bindings.add(child.id)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(child.name)
        elif isinstance(child, ast.Import):
            bindings.update(alias.asname or alias.name.split(".")[0] for alias in child.names)
        elif isinstance(child, ast.ImportFrom):
            bindings.update(alias.asname or alias.name for alias in child.names)
        elif isinstance(child, ast.ExceptHandler) and child.name:
            bindings.add(child.name)
    return bindings


def _argument_names(node: ast.arguments) -> set[str]:
    arguments = [*node.posonlyargs, *node.args, *node.kwonlyargs]
    if node.vararg:
        arguments.append(node.vararg)
    if node.kwarg:
        arguments.append(node.kwarg)
    return {argument.arg for argument in arguments}


class _ReferenceVisitor(ast.NodeVisitor):
    def __init__(
        self,
        tree: ast.Module,
        relative_file: str,
        module: str,
        path: Path,
        imports: dict[str, tuple[str, str]],
        module_functions: set[str],
        module_names: set[str],
        warnings: list[str],
    ) -> None:
        self.relative_file = relative_file
        self.module = module
        self.path = path
        self.imports = imports
        self.module_functions = module_functions
        self.module_names = module_names
        self.warnings = warnings
        self.references: list[Reference] = []
        self.scope = _Scope(None, "module", None, set(imports) | module_functions)
        self.current_symbol: str | None = None

    def _resolve(self, name: str) -> tuple[str | None, str | None, bool]:
        scope = self.scope
        while scope is not None:
            if scope.kind == "function" and scope.shadows(name):
                return None, None, False
            scope = scope.parent
        if name in self.imports:
            return (*self.imports[name], False)
        if name in self.module_functions:
            is_module_function = (
                self.scope.kind == "function"
                and self.scope.parent is not None
                and self.scope.parent.kind == "module"
            )
            return self.module, name, is_module_function and self.current_symbol == name
        return None, None, False

    def _add(
        self,
        node: ast.AST,
        local_name: str,
        kind: str,
        target_module: str | None,
        target_name: str | None,
        self_reference: bool = False,
    ) -> None:
        if target_module is None or target_name is None:
            return
        self.references.append(
            Reference(
                module=self.module,
                name=local_name,
                local_name=local_name,
                target_module=target_module,
                target_name=target_name,
                location=_location(self.relative_file, node),
                kind=kind,
                source_symbol=self.current_symbol,
                self_reference=self_reference,
            )
        )

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        target_module, target_name, self_reference = self._resolve(node.id)
        self._add(node, node.id, "name", target_module, target_name, self_reference)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            target_module, target_name, self_reference = self._resolve(node.func.id)
            self._add(
                node,
                node.func.id,
                "call",
                target_module,
                target_name,
                self_reference,
            )
            for argument in node.args:
                self.visit(argument)
            for keyword in node.keywords:
                self.visit(keyword.value)
            return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and node.value.id in self.imports:
            target_module, _, _ = self._resolve(node.value.id)
            self._add(node, node.value.id, "attribute", target_module, node.attr)
            return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.warnings.append(
                    f"{self.relative_file}: wildcard import is not resolved"
                )
                continue
            local_name = alias.asname or alias.name
            target_module, target_name = self.imports.get(local_name, (None, None))
            self._add(node, local_name, "import", target_module, target_name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default:
                self.visit(default)
        previous_scope = self.scope
        previous_symbol = self.current_symbol
        self.current_symbol = node.name
        self.scope = _Scope(
            previous_scope,
            "function",
            self.current_symbol,
            _argument_names(node.args) | _binding_names(node),
        )
        for statement in node.body:
            self.visit(statement)
        self.scope = previous_scope
        self.current_symbol = previous_symbol

    def visit_Lambda(self, node: ast.Lambda) -> None:
        previous_scope = self.scope
        self.scope = _Scope(
            previous_scope,
            "function",
            previous_scope.symbol,
            _argument_names(node.args) | _binding_names(node.body),
        )
        self.visit(node.body)
        self.scope = previous_scope

def _imports(
    tree: ast.Module,
    module: str,
    path: Path,
    module_names: set[str],
    warnings: list[str],
) -> dict[str, tuple[str, str]]:
    def is_standard_library(name: str) -> bool:
        root_name = name.split(".", 1)[0]
        return root_name in sys.stdlib_module_names or root_name in sys.builtin_module_names

    imported: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imported_module = _resolve_import_module(module, path, node.level, node.module)
            for alias in node.names:
                if alias.name == "*":
                    warnings.append(f"{path.as_posix()}: wildcard import is not resolved")
                    continue
                local_name = alias.asname or alias.name
                target_module = imported_module
                target_name = alias.name
                if node.module is None and target_module not in module_names:
                    target_module = f"{imported_module}.{alias.name}".strip(".")
                    target_name = alias.name
                imported[local_name] = (target_module, target_name)
                if (
                    not is_standard_library(target_module)
                    and target_module not in module_names
                    and not any(
                    name.startswith(f"{target_module}.") for name in module_names
                    )
                ):
                    warnings.append(
                        f"{path.as_posix()}: unresolved project import {target_module}.{target_name}"
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                imported[local_name] = (alias.name, "*")
                if (
                    not is_standard_library(alias.name)
                    and alias.name not in module_names
                    and not any(
                    name.startswith(f"{alias.name}.") for name in module_names
                    )
                ):
                    warnings.append(f"{path.as_posix()}: unresolved import {alias.name}")
    return imported


def _warn_unsupported_patterns(
    tree: ast.Module, relative_file: str, warnings: list[str]
) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"__import__", "import_module"}:
                warnings.append(
                    f"{relative_file}: dynamic import is not resolved"
                )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
            ):
                warnings.append(
                    f"{relative_file}: dynamic import is not resolved"
                )


def _resolve_reexports(
    module: str,
    name: str,
    imports_by_module: dict[str, dict[str, tuple[str, str]]],
    module_names: set[str],
    seen: set[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    seen = seen or set()
    key = (module, name)
    if key in seen:
        return module, name
    seen.add(key)
    target = imports_by_module.get(module, {}).get(name)
    if target is None or target[1] == "*":
        return module, name
    target_module, target_name = target
    if target_module in imports_by_module and target_name in imports_by_module[target_module]:
        return _resolve_reexports(
            target_module, target_name, imports_by_module, module_names, seen
        )
    return target_module, target_name


def analyze_repository(
    repository: Path,
    include_history: bool = False,
    history_limit: int = 20,
    verify_evidence: bool = False,
    ai_provider: object | None = None,
) -> ScanSummary:
    """Scan Python files and classify module-level functions conservatively."""
    root = repository.resolve()
    with stage("cryptgit.repository_scan"):
        files, discovery_errors, discovery_warnings, coverage_incomplete = discover_repository(root)
    python_files = [item for item in files if item.path.suffix == ".py"]
    trees: dict[str, tuple[Path, ast.Module]] = {}
    errors = list(discovery_errors)
    warnings = list(discovery_warnings)

    for item in python_files:
        relative = item.path.as_posix()
        try:
            with tokenize.open(root / item.path) as source_file:
                source = source_file.read()
        except (OSError, UnicodeError, SyntaxError, LookupError) as error:
            errors.append(f"{relative}: unable to read source ({error})")
            coverage_incomplete = True
            continue
        try:
            with stage("cryptgit.ast_analysis", {"python_file": relative}):
                trees[relative] = (item.path, ast.parse(source, filename=relative))
            _warn_unsupported_patterns(trees[relative][1], relative, warnings)
        except SyntaxError as error:
            location = f"line {error.lineno}" if error.lineno else "unknown line"
            errors.append(f"{relative}: syntax error at {location}: {error.msg}")

    module_by_path = {
        relative: _module_name(path) for relative, (path, _) in trees.items()
    }
    module_names = _module_map([path for path, _ in trees.values()])
    functions: list[FunctionSymbol] = []
    module_functions: dict[str, set[str]] = {}

    for relative, (path, tree) in trees.items():
        module = module_by_path[relative]
        module_functions[module] = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(
                    FunctionSymbol(
                        module=module,
                        name=node.name,
                        location=_location(relative, node),
                        is_async=isinstance(node, ast.AsyncFunctionDef),
                        decorators=tuple(
                            _decorator_name(item) for item in node.decorator_list
                        ),
                        is_private=node.name.startswith("_")
                        and not node.name.startswith("__"),
                        is_test=_is_test_file(path),
                    )
                )

    imports_by_module: dict[str, dict[str, tuple[str, str]]] = {}
    for relative, (path, tree) in trees.items():
        module = module_by_path[relative]
        imports_by_module[module] = _imports(
            tree, module, path, module_names, warnings
        )
    for module, imports in imports_by_module.items():
        for local_name, (target_module, target_name) in list(imports.items()):
            imports[local_name] = _resolve_reexports(
                target_module,
                target_name,
                imports_by_module,
                module_names,
            )

    all_references_by_symbol: dict[tuple[str, str], list[Reference]] = defaultdict(list)
    references_by_symbol: dict[tuple[str, str], list[Reference]] = defaultdict(list)
    for relative, (path, tree) in trees.items():
        module = module_by_path[relative]
        visitor = _ReferenceVisitor(
            tree,
            relative,
            module,
            path,
            imports_by_module[module],
            module_functions[module],
            module_names,
            warnings,
        )
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visitor._visit_function(node)
            else:
                visitor.visit(node)
        for reference in visitor.references:
            if reference.target_module and reference.target_name:
                key = (reference.target_module, reference.target_name)
                all_references_by_symbol[key].append(reference)
                if not reference.self_reference:
                    references_by_symbol[key].append(reference)

    findings: list[AnalysisFinding] = []
    for symbol in sorted(
        functions,
        key=lambda item: (item.location.file, item.location.start_line, item.name),
    ):
        references = tuple(
            sorted(
                all_references_by_symbol.get((symbol.module, symbol.name), []),
                key=lambda item: (
                    item.location.file,
                    item.location.start_line,
                    item.kind,
                    item.name,
                ),
            )
        )
        incoming_references = tuple(
            reference for reference in references if not reference.self_reference
        )
        exports = _collect_exports(trees[symbol.location.file][1])
        if symbol.is_test:
            classification, score, explanation = (
                "not_assessed",
                1.0,
                "Functions in test files are excluded from production dead-code candidates.",
            )
        elif symbol.decorators:
            classification, score, explanation = (
                "uncertain",
                0.6,
                "Heuristic score: decorated functions may be registered or invoked dynamically.",
            )
        elif incoming_references:
            classification, score, explanation = (
                "referenced",
                1.0,
                "A static reference from another symbol or module was detected.",
            )
        elif symbol.name in exports or symbol.name == "__main__":
            classification, score, explanation = (
                "not_assessed",
                1.0,
                "The function is explicitly exported or reserved as an entry point.",
            )
        elif symbol.is_private:
            classification, score, explanation = (
                "potentially_unreferenced",
                0.85,
                "Heuristic score: no incoming static reference was detected for this private function.",
            )
        else:
            classification, score, explanation = (
                "not_assessed",
                1.0,
                "Public functions may be called by external consumers.",
            )
        history = (
            track_function_history(
                root,
                symbol.location.file,
                symbol.name,
                limit=history_limit,
                verify=verify_evidence,
            )
            if include_history
            else None
        )
        if history:
            warnings.extend(history.history_warnings)
        findings.append(
            AnalysisFinding(
                file=symbol.location.file,
                symbol=symbol.name,
                line_start=symbol.location.start_line,
                line_end=symbol.location.end_line,
                classification=classification,
                heuristic_score=score,
                references=references,
                explanation=explanation,
                history=history,
            )
        )

    ai_report: dict[str, object] | None = None
    if ai_provider is not None:
        interpretations: list[dict[str, object]] = []
        ai_errors: list[str] = []
        for finding in findings:
            if finding.classification not in {"potentially_unreferenced", "uncertain"}:
                continue
            packet = build_evidence_packet(
                finding,
                limitations=(
                    "Static analysis is conservative and does not prove code is unused.",
                    "Dynamic dispatch, reflection, runtime registration, and external callers may be missed.",
                ),
                warnings=tuple(warnings),
            )
            try:
                interpretation = interpret_packet(packet, ai_provider)  # type: ignore[arg-type]
                interpretations.append(interpretation.to_dict())
            except (OSError, TimeoutError, ValueError, OllamaError) as error:
                ai_errors.append(f"{packet.finding_id}: {error}")
        ai_report = {
            "enabled": True,
            "model": getattr(ai_provider, "model", "unknown"),
            "interpretations": interpretations,
            "errors": sorted(ai_errors),
        }

    counts = {
        classification: 0
        for classification in (
            "potentially_unreferenced",
            "referenced",
            "uncertain",
            "not_assessed",
        )
    }
    for finding in findings:
        counts[finding.classification] += 1
    try:
        display_path = root.name or os.path.basename(root)
    except OSError:
        display_path = "<repository>"
    return ScanSummary(
        schema_version=SCHEMA_VERSION,
        repository_path=display_path,
        python_files_scanned=len(python_files),
        functions_discovered=len(functions),
        classification_counts=counts,
        findings=tuple(findings),
        parsing_errors=tuple(sorted(set(errors))),
        analysis_warnings=tuple(sorted(set(warnings))),
        coverage_incomplete=coverage_incomplete,
        ai_report=ai_report,
    )


def summary_json(summary: ScanSummary) -> str:
    """Serialize a scan summary deterministically."""
    return json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n"
