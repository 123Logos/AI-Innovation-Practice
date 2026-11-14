"""面试轮次的作答模式（B11b，`ADR-030` §2.5）。

只有两个取值，所以用 `StrEnum` 而不是裸字符串：`interview_turns.input_mode` 的
`CHECK` 约束、服务层的默认值、前端的展示分支都必须引用同一份定义。

**它不描述音频**。音频在浏览器里转写完就被丢弃（`ADR-030` §2.1 不变量 A），
这一列只回答「这一轮是说的还是打的」。
"""

from __future__ import annotations

from enum import StrEnum


class TurnInputMode(StrEnum):
    """一轮候选人作答是怎么进来的。"""

    #: 打字（默认，也是 B11b 之前全部存量行的取值）。
    TEXT = "text"
    #: 语音：浏览器录音 → 直连 ASR 转写 → 以文本提交。音频本身不留存。
    VOICE = "voice"


#: 迁移 `20261114_0027` 的 `CHECK` 约束取值，与上面的枚举逐字对应，改一处要改两处。
INPUT_MODES: tuple[str, ...] = tuple(mode.value for mode in TurnInputMode)
