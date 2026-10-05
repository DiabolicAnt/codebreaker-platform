import unittest

from services.complexity import CodeValidationError, analyze_code


class ComplexityTests(unittest.TestCase):
    def test_python_branch_and_real_radon_metrics(self):
        report = analyze_code("def positive(x):\n    if x > 0:\n        return x + 1\n    return 0\n")
        self.assertEqual(report.functions[0].complexity, 2)
        self.assertEqual(report.max_cyclomatic, 2)
        self.assertGreater(report.halstead_effort, 0)
        self.assertGreater(report.maintainability, 0)
        self.assertLessEqual(report.maintainability, 100)

    def test_java(self):
        report = analyze_code(
            "class Demo { int abs(int x) { if (x < 0) return -x; return x; } }", "java"
        )
        self.assertEqual(len(report.functions), 1)
        self.assertEqual(report.max_cyclomatic, 2)
        self.assertIsNone(report.halstead_effort)
        self.assertIsNone(report.maintainability)

    def test_csharp_aliases(self):
        for language in ["csharp", "c#", "cs", " C# "]:
            with self.subTest(language=language):
                report = analyze_code(
                    "class Demo { int Abs(int x) { if (x < 0) return -x; return x; } }", language
                )
                self.assertEqual(len(report.functions), 1)
                self.assertEqual(report.max_cyclomatic, 2)
                self.assertIsNone(report.maintainability)

    def test_complex_function_is_not_hidden_by_average(self):
        code = "def complex(x):\n" + "    if x:\n        x -= 1\n" * 11
        code += "    return x\n"
        code += "".join(f"def simple_{i}():\n    return 1\n" for i in range(12))
        report = analyze_code(code)
        self.assertLess(report.cyclomatic, 5)
        self.assertEqual(report.max_cyclomatic, 12)
        self.assertEqual(report.risk_level, "HIGH")

    def test_risk_boundaries(self):
        for maximum, risk in [(5, "LOW"), (6, "MEDIUM"), (10, "MEDIUM"),
                              (11, "HIGH"), (20, "HIGH"), (21, "VERY_HIGH")]:
            with self.subTest(maximum=maximum):
                code = "def f(x):\n" + "    if x:\n        x -= 1\n" * (maximum - 1)
                code += "    return x\n"
                self.assertEqual(analyze_code(code).risk_level, risk)

    def test_invalid_python_is_rejected(self):
        with self.assertRaises(CodeValidationError):
            analyze_code("def broken(:\n    pass")

    def test_empty_code_and_unknown_language_are_rejected(self):
        for code, language in [(" \n", "python"), ("x = 1", "javascript")]:
            with self.subTest(language=language), self.assertRaises(CodeValidationError):
                analyze_code(code, language)

    def test_module_without_functions(self):
        report = analyze_code("x = 1 + 2", " PYTHON ")
        self.assertEqual(report.functions, [])
        self.assertEqual(report.cyclomatic, 1)
        self.assertGreater(report.halstead_effort, 0)

    def test_code_is_never_executed(self):
        report = analyze_code("raise RuntimeError('should not execute')")
        self.assertEqual(report.risk_level, "LOW")
