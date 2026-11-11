from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_design_tokens as checker


class DesignTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(suffix=".fixture")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.src = self.root / "frontend" / "src"
        self.src.mkdir(parents=True)
        self.write("styles.css", ":root { --ui-ink: #123; --text-base: 13px; }")
        self.root_patch = patch.object(checker, "ROOT", self.root)
        self.src_patch = patch.object(checker, "SRC", self.src)
        self.root_patch.start()
        self.src_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.src_patch.stop)

    def write(self, name: str, content: str) -> Path:
        path = self.src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def style(self, css: str, name: str = "Fixture.vue") -> Path:
        return self.write(name, f"<template><div /></template>\n<style scoped>\n{css}\n</style>\n")

    def assert_scan(self, count: int) -> list[str]:
        problems = checker.check()
        self.assertEqual(len(problems), count, problems)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(checker.main(), int(count > 0))
        self.assertEqual(stdout.getvalue(), f"设计令牌扫描：问题 {count} 处。\n")
        self.assertEqual(stderr.getvalue(), "".join(f"  {problem}\n" for problem in problems))
        return problems

    def test_tokenized_styles_and_spacing_pass(self) -> None:
        self.style(
            ".fixture {color: var(--ui-ink); font-size: var(--text-base); "
            "font: italic var(--text-base)/1.4 sans-serif; margin: 99px; padding: 12px}"
        )
        self.assert_scan(0)

    def test_final_declarations_without_semicolons_are_checked(self) -> None:
        self.style(".x {font-size:99px}\n.y {color:#abc}\n.z {font: italic 17px serif}")
        problems = self.assert_scan(3)
        for line, problem in enumerate(problems, start=3):
            self.assertIn(f"frontend/src/Fixture.vue:{line} ", problem)

    def test_multiline_comments_preserve_property_line(self) -> None:
        self.style(
            "/* font-size:99px; {\n color:#fff; } */\n.x {\n  font-size:\n"
            "    calc(var(--text-base) + 2px)\n}"
        )
        problems = self.assert_scan(1)
        self.assertIn("Fixture.vue:6 ", problems[0])
        self.assertIn("font-size", problems[0])

    def test_strings_urls_and_nested_parentheses_do_not_split_declarations(self) -> None:
        self.style(
            r""".fixture {
  content: "escaped\"; } { font-size:88px; color:#fff; /* not a comment */";
  background: url("data:image/svg+xml;utf8,<svg>{;#abc;99px}</svg>");
  mask: url(data:image/svg+xml;utf8,{;#abc;99px});
  --custom: outer(inner(";}", pair({a;b})), "{");
  font-size: calc(var(--text-base, max(2px, 3px)) + 1px);
  color: #abc
}"""
        )
        problems = self.assert_scan(2)
        self.assertIn("font-size: calc(", problems[0])
        self.assertIn("color: #abc", problems[1])

    def test_quoted_literals_are_not_color_or_font_values(self) -> None:
        self.style(
            '.fixture {content: "#abc 99px"; font-family: "99px"; '
            'font: var(--text-base) "99px"; background: url(#abc)}'
        )
        self.assert_scan(0)

    def test_font_shorthand_and_calc_fallbacks_are_checked(self) -> None:
        for value in (
            "99px",
            ".5px",
            "1e2PX",
            "calc(var(--text-base) + 2px)",
            "clamp(12px, calc(1em + max(2px, 3px)), 20px)",
            "var(--text-base, 99px)",
        ):
            with self.subTest(value=value):
                self.style(f".fixture {{ FONT-SIZE: {value}; font: italic {value}/1.4 serif }}")
                self.assert_scan(2)

    def test_font_shorthand_line_height_is_not_font_size(self) -> None:
        self.style(".fixture {font: italic var(--text-base)/24px sans-serif}")
        self.assert_scan(0)

    def test_hex_color_lengths_and_fallbacks_are_checked(self) -> None:
        for value in ("#abc", "#abcd", "#ABCDEF", "#12345678", "var(--ui-ink, #abc)"):
            with self.subTest(value=value):
                self.style(f".fixture {{border: 1px solid {value}}}")
                self.assert_scan(1)

    def test_nested_at_rules_keep_scope_and_resume_after_print(self) -> None:
        self.style(
            "@layer fixture { @supports (display: grid) {\n"
            "@media print {.x {font-size:99px; color:#fff}}\n"
            "@media screen {.x {font-size:98px}}\n"
            ".x {color:#abc}\n}}\n.x {font-size:97px}"
        )
        self.assert_scan(3)

    def test_print_only_media_queries_pass(self) -> None:
        for query in (
            "print",
            "only print",
            "PRINT and (color)",
            "print and (width > calc(10px + (2px)))",
            "print, print and (color)",
        ):
            with self.subTest(query=query):
                self.style(
                    f"@media {query} {{ @supports (display: grid) {{ .x {{font:99px serif; color:#fff}} }} }}"
                )
                self.assert_scan(0)

    def test_non_print_media_queries_cannot_claim_exemption(self) -> None:
        for query in (
            "printscreen",
            "not print",
            "screen, print",
            "print, screen",
            "print, all",
            '(label: "@media print")',
            "(prefers-color-scheme: dark)",
        ):
            with self.subTest(query=query):
                self.style(f"@media {query} {{ .x {{font-size:99px; color:#fff}} }}")
                self.assert_scan(2)

    def test_paper_exemption_is_file_and_selector_specific(self) -> None:
        for selector in (
            ".paper",
            ".paper-watermark",
            ".paper .resume-name",
            ".paper .paper-section>h2",
            ".paper [contenteditable]:empty::before",
            ".paper .education-school, .paper .experience-title",
        ):
            with self.subTest(selector=selector):
                css = f"{selector} {{font-size:32px; --paper-ink:#fff; color:#123}}"
                paper = self.style(css, "components/ResumePreview.vue")
                self.assert_scan(0)
                paper.unlink()
                other = self.style(css, "other/ResumePreview.vue")
                self.assert_scan(3)
                other.unlink()

    def test_preview_ui_and_mixed_selectors_are_not_paper(self) -> None:
        for selector in (
            ".preview-toolbar",
            ".mode-badge",
            ".modal-title",
            ".toast",
            ".paperclip",
            ".paper-title",
            ".paper-toolbar",
            ".paper-frame",
            ".paper-stage",
            ".paper, .toast",
            ".toast, .paper",
            ":is(.paper, .toast)",
            ":not(.paper)",
            '[data-label=".paper"]',
            ".paper + .toast",
            ".paper ~ .toast",
            ".paper .toast",
        ):
            with self.subTest(selector=selector):
                self.style(
                    f"{selector} {{font-size:99px; --paper-ink:#fff; color:#123}}",
                    "components/ResumePreview.vue",
                )
                self.assert_scan(3)

    def test_paper_custom_properties_on_stage_are_not_general_ui_exemption(self) -> None:
        self.style(
            ".paper-stage {--paper-width:794px; font-size:99px; color:#123; --paper-ink:#fff}",
            "components/ResumePreview.vue",
        )
        self.assert_scan(3)

    def test_nested_selectors_do_not_inherit_parent_exemptions(self) -> None:
        self.style(".paper { & + .toast {font-size:99px} }", "components/ResumePreview.vue")
        self.write("styles.css", ":root { .toast {--ui-ink:#123} }")
        self.assert_scan(2)

    def test_only_global_root_custom_property_definitions_are_exempt(self) -> None:
        self.write(
            "styles.css",
            ":root {--ui-ink:#abc; color:#fff; font-size:99px}\n"
            "@media (prefers-color-scheme: dark) {:root {--ui-ink:#fff}}\n"
            ":root, .toast {--ui-ink:#abc}\n"
            ":root .toast {--ui-ink:#abc}",
        )
        self.assert_scan(4)

    def test_vue_root_and_arbitrary_paper_variables_are_checked(self) -> None:
        for name in ("Fixture.vue", "components/styles.vue", "components/ResumePreview.vue"):
            with self.subTest(name=name):
                path = self.style(":root {--ui-ink:#abc; --paper-ink:#fff; font-size:99px}", name)
                self.assert_scan(3)
                path.unlink()
        self.write("styles.css", ":root {--paper-ink:#abc}")
        self.assert_scan(1)

    def test_comments_cannot_disable_checks(self) -> None:
        self.style(
            "/* eslint-disable design-tokens */\n/* .paper @media print :root */\n.x {font-size:99px}"
        )
        self.assert_scan(1)

    def test_all_vue_style_blocks_and_global_rules_are_scanned(self) -> None:
        self.write(
            "Fixture.vue",
            "<template><div>font-size:99px</div></template>\n"
            "<style scoped>.x {color:#abc}</style>\n"
            '<style media="screen">.x {font-size:99px}</style>',
        )
        self.write("styles.css", ".fixture {font-size:99px}")
        self.assert_scan(3)

    def test_missing_stylesheet_fails_even_with_valid_vue_styles(self) -> None:
        (self.src / "styles.css").unlink()
        self.style(".fixture {color:var(--ui-ink)}")
        self.assertIn("styles.css", "\n".join(self.assert_scan(1)))

    def test_empty_scan_fails(self) -> None:
        for content in ("", "/* only comments */", ".fixture {}"):
            with self.subTest(content=content):
                self.write("styles.css", content)
                self.write("Fixture.vue", "<template><div /></template><style></style>")
                self.assertIn("空扫描", "\n".join(self.assert_scan(1)))

    def test_malformed_css_fails_closed(self) -> None:
        for css in (
            ".x {font-size:calc(99px}",
            '.x {content:"unfinished}',
            ".x {/* unfinished",
            ".x {font-size:99px;",
        ):
            with self.subTest(css=css):
                self.style(css)
                self.assertTrue(any("解析失败" in p for p in self.assert_scan(1)))

    def test_process_exit_codes_use_real_main_and_fixture_files(self) -> None:
        script = Path(checker.__file__).resolve()
        bootstrap = (
            "import runpy,sys; from pathlib import Path; "
            "module=runpy.run_path(sys.argv[1]); "
            "module['main'].__globals__.update(ROOT=Path(sys.argv[2]), "
            "SRC=Path(sys.argv[2])/'frontend'/'src'); "
            "raise SystemExit(module['main']())"
        )
        for css, code in ((".x {font-size:var(--text-base)}", 0), (".x {font-size:99px}", 1)):
            with self.subTest(code=code):
                self.style(css)
                result = subprocess.run(
                    [sys.executable, "-B", "-c", bootstrap, str(script), str(self.root)],
                    cwd=self.root,
                    env={**os.environ, "PYTHONUTF8": "1"},
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(f"问题 {code} 处", result.stdout)
                self.assertEqual(bool(result.stderr), bool(code))


if __name__ == "__main__":
    unittest.main()
