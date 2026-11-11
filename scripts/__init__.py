"""仓库自检脚本（G1/G3/G4/G5 与文档引用闸）。

这个 `__init__.py` 有且只有一个作用：让 `scripts` 成为一个**普通包**，
使 `mypy app scripts` 与 `scripts/tests/` 里的 `from scripts import check_design_tokens`
解析到**同一个模块名**。少了它，mypy 会同时看到 `check_design_tokens` 与
`scripts.check_design_tokens` 两个名字，直接以
「Source file found twice under different module names」拒绝检查——那不是风格问题，
是整道类型闸在 CI 里失效。

脚本本身仍然可以直接跑：`python scripts/check_ui_copy.py`（`__main__` 路径不受影响）。
"""
