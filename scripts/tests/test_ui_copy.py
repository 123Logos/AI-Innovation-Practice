"""G1 文案闸的判据（`scripts/check_ui_copy.py`）。

这份单测是 2026-11-12 补的，起因是一次反证：PR #30 的 CI 红在
`frontend/src/stores/resume.ts` 的「网址最长 500 个字符」上（`500` 被读成裸状态码），
改文案时顺手在注释里写了那串豁免标记的字面量，结果**豁免数从 11 变成 12、闸照样绿**。

也就是说：那道闸只要有人「提到」标记就能被关掉，它在此之前发的都是假证书。
这里钉住三件事——真指令照旧豁免、散文提到不算、没理由的豁免不算。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.check_ui_copy import collect


def run(*lines: str) -> tuple[list[str], int]:
    """按 .ts 文件跑一遍（没有模板区，最省事的形态）。"""
    return collect(list(lines), is_vue=False)


class BareStatusCodeTest(unittest.TestCase):
    """C2：中文文案里的三位 4xx/5xx。"""

    def test_flags_limit_number_in_copy(self) -> None:
        """「最长 500 个字符」会被判成状态码——PR #30 的 CI 红就是这一条。"""
        problems, _ = run("      if (url.length > 500) return `${label}的网址最长 500 个字符。`")
        self.assertEqual(len(problems), 1)
        self.assertIn("裸状态码 `500`", problems[0])

    def test_same_limit_without_number_passes(self) -> None:
        """改说法（不写数字）之后同一行不再违约——修的是文案，不是闸。"""
        problems, _ = run(
            "      if (url.length > 500) return `${label}的网址太长了，请换一个更短的链接。`"
        )
        self.assertEqual(problems, [])

    def test_four_digit_number_is_not_a_status_code(self) -> None:
        """2000 这类四位上限不该被误判：正则刻意只认三位。"""
        problems, _ = run("      return `${label}的描述最长 2000 个字符。`")
        self.assertEqual(problems, [])


class AllowMarkerTest(unittest.TestCase):
    """豁免标记的语义：只有注释开头的指令算，散文提到不算。"""

    def test_directive_marker_still_exempts(self) -> None:
        """既有 11 处豁免的写法必须照旧生效，否则这次收紧就是把真豁免也砍了。"""
        problems, exempted = run(
            "// ui-copy:allow C2 唯一豁免：用户得自己去那个界面粘凭据",
            'const hint = "请到 AI 设置里粘贴 DeepSeek API Key"',
        )
        self.assertEqual(problems, [])
        self.assertEqual(exempted, 1)

    def test_prose_mention_does_not_exempt(self) -> None:
        """句子里顺嘴提到标记，不许关掉下面三行——这就是那个假证书。"""
        problems, exempted = run(
            "// 注意：不要用 ui-copy:allow 这种标记绕闸，那是换绿不是修文案",
            'const hint = "请到 AI 设置里粘贴 DeepSeek API Key"',
        )
        self.assertEqual(exempted, 0)
        self.assertEqual(len(problems), 1)

    def test_marker_without_reason_is_a_problem(self) -> None:
        """没写理由的豁免不算豁免，要自己报出来（豁免本身也要有账）。"""
        problems, exempted = run(
            "// ui-copy:allow",
            'const hint = "请到 AI 设置里粘贴 DeepSeek API Key"',
        )
        self.assertEqual(exempted, 0)
        self.assertTrue(any("豁免没有理由" in item for item in problems))

    def test_marker_far_above_does_not_reach(self) -> None:
        """豁免只管往上一行到三行内，隔远了不能顺手豁免别处。"""
        problems, exempted = run(
            "// ui-copy:allow C2 唯一豁免：用户得自己去那个界面粘凭据",
            "const a = 1",
            "const b = 2",
            "const c = 3",
            'const hint = "请到 AI 设置里粘贴 DeepSeek API Key"',
        )
        self.assertEqual(exempted, 0)
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
