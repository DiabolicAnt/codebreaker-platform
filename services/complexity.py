"""Métricas estáticas. Este módulo nunca ejecuta el código recibido."""

import ast
from dataclasses import dataclass
from typing import Literal

import lizard
from radon.metrics import h_visit, mi_visit


EXTENSIONS = {"python": "py", "java": "java", "csharp": "cs"}
ALIASES = {"py": "python", "c#": "csharp", "cs": "csharp"}
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]


class CodeValidationError(ValueError):
    """Código o lenguaje que no se puede analizar."""


@dataclass
class FunctionReport:
    name: str
    complexity: int
    lines: int
    params: int


@dataclass
class ComplexityReport:
    cyclomatic: float
    max_cyclomatic: int
    halstead_effort: float | None
    maintainability: float | None
    functions: list[FunctionReport]
    risk_level: RiskLevel


def normalize_language(language: str) -> str:
    normalized = language.strip().lower()
    normalized = ALIASES.get(normalized, normalized)
    if normalized not in EXTENSIONS:
        raise CodeValidationError("Lenguaje no soportado. Usa python, java o csharp (c#).")
    return normalized


def analyze_code(code: str, language: str = "python") -> ComplexityReport:
    language = normalize_language(language)
    if not code.strip():
        raise CodeValidationError("No hay código para analizar")

    if language == "python":
        try:
            ast.parse(code)
        except (SyntaxError, ValueError, RecursionError) as exc:
            line = getattr(exc, "lineno", None)
            location = f" en la línea {line}" if line else ""
            raise CodeValidationError(f"El código Python no tiene sintaxis válida{location}.") from exc

    analysis = lizard.analyze_file.analyze_source_code(
        f"submission.{EXTENSIONS[language]}", code
    )
    functions = [
        FunctionReport(
            name=fn.name,
            complexity=fn.cyclomatic_complexity,
            lines=fn.length,
            params=fn.parameter_count,
        )
        for fn in analysis.function_list
    ]
    average = sum(fn.complexity for fn in functions) / len(functions) if functions else 1
    maximum = max((fn.complexity for fn in functions), default=1)
    # La función más compleja no debe quedar oculta detrás del promedio.
    risk: RiskLevel = (
        "LOW" if maximum <= 5 else "MEDIUM" if maximum <= 10
        else "HIGH" if maximum <= 20 else "VERY_HIGH"
    )

    effort = maintainability = None
    if language == "python":
        effort = round(h_visit(code).total.effort, 2)
        maintainability = round(float(mi_visit(code, multi=True)), 2)

    return ComplexityReport(
        cyclomatic=round(average, 2),
        max_cyclomatic=maximum,
        halstead_effort=effort,
        maintainability=maintainability,
        functions=functions,
        risk_level=risk,
    )
