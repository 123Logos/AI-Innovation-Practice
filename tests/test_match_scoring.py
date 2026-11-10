"""`app/services/match_scoring.py` 的单元测试（ADR-013 §7/§8，落地顺序 7a）。

这一层是纯函数，所以这里**不碰数据库、不碰 HTTP**：五项分项、片段定位、四桶核验、
权重归一全部用值来测。集成侧（读库 + 调模型）的用例在 `tests/test_job_match.py`，
两边的断言不混在一起——混了之后失败原因读不出来。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.skill_names import (
    ALIAS_SCOPE_ROLE,
    ALIAS_SCOPE_SKILL,
    SKILL_ALIAS_VERSION,
    SKILL_MATCHER_VERSION,
    AliasIndex,
    skill_forms_match,
    skill_name_key,
)
from app.services.match_scoring import (
    EVIDENCE_MODE_AI,
    EVIDENCE_MODE_KEYWORD,
    KEYWORD_FALLBACK_HITS_CAP,
    MATCH_SNIPPET_RADIUS,
    MATCH_WEIGHTS,
    MATCH_WEIGHTS_VERSION,
    MAX_SNIPPETS_PER_SKILL,
    PARTIAL_STRENGTH_THRESHOLD,
    PREFERENCE_NOTE_ALIAS_MATCH,
    PREFERENCE_NOTE_BELOW_THRESHOLD,
    PREFERENCE_NOTE_CONFLICT,
    PREFERENCE_TOKEN_RATIO,
    AiScoring,
    Candidate,
    ExperienceText,
    MatchFacts,
    ModelOutcome,
    RequiredSkill,
    Term,
    apply_model_scores,
    build_evidence,
    fold_with_positions,
    keyword_strength,
    locate_snippets,
    matched_required_skills,
    matched_with_forms,
    normalize_score,
    overlap_ratio,
    remaining_time_value,
    score_match,
    tokens,
    unique_required,
)

TODAY = date(2026, 11, 7)


def alias_index(*pairs: tuple[str, str], scope: str = ALIAS_SCOPE_SKILL) -> AliasIndex:
    """按 `ADR-016` §2.2 的存储口径造索引：存的是**归一后的键**，不是原始写法。"""
    return AliasIndex.from_rows([(skill_name_key(a), skill_name_key(b), scope) for a, b in pairs])


def experience(experience_id: str, **text: str) -> ExperienceText:
    return ExperienceText(
        id=experience_id,
        title=text.get("title", ""),
        description=text.get("description", ""),
        responsibility=text.get("responsibility", ""),
    )


def facts(
    *,
    job_title: str = "Python 后端开发工程师",
    required: tuple[RequiredSkill, ...] = (RequiredSkill("Python", "hard"),),
    student: tuple[str, ...] = ("Python",),
    experiences: tuple[ExperienceText, ...] = (),
    target_role: str | None = None,
    deadline: date | None = None,
    aliases: AliasIndex | None = None,
) -> MatchFacts:
    return MatchFacts(
        job_title=job_title,
        required_skills=required,
        student_skills=student,
        experiences=experiences,
        target_role=target_role,
        deadline=deadline,
        today=TODAY,
        # 没传就是**空词表**（§5.1 的「空表 = 纯词面行为」）。`MatchFacts.aliases` 的默认值
        # 也是同一个空索引，所以这里显式传与省略传，读数必须逐字相同——那条由
        # `TestAliasLayer.test_an_absent_index_and_an_empty_index_read_identically` 钉住。
        aliases=aliases if aliases is not None else AliasIndex.empty(),
    )


def term(match, name: str):
    return next(item for item in match.terms if item.name == name)


# --------------------------------------------------------------------- 片段定位


class TestLocateSnippets:
    """§3 的反幻觉闸门：片段必须是**原文的连续子串**。"""

    def test_snippet_is_a_contiguous_substring_of_the_original_text(self) -> None:
        text = "负责订单服务的重构，使用 Python + FastAPI 把 P99 从 800ms 降到 120ms。"
        found = locate_snippets(text, "Python")
        assert found, "命中了却没返回片段"
        for snippet, _ in found:
            assert snippet in text

    def test_case_and_fullwidth_differences_still_hit(self) -> None:
        text = "用 ＰＹＴＨＯＮ 写过爬虫"
        found = locate_snippets(text, "python")
        assert [snippet for snippet, _ in found] == [text]

    def test_window_is_centered_on_the_hit_and_flags_truncation(self) -> None:
        filler = "字" * 300
        text = f"{filler}用 Python 做完交付{filler}"
        ((snippet, was_cut),) = locate_snippets(text, "Python")
        assert "Python" in snippet
        assert was_cut is True
        # 窗口是「命中位置前后各 MATCH_SNIPPET_RADIUS 个**原文**字符」，
        # 中心是被搜到的那串技能名本身，不是它周围的中文。
        at = text.find("Python")
        assert (
            snippet == text[at - MATCH_SNIPPET_RADIUS : at + len("Python") + MATCH_SNIPPET_RADIUS]
        )
        assert len(snippet) == 2 * MATCH_SNIPPET_RADIUS + len("Python")

    def test_short_text_is_returned_whole_and_not_marked_cut(self) -> None:
        ((snippet, was_cut),) = locate_snippets("用 Python 做完交付", "Python")
        assert snippet == "用 Python 做完交付"
        assert was_cut is False

    def test_no_hit_returns_nothing(self) -> None:
        assert locate_snippets("只写过 Java", "Python") == ()
        assert locate_snippets("", "Python") == ()
        assert locate_snippets("Python", "") == ()

    def test_repeated_hit_yields_several_snippets(self) -> None:
        text = "Python 入门" + "。" * 200 + "Python 进阶"
        assert len(locate_snippets(text, "Python")) == 2

    def test_fold_positions_round_trip_to_original_indices(self) -> None:
        text = "用 Ｐython 写"
        folded, positions = fold_with_positions(text)
        assert folded == "用 python 写"
        # 折叠后每个字符都必须指回原文一个真实下标，且单调不减。
        assert all(0 <= index < len(text) for index in positions)
        assert positions == sorted(positions)

    # --- 写法等价与词边界（ADR-013 §1.3 的 2026-11-08 更正）-------------------

    @pytest.mark.parametrize(
        ("required", "text", "expected_hits"),
        [
            # 岗位要求带版本号、正文写的是 `Python 3.13`：整串子搜会 0 命中，
            # 而老板 2026-11-08 实测到的正是这一格——覆盖修好了也仍走「证据 0 分」。
            ("Python3", "后端采用 Python 3.13 与 FastAPI", 1),
            # 反过来：岗位要求不带号、正文带号。
            ("Python", "后端采用 Python 3.13", 1),
            # 主号不同不算：这一段是 Python2 的代码，不能替 Python3 作证。
            ("Python3", "维护过一段 Python2 的老代码", 0),
            # 词边界：`Java` 不许在 `JavaScript` 里被找到，`C` 不许在 `C++` / `C#` 里被找到。
            ("Java", "用过 JavaScript", 0),
            ("C", "用过 C++ 与 C#", 0),
            ("C++", "用过 C++ 与 C#", 1),
            # 中文紧挨着不算越界：`C语言`、`Python编程` 都是真命中。
            ("C", "熟悉 C语言 与 数据结构", 1),
            # 缩写不是别名（L1 词表暂缓），所以 `Go` 不许命中 `Golang`。
            ("Go", "用 Golang 写过服务", 0),
        ],
    )
    def test_version_and_word_boundary_rules(
        self, required: str, text: str, expected_hits: int
    ) -> None:
        assert len(locate_snippets(text, required)) == expected_hits

    def test_matched_surface_stays_inside_the_returned_snippet(self) -> None:
        """片段仍然必须是原文连续子串（反幻觉闸门没因为新的并进逻辑而松动）。"""
        text = "后端采用 Python 3.13 与 FastAPI"
        ((snippet, was_cut),) = locate_snippets(text, "Python3")
        assert snippet == text
        assert was_cut is False


# ------------------------------------------------------------------- 命中集合


class TestMatchedRequiredSkills:
    def test_hit_is_keyed_not_literal(self) -> None:
        matched = matched_required_skills(
            [RequiredSkill("python"), RequiredSkill("Go")], ["PYTHON", "docker"]
        )
        assert [item.name for item in matched] == ["python"]

    def test_same_key_written_twice_on_job_side_counts_once(self) -> None:
        """分母不能被「Python / python」这种重复写法抬高（§7.1 的 `|key(R)|`、分子分母同一口径）。"""
        required = [RequiredSkill("Python"), RequiredSkill("python")]
        unique = unique_required(required)
        assert [item.name for item in unique] == ["Python"]
        assert len(matched_required_skills(unique, ["PYTHON"])) == 1
        match = score_match(facts(required=tuple(required), student=("PYTHON",)))
        assert term(match, "coverage").value == 1.0

    def test_distinct_skills_are_not_merged(self) -> None:
        """去重不能顺把 C# 和 C 合成一个（`skill_name_key` 不去标点的理由在这里生效）。"""
        assert len(unique_required([RequiredSkill("C#"), RequiredSkill("C")])) == 2

    # --- 写法等价（ADR-013 §1.3 的 2026-11-08 更正）---------------------------

    #: `(岗位写法, 学生写法, 该不该算同一个技能)`。每一行都**双向**测，
    #: 因为「岗位带号」与「学生带号」命中的是不同分支，只测单向会漏掉一半。
    @pytest.mark.parametrize(
        ("left", "right", "same_skill"),
        [
            # 该认的：纯粹是版本号写法之差，与领域无关（财务的 SPSS 26、机械的 AutoCAD 2022 同理）。
            ("Python", "Python3", True),
            ("Python", "Python 3.13", True),
            ("Python3", "Python 3.13", True),
            ("C++", "C++11", True),
            ("SQL Server", "SQL Server 2019", True),
            ("Excel", "Excel2016", True),
            # 必须判不同的：允许前缀/子串推断，这些全会被误并。
            ("C", "C++", False),
            ("C", "C#", False),
            ("C++", "C#", False),
            ("Java", "JavaScript", False),
            ("SQL", "MySQL", False),
            ("Go", "Golang", False),
            ("R", "React", False),
            # 主版本号不同就是两件事（这条是「Python3 认 Python」不被推成「什么号都认」的边界）。
            ("Python2", "Python3", False),
            ("Vue 2", "Vue 3", False),
            # 非计算机领域的反例（老板 ③：别名表暂缓，这里只钉住「不许误并」这一半）。
            ("会计", "审计", False),
            ("护理", "助产", False),
            ("平面设计", "室内设计", False),
            ("市场营销", "营销", False),
        ],
    )
    def test_equivalence_table(self, left: str, right: str, same_skill: bool) -> None:
        assert skill_forms_match(left, right) is same_skill
        assert skill_forms_match(right, left) is same_skill
        matched = matched_required_skills([RequiredSkill(left)], [right])
        assert bool(matched) is same_skill

    def test_coverage_counts_the_versioned_spelling(self) -> None:
        """老板 2026-11-08 那一屏的回归：技能 `Python3` 该顶住岗位要求 `Python`。"""
        match = score_match(
            facts(required=(RequiredSkill("Python"), RequiredSkill("Django")), student=("Python3",))
        )
        assert term(match, "coverage").value == 0.5

    def test_projection_reports_the_students_own_spelling(self) -> None:
        """第 3c 条要用「对面写的是哪个写法」，不是我们再猜一个规范名。"""
        rows = matched_with_forms([RequiredSkill("Python")], ["Python3"])
        assert [(item.name, form) for item, form in rows] == [("Python", "Python3")]

    def test_projection_takes_the_first_hit_not_the_neatest(self) -> None:
        """可复现性优先：「第一个」是录入顺序决定的，「最好的」需要再造一套排序判据。"""
        rows = matched_with_forms([RequiredSkill("Python")], ["Python 3.13", "PYTHON"])
        assert rows[0][1] == "Python 3.13"

    def test_both_projections_come_from_the_same_loop(self) -> None:
        """`matched_required_skills` 必须是 `matched_with_forms` 的投影，不是第二份判据。

        这条不是形式主义：第 3c 条与匹配一旦各走一份判据，同屏一致性就只能是巧合，
        而老板 2026-11-08 定的验收标准恰恰是「同屏一致性 100%」。
        """
        required = [
            RequiredSkill("Python"),
            RequiredSkill("C"),
            RequiredSkill("Go"),
            RequiredSkill("Docker"),
        ]
        student = ("Python3", "C++", "用 Golang 写过服务", " ")
        assert [item for item, _ in matched_with_forms(required, student)] == list(
            matched_required_skills(required, student)
        )


# ------------------------------------------------------------------ 证据构建


class TestBuildEvidence:
    def test_hits_count_experiences_not_occurrences(self) -> None:
        """同一句话里写三遍 Python 不该刷分（§7.2）。"""
        item = experience("e1", description="Python Python Python，都是 Python")
        bundle = build_evidence(facts(experiences=(item,)), (RequiredSkill("Python"),))
        assert bundle.experience_hits["Python"] == 1
        # 一条 (技能, 经历, 字段) 最多一个窗口。
        assert len(bundle.candidates) == 1

    def test_field_order_is_title_description_responsibility(self) -> None:
        item = experience(
            "e1",
            title="Python 项目",
            description="用了 Python",
            responsibility="负责 Python 模块",
        )
        bundle = build_evidence(facts(experiences=(item,)), (RequiredSkill("Python"),))
        assert [candidate.field for candidate in bundle.candidates] == [
            "title",
            "description",
            "responsibility",
        ]
        assert [candidate.fragment_id for candidate in bundle.candidates] == [0, 1, 2]

    def test_two_experiences_give_two_hits_and_two_fragments(self) -> None:
        items = (
            experience("e1", description="做过 Python"),
            experience("e2", responsibility="用 Python 值班"),
        )
        bundle = build_evidence(facts(experiences=items), (RequiredSkill("Python"),))
        assert bundle.experience_hits["Python"] == 2
        assert len(bundle.candidates) == 2

    def test_truncation_is_counted(self) -> None:
        long_text = "字" * 300 + "Python" + "。" * 300
        item = experience("e1", description=long_text)
        bundle = build_evidence(facts(experiences=(item,)), (RequiredSkill("Python"),))
        assert bundle.fragments_truncated == 1


# -------------------------------------------------------------- 四桶核验（§8）


def candidate(skill: str, fragment_id: int) -> Candidate:
    return Candidate(
        fragment_id=fragment_id,
        skill=skill,
        experience_id="e1",
        field="description",
        snippet=f"{skill} 的片段",
    )


class TestApplyModelScores:
    def test_happy_path_verifies_everything(self) -> None:
        items = [candidate("Python", 0), candidate("Go", 1)]
        outcome = apply_model_scores(
            items,
            [
                {"fragment_id": 0, "skill": "Python", "strength": 0.9},
                {"fragment_id": 1, "skill": "Go", "strength": 0.4},
            ],
        )
        assert outcome.verified_pairs == 2
        assert outcome.strengths == {0: 0.9, 1: 0.4}
        assert (
            outcome.verified_pairs
            + outcome.dropped_unreferenced
            + outcome.dropped_unknown_skill
            + outcome.dropped_missing_output
            == len(items)
        )

    def test_unknown_skill_name_is_dropped_first(self) -> None:
        """§8 的次序：先技能名后编号。这条若不成立，两个计数器的值会互相串。"""
        outcome = apply_model_scores(
            [candidate("Python", 0)],
            [{"fragment_id": 0, "skill": "Rust", "strength": 0.5}],
        )
        assert outcome.dropped_unknown_skill == 1
        assert outcome.dropped_unreferenced == 0

    def test_reference_we_never_issued_is_dropped(self) -> None:
        outcome = apply_model_scores(
            [candidate("Python", 0)],
            [{"fragment_id": 99, "skill": "Python", "strength": 0.5}],
        )
        assert outcome.dropped_unreferenced == 1

    def test_real_id_wrong_skill_pairing_counts_as_bad_reference(self) -> None:
        outcome = apply_model_scores(
            [candidate("Python", 0), candidate("Go", 1)],
            [{"fragment_id": 0, "skill": "Go", "strength": 0.5}],
        )
        assert outcome.dropped_unknown_skill == 0
        assert outcome.dropped_unreferenced == 1

    def test_strength_out_of_range_is_clamped_and_counted(self) -> None:
        outcome = apply_model_scores(
            [candidate("Python", 0)],
            [{"fragment_id": 0, "skill": "Python", "strength": 1.7}],
        )
        assert outcome.strengths == {0: 1.0}
        assert outcome.clamped_strength == 1

    def test_boolean_strength_is_not_a_number(self) -> None:
        """`True` 是 `int` 的子类，`isinstance(True, int)` 恒真；不挡住就会当成 1.0 打分。"""
        outcome = apply_model_scores(
            [candidate("Python", 0)],
            [{"fragment_id": 0, "skill": "Python", "strength": True}],
        )
        assert outcome.strengths == {}
        assert outcome.dropped_missing_output == 1

    def test_pair_model_simply_omitted_is_counted_as_missing(self) -> None:
        """§13 复核第 6 条：模型少回条目时必须有桶装它，否则候选侧的等式不闭合。"""
        items = [candidate("Python", 0), candidate("Go", 1), candidate("SQL", 2)]
        outcome = apply_model_scores(
            items,
            [
                {"fragment_id": 0, "skill": "Python", "strength": 0.6},
                {"fragment_id": 1, "skill": "Kotlin", "strength": 0.6},  # 未知技能
            ],
        )
        assert outcome.verified_pairs == 1
        assert outcome.dropped_unknown_skill == 1
        assert outcome.dropped_missing_output == 2  # Go 与 SQL 都没拿到评分
        assert outcome.verified_pairs + outcome.dropped_missing_output == len(items)

    def test_invented_items_do_not_break_the_candidate_partition(self) -> None:
        """模型多编条目只影响**条目侧**分区；候选侧等式与 junk 数无关。

        这条就是原稿那个「四个 dropped 相加 = candidate_pairs」把两种单位混进一个式子的地方，
        实测一跑就红，所以两个等式分开写、分开测（§8 更正版）。
        """
        items = [candidate("Python", 0)]
        raw = [
            {"fragment_id": 0, "skill": "Python", "strength": 0.5},
            {"fragment_id": 7, "skill": "Python", "strength": 0.5},  # 编号是我们没发过的
            {"fragment_id": 0, "skill": "Cobol", "strength": 0.5},  # 技能不在命中集合里
            {"fragment_id": 0, "skill": "Python", "strength": "很高"},  # 评分不可用
        ]
        outcome = apply_model_scores(items, raw)
        assert outcome.verified_pairs + outcome.dropped_missing_output == len(items)
        assert (
            len(raw)
            == outcome.verified_pairs
            + outcome.dropped_unreferenced
            + outcome.dropped_unknown_skill
            + outcome.dropped_unusable_strength
        )
        assert (outcome.dropped_unreferenced, outcome.dropped_unknown_skill) == (1, 1)
        assert outcome.dropped_unusable_strength == 1

    def test_integer_fragment_ids_only_never_a_bool(self) -> None:
        """`True` 能当 dict 键命中编号 1；不挡住就会把假编号认成第 2 个候选对。"""
        outcome = apply_model_scores(
            [candidate("Python", 0), candidate("Go", 1)],
            [{"fragment_id": True, "skill": "Go", "strength": 0.9}],
        )
        assert outcome.verified_pairs == 0
        assert outcome.dropped_unreferenced == 1


# ------------------------------------------------------------------ 兜底强度


class TestKeywordStrength:
    def test_saturates_at_the_cap(self) -> None:
        assert keyword_strength(0) == 0.0
        assert keyword_strength(1) == pytest.approx(1 / 3)
        assert keyword_strength(KEYWORD_FALLBACK_HITS_CAP) == 1.0
        assert keyword_strength(99) == 1.0


# ---------------------------------------------------------------- 权重归一


class TestNormalizeScore:
    def test_only_computed_terms_enter_the_denominator(self) -> None:
        terms = [
            Term("a", 0.5, 1.0, True),
            Term("b", 0.3, 0.0, True),
            Term("c", 0.2, None, False),
        ]
        score, used = normalize_score(terms)
        assert used == 0.8
        assert score == pytest.approx(0.5 / 0.8, abs=0.001)

    def test_a_zero_value_is_not_the_same_as_not_computed(self) -> None:
        """`computed=False` 完全不进分母；`value=0.0, computed=True` 会把总分拉下来。"""
        excluded, _ = normalize_score([Term("a", 0.5, 1.0, True), Term("b", 0.5, None, False)])
        penalized, _ = normalize_score([Term("a", 0.5, 1.0, True), Term("b", 0.5, 0.0, True)])
        assert excluded == 1.0
        assert penalized == 0.5

    def test_nothing_computed_is_null_not_zero(self) -> None:
        """F1:A（老板 2026-11-08 裁定）：分母为 0 时是「算不出来」，不是 0 分。

        原来这一条钉的是 `(0.0, 0.0)`，而那个 0 恰好是 §5.1 否决「缺项记 0 分」时引用的现象
        ——新注册、什么都没填的人稳定看到 0%。两头不能同时成立，所以改的是实现。
        """
        assert normalize_score([Term("a", 0.5, None, False)]) == (None, 0.0)

    def test_zero_coverage_is_still_a_score(self) -> None:
        """与上一条配对：`computed=True` 且值为 0 必须仍然给分数，否则 F1 会被过度解读。"""
        assert normalize_score([Term("coverage", 0.45, 0.0, True)]) == (0.0, 0.45)


# ------------------------------------------------------- 剩余投递时间（§7.5）


class TestRemainingTime:
    @pytest.mark.parametrize(
        ("deadline", "expected_value", "expected_computed", "expected_note"),
        [
            (None, None, False, "no_deadline"),
            (date(2026, 11, 6), 0.0, True, "expired"),
            (TODAY, 0.4, True, "urgent"),
            (date(2026, 11, 14), 0.4, True, "urgent"),
            (date(2026, 11, 15), 0.7, True, "near"),
            (date(2026, 12, 7), 0.7, True, "near"),
            (date(2026, 12, 8), 1.0, True, "open"),
        ],
    )
    def test_ladder(self, deadline, expected_value, expected_computed, expected_note) -> None:
        assert remaining_time_value(deadline, TODAY) == (
            expected_value,
            expected_computed,
            expected_note,
        )

    def test_deadline_today_is_not_expired(self) -> None:
        """与列表页「今天截止」不显示过期同一判据（core/deadline）。"""
        assert remaining_time_value(TODAY, TODAY)[2] == "urgent"


# ---------------------------------------------------------- 目标偏好（§7.4）


class TestPreference:
    def test_missing_target_role_is_excluded_not_zero(self) -> None:
        match = score_match(facts(target_role=None))
        item = term(match, "preference")
        assert item.computed is False
        assert item.note == "no_target_role"

    @pytest.mark.parametrize(
        "role",
        ["Python 后端开发工程师", "python 后端开发", "后端开发工程师"],
    )
    def test_containment_either_way_is_full_credit(self, role) -> None:
        match = score_match(facts(target_role=role))
        assert term(match, "preference").value == 1.0

    def test_conflicting_target_is_zero_and_still_in_the_denominator(self) -> None:
        """老板 2026-11-08 裁定：`0.2` 那一档删掉，「不相干」拆成两格。

        这里是**明确冲突**那格（词面零重合）：`computed=True` + `value=0.0`，也就是真的参与加权、
        真的扣分。原来的 0.2 是「缺项记 0 分」的变体——它用一个没有依据的软分数把「不同方向」这件真事
        稀释成一点点扣分。免加权只留给下一款那种「没有可比的内容」。
        """
        match = score_match(facts(target_role="想做游戏策划"))
        item = term(match, "preference")
        assert item.value == 0.0
        assert item.computed is True
        assert item.note == "preference_conflict"

    def test_weak_overlap_below_the_ratio_line_is_below_threshold_not_conflict(self) -> None:
        """`0 < overlap_ratio < 0.5`：**分数与冲突完全相同，原因码必须不同**（老板 2026-11-09 裁定）。

        这一格原先我自己归边归进了「冲突」，实测它有多小：dev 库 7 个岗位标题 × 10 条常见目标岗位
        = 70 对里只有 1 对落进来（`Python 后端开发工程师` vs `前端开发实习生`，重合 0.333，只共用
        「开发」这一个二元组）。归边判分没错，但把标签叫「冲突」是错的：它不是方向相反，
        是词面信息不够，修它的是阶段 2 的 L1 别名表，不是让用户改目标岗位。
        所以两个码同值同加权、只分原因——**不许因为换了码就逃进免加权**。
        """
        match = score_match(facts(job_title="前端开发实习生", target_role="Python 后端开发工程师"))
        ratio = overlap_ratio("Python 后端开发工程师", "前端开发实习生")
        assert 0.0 < ratio < PREFERENCE_TOKEN_RATIO
        item = term(match, "preference")
        assert item.value == 0.0
        assert item.computed is True
        assert item.note == "preference_below_threshold"

    def test_the_two_zero_codes_cost_exactly_the_same(self) -> None:
        """拆码只拆语义，不许拆出分数差：两格的 `score` 必须逐位相同。"""
        conflict = score_match(facts(target_role="想做游戏策划"))
        below = score_match(facts(job_title="前端开发实习生", target_role="Python 后端开发工程师"))
        assert term(conflict, "preference").value == term(below, "preference").value == 0.0
        assert conflict.score == below.score
        assert conflict.meta.weight_sum_used == below.meta.weight_sum_used

    def test_partial_overlap_gets_middle_credit(self) -> None:
        """不相干但词面确实重合：0.6，而不是「看不懂就归零」。"""
        match = score_match(facts(job_title="数据分析实习生", target_role="分析 数据 报表"))
        item = term(match, "preference")
        assert item.value == 0.6
        assert item.note == "token_overlap"


# ------------------------------------------------------------ 词面切分


class TestTokens:
    def test_cjk_bigrams_give_partial_credit_where_whole_string_gave_zero(self) -> None:
        """§7.3 原稿「中文整串当一个 token」实测 0/40；二元组是它的最小修正。"""
        assert tokens("大模型算法实习生") != {"大模型算法实习生"}
        assert overlap_ratio("大模型算法实习生", "大模型算法") > 0.5

    def test_latin_runs_are_single_tokens(self) -> None:
        assert {"fastapi", "python"} <= tokens("用 FastAPI 和 Python")

    def test_empty_input_is_zero_overlap(self) -> None:
        assert overlap_ratio("", "Python") == 0.0
        assert tokens("！！！") == frozenset()


# ------------------------------------------------------- 装配：score_match


class TestScoreMatchContract:
    def test_weights_are_exposed_with_a_version(self) -> None:
        match = score_match(facts())
        assert match.weights_version == MATCH_WEIGHTS_VERSION
        assert {item.name: item.weight for item in match.terms} == MATCH_WEIGHTS

    def test_matcher_version_is_reported_and_shared(self) -> None:
        """`meta.matcher_version` 与写法判据同源，是第 3c 条一致性断言的另一半。

        断言它等于常量而不是等于某个字面量：这样「改了判据忘了升版本」仍会被
        `app/core/skill_names.py` 里那条规则抓住（升版本才让这条测试继续有意义）。
        """
        assert score_match(facts()).meta.matcher_version == SKILL_MATCHER_VERSION

    def test_no_required_skills_is_not_evaluated_even_when_other_terms_compute(self) -> None:
        """老板 2026-11-08 裁定的直接回归锁：**核心 term 不在 ⇒ 整个不给分**。

        这一格就是那条被真浏览器量出来的荒谬：岗位没填任何要求技能，但用户填了目标岗位
        且标题逐字相同 ⇒ `preference` 单独参与、分母只剩 0.10，重归一把它放大成 **1.0**，
        屏幕上写着「100% 匹配度」，同一屏另一行写着「没有可比对的条目」。
        现在它必须是 `null`。同时 `terms[]` 与 `weight_sum_used` **不许被抹平**——
        「算出来了但没用于评分」是对账要看见的事实，抹掉它就成了另一种掩耳盗铃。
        """
        match = score_match(facts(required=(), student=(), target_role="Python 后端开发工程师"))
        assert match.score is None
        assert match.meta.note == "not_evaluated"
        assert match.meta.no_required_skills is True
        assert term(match, "preference").computed is True
        assert term(match, "preference").value == 1.0
        assert match.meta.weight_sum_used == 0.1

    def test_score_is_null_and_note_set_when_nothing_is_computable(self) -> None:
        """五项全免加权那一格同样不给分（F1:A 的原始角落，现在被上一款的规则覆盖）。"""
        match = score_match(facts(required=(), student=()))
        assert match.score is None
        assert match.meta.weight_sum_used == 0.0
        assert match.meta.note == "not_evaluated"

    def test_zero_coverage_still_has_a_score_and_no_note(self) -> None:
        """上一条件的对照：有项可算、算出来是 0，仍然是分数。

        少了这条，`score is None` 与 `score == 0.0` 的分工就只是我的一句话。
        """
        match = score_match(facts(required=(RequiredSkill("Docker"),), student=()))
        assert term(match, "coverage").value == 0.0
        assert match.score == 0.0
        assert match.meta.note is None

    def test_score_is_null_exactly_when_the_job_has_no_required_skills(self) -> None:
        """枚举 81 个组合证明**双向**等价：`score is None` ⇔ 岗位没填要求技能。

        2026-11-08 裁定之前这里只有单向（`⇒`），而反方向不成立正是那一格荒谬的来源
        （`required=()` + 填了偏好 ⇒ 给出 1.0）。现在两边焊死，所以：

        1. 前端可以为 `null` 而不派活——那一屏必然写着「岗位没填技能要求」；
        2. `0.0` 只可能来自**真的有要求技能但一条没对上**，`null` 永不会被读成「你不行」。
        """
        seen_null = 0
        for required in ((), (RequiredSkill("Python", "hard"),), (RequiredSkill("Rust"),)):
            for student in ((), ("Python",), ("Python 3.13",)):
                for target_role in (None, "Python 后端开发工程师", "运营助理"):
                    for deadline in (None, TODAY, date(2099, 1, 1)):
                        scored = score_match(
                            facts(
                                required=required,
                                student=student,
                                target_role=target_role,
                                deadline=deadline,
                            )
                        )
                        assert (scored.score is None) == (not required), (
                            required,
                            student,
                            target_role,
                            deadline,
                            scored.score,
                        )
                        if scored.score is None:
                            seen_null += 1
                            assert scored.meta.note == "not_evaluated"
                        else:
                            assert scored.meta.note is None
        assert seen_null > 0, "枚举没覆盖到 null 那一格，这条测试就是空跑"

    def test_relevance_never_enters_the_score_in_v1(self) -> None:
        """§7.3 的代理指标实测 0/40，所以 v1 显式不参与；而且必须能被对账。

        这里把偏好与截止都填上，让「分母只少了 relevance 一项」这件事单独可见——
        否则它会被另两项免加权挡住，数字同样变 0.65，但原因完全不同。
        """
        match = score_match(facts(target_role="Python 后端开发工程师", deadline=date(2026, 12, 8)))
        item = term(match, "relevance")
        assert item.computed is False
        assert item.note == "not_measurable_v1"
        assert term(match, "preference").computed is True
        assert term(match, "remaining_time").computed is True
        assert match.meta.weight_sum_used == pytest.approx(1.0 - MATCH_WEIGHTS["relevance"])

    def test_full_coverage_and_strong_evidence(self) -> None:
        """两条经历 = 0.667，两种模式下都算「证据站得住」；片段全部回显给用户。"""
        items = (
            experience("e1", description="用 Python 重写订单服务，P99 降了 60%"),
            experience("e2", responsibility="用 Python 写了对账脚本"),
        )
        match = score_match(
            facts(
                required=(RequiredSkill("Python", "hard"),),
                student=("Python",),
                experiences=items,
            )
        )
        assert term(match, "coverage").value == 1.0
        assert match.meta.evidence_mode == EVIDENCE_MODE_KEYWORD
        assert [covered.skill for covered in match.covered] == ["Python"]
        assert len(match.covered[0].evidence) == 2
        assert match.gaps == ()

    def test_one_real_mention_is_covered_in_keyword_mode(self) -> None:
        """阈值 0.4 → 0.3 的理由就在这条用例上：一段经历是常态，不是弱点。

        关键词模式测的是「有没有一条真实出处」，有出处就进 `covered` 并把片段给用户看；
        「提到名字但没做过」只有 AI 模式分得出来，所以下一条用例改用 0.2 的 AI 评分触发 partial。
        """
        item = experience("e1", description="接触过 Python")
        match = score_match(facts(experiences=(item,)))
        assert keyword_strength(1) > PARTIAL_STRENGTH_THRESHOLD
        assert [covered.skill for covered in match.covered] == ["Python"]
        assert match.partial == ()

    def test_weak_ai_judgement_moves_a_skill_into_partial(self) -> None:
        """AI 给 0.2（Prompt 里那一档 =「只是提到名字」）→ 证据偏薄，进 partial。"""
        item = experience("e1", description="接触过 Python")
        facts_ = facts(experiences=(item,))
        bundle = build_evidence(facts_, (RequiredSkill("Python"),))
        outcome = ModelOutcome(
            strengths={0: 0.2},
            verified_pairs=1,
            dropped_unreferenced=0,
            dropped_unknown_skill=0,
            dropped_unusable_strength=0,
            dropped_missing_output=0,
            clamped_strength=0,
        )
        match = score_match(
            facts_,
            bundle,
            AiScoring(model="deepseek-flash", prompt_version="v1", outcome=outcome),
        )
        assert match.covered == ()
        assert [partial.skill for partial in match.partial] == ["Python"]

    def test_matched_name_without_any_evidence_is_a_real_zero(self) -> None:
        match = score_match(facts(experiences=()))
        item = term(match, "evidence")
        assert item.computed is True
        assert item.value == 0.0
        assert item.note == "matched_without_evidence"
        # `partial` 这一格是集成测试逼出来的（`test_job_match.py` 里 Docker 命中却零片段，
        # 三档清单当时全空）：它既不是 covered 也不是 gap，两头都不算就等于对用户隐身。
        assert [partial.skill for partial in match.partial] == ["Python"]

    def test_no_required_skills_excludes_coverage(self) -> None:
        match = score_match(facts(required=(), student=("Python",)))
        assert term(match, "coverage").computed is False
        assert match.meta.no_required_skills is True

    def test_gap_actions_are_templates_per_type(self) -> None:
        match = score_match(
            facts(
                required=(
                    RequiredSkill("Kubernetes", "hard"),
                    RequiredSkill("跨部门沟通", "soft"),
                    RequiredSkill("支付清算", "domain"),
                ),
                student=(),
            )
        )
        by_skill = {gap.skill: gap for gap in match.gaps}
        assert set(by_skill) == {"Kubernetes", "跨部门沟通", "支付清算"}
        assert "Kubernetes" in by_skill["Kubernetes"].action
        assert "形容词" in by_skill["跨部门沟通"].verify
        assert "取舍" in by_skill["支付清算"].verify

    def test_snippets_shown_are_capped_but_strength_is_not(self) -> None:
        items = tuple(
            experience(f"e{i}", description=f"第 {i} 次用 Python 交付")
            for i in range(MAX_SNIPPETS_PER_SKILL + 2)
        )
        match = score_match(facts(experiences=items))
        [covered] = match.covered
        assert len(covered.evidence) == MAX_SNIPPETS_PER_SKILL
        assert match.meta.candidate_pairs == len(items)
        # 关键词模式：强度按命中经历条数，第 4 条起已经封顶，不受展示条数影响。
        assert covered.evidence[0].strength == keyword_strength(KEYWORD_FALLBACK_HITS_CAP)

    def test_ai_mode_counts_and_degrade_reason_are_exclusive(self) -> None:
        item = experience("e1", description="用 Python 重写订单服务")
        bundle = build_evidence(facts(experiences=(item,)), (RequiredSkill("Python", "hard"),))
        outcome = ModelOutcome(
            strengths={0: 0.8},
            verified_pairs=1,
            dropped_unreferenced=0,
            dropped_unknown_skill=0,
            dropped_unusable_strength=0,
            dropped_missing_output=0,
            clamped_strength=0,
        )
        ai = AiScoring(model="deepseek-flash", prompt_version="v1", outcome=outcome)
        match = score_match(facts(experiences=(item,)), evidence=bundle, ai=ai)
        assert match.meta.evidence_mode == EVIDENCE_MODE_AI
        assert match.meta.model == "deepseek-flash"
        assert match.meta.degrade_reason is None
        assert match.meta.verified_pairs == 1

    def test_keyword_mode_must_say_why_it_degraded(self) -> None:
        """§6.5：降级可归因。没有 AI 又没给原因，就是静默降级。"""
        match = score_match(facts(), degrade_reason="key_missing")
        assert match.meta.evidence_mode == EVIDENCE_MODE_KEYWORD
        assert match.meta.degrade_reason == "key_missing"

    def test_score_is_renormalized_when_two_terms_drop_out(self) -> None:
        """无 deadline + 无偏好 → 分母只剩 coverage + evidence。"""
        item = experience("e1", description="用 Python 重写订单服务，做了三个月")
        match = score_match(facts(experiences=(item,), deadline=None, target_role=None))
        assert match.meta.weight_sum_used == pytest.approx(0.45 + 0.20)
        assert term(match, "preference").computed is False
        assert term(match, "remaining_time").computed is False


class TestScoreMatchDoesNotLeakIdentityFields:
    """§6 的「不发清单」：输入类型里就没有这些字段，测试把它钉成事实。"""

    def test_experience_text_has_no_dates_or_school(self) -> None:
        assert {field_name for field_name in ExperienceText.__dataclass_fields__} == {
            "id",
            "title",
            "description",
            "responsibility",
        }


class TestAliasLayer:
    """B1 / `ADR-016`：别名层在**打分纯函数**里的行为（档位顺序、域边界、覆盖归属）。

    这里只测判据，不测端点（端点那四条在 `tests/test_job_match.py`），也不测词表本身
    与它的写入约束（在 `tests/test_skill_alias_layer.py`）。分成三层是因为失败必须读得出是谁坏的。
    """

    def test_the_dataclass_default_and_an_explicit_empty_index_read_identically(self) -> None:
        """§5.1 的本层形式：**省略 `aliases`（走 `MatchFacts` 的默认值）与显式传空索引，
        整个 `ScoredMatch` 逐字相同**。

        为什么比的是整个对象而不是抽几格：§5.1 要防的退化是「把兜底接到词面四条之前」
        这类**结构性**改动，它可能只体现在某一格上（比如 `evidence` 的候选数），
        抽格子最容易恰好抽不到那一格。
        """
        item = experience("e1", description="用 Python 重写订单服务，做了三个月")
        common = {
            "job_title": "Python 后端开发工程师",
            "required_skills": (RequiredSkill("Python"), RequiredSkill("JavaScript")),
            "student_skills": ("Python", "JS"),
            "experiences": (item,),
            "target_role": "前端开发",
            "deadline": date(2026, 12, 22),
            "today": TODAY,
        }
        by_default = score_match(MatchFacts(**common))
        by_empty = score_match(MatchFacts(**common, aliases=AliasIndex.empty()))
        assert by_default == by_empty
        assert by_default.meta.alias_version == SKILL_ALIAS_VERSION
        assert by_default.meta.matcher_version == SKILL_MATCHER_VERSION

    def test_the_alias_row_moves_coverage_and_only_coverage(self) -> None:
        """§2.3 的影响面第一格：`JavaScript` 那条要求因为一行别名而进入分子。

        断言的是 **0.5 → 1.0** 这一跳而不是「等于 1.0」：前一跳才证明别名真的参与了，
        单看后一跳无法与「词面四条本来就认了」区分。
        最后一行钉的是回显契约（§2.2）：别名命中之前，`gaps` 里的名字仍是**岗位侧原写法**。
        """
        case = {
            "required": (RequiredSkill("Python"), RequiredSkill("JavaScript")),
            "student": ("Python", "JS"),
        }
        without = score_match(facts(**case))
        with_alias = score_match(facts(aliases=alias_index(("JavaScript", "JS")), **case))
        assert term(without, "coverage").value == 0.5
        assert term(with_alias, "coverage").value == 1.0
        assert [gap.skill for gap in without.gaps] == ["JavaScript"]
        assert with_alias.gaps == ()

    def test_a_skill_alias_row_never_touches_the_role_domain(self) -> None:
        """§2.2 两个域互不相通（本层的这一半）：`skill` 行的配对抬不动 `preference`。

        反面就是「技能同义表」与「方向同义表」混成一张——那时 `React 工程师` ↔ `前端开发`
        会被一条本来只说「两种技能写法是一回事」的行捞起来，而 §2.4 的 0.6 恰恰要
        区分「我们替他认的方向」与「技能写法」。
        """
        pair = ("React 工程师", "前端开发")
        lifted = score_match(
            facts(
                job_title=pair[1],
                target_role=pair[0],
                aliases=alias_index(pair, scope=ALIAS_SCOPE_ROLE),
            )
        )
        untouched = score_match(
            facts(
                job_title=pair[1],
                target_role=pair[0],
                aliases=alias_index(pair, scope=ALIAS_SCOPE_SKILL),
            )
        )
        assert term(lifted, "preference").note == PREFERENCE_NOTE_ALIAS_MATCH
        assert term(untouched, "preference").note == PREFERENCE_NOTE_CONFLICT
        assert term(untouched, "preference").value == 0.0

    def test_a_role_alias_row_never_touches_the_skill_domain(self) -> None:
        """反方向同样不许通：`role` 行抬不动 coverage，也不许让一条技能命中。"""
        required = (RequiredSkill("Python"), RequiredSkill("JavaScript"))
        student = ("Python", "JS")
        match = score_match(
            facts(
                required=required,
                student=student,
                aliases=alias_index(("JavaScript", "JS"), scope=ALIAS_SCOPE_ROLE),
            )
        )
        assert term(match, "coverage").value == 0.5
        assert [gap.skill for gap in match.gaps] == ["JavaScript"]

    @pytest.mark.parametrize(
        ("job_title", "target_role", "expected_note", "expected_value"),
        [
            # `contains` 仍然赢：词面逐字包含时不许被 0.6 的别名档盖掉（§2.4 顺序写死）。
            ("Python 后端开发工程师", "Python 后端开发", "contains", 1.0),
            # `token_overlap` 仍然赢：词面重合够线就用不着词表。
            ("数据分析实习生", "分析 数据 报表", "token_overlap", 0.6),
            # 别名档排在两格 0.0 之前：词表里有配对，就不许再报「冲突」。
            ("前端开发", "React 工程师", PREFERENCE_NOTE_ALIAS_MATCH, 0.6),
            # 词面有一点重合但没过线，且词表**没有**这一对 → 仍然是阈值以下那一格。
            ("前端开发实习生", "Python 后端开发工程师", PREFERENCE_NOTE_BELOW_THRESHOLD, 0.0),
            # 词面零重合且词表没有这一对 → 仍然是冲突那一格（§2.4「不重开那一格」）。
            ("前端开发", "游戏策划", PREFERENCE_NOTE_CONFLICT, 0.0),
        ],
    )
    def test_the_ladder_order_is_contains_token_overlap_alias_then_the_two_zeros(
        self,
        job_title: str,
        target_role: str,
        expected_note: str,
        expected_value: float,
    ) -> None:
        """§2.4 写死的顺序：`contains` → `token_overlap` → `alias_match` → 两格 0.0。

        词表对**每一行**都备好了 `前端开发 ↔ React 工程师` 这一对：前三行都不该被它碰到
        （各自的词面判据更先命中），第四、五行则是「词表里没有这一对」的对照——
        那两格 0.0 的语义与原因码是老板 2026-11-09 拍的，本批一个字不许改（§2.4 末句）。
        """
        aliases = alias_index(("前端开发", "React 工程师"), scope=ALIAS_SCOPE_ROLE)
        item = term(
            score_match(facts(job_title=job_title, target_role=target_role, aliases=aliases)),
            "preference",
        )
        assert item.note == expected_note
        assert item.value == expected_value

    def test_the_alias_tier_costs_exactly_the_same_as_the_token_overlap_tier(self) -> None:
        """0.6 就是 0.6：别名档与 `token_overlap` 档**同分**（§2.4「理由一致」的形式化）。

        这一格为什么要单独钉：档位值如果被写成 1.0，用户自己写准的标题与我们替他认的
        配对就同分了——那是 §4 列明的「代理指标越权」形状，而且它一旦写成 1.0
        就再也没人能从分数里看出这一对是词表救回来的。
        """
        alias_case = score_match(
            facts(
                job_title="前端开发",
                target_role="React 工程师",
                aliases=alias_index(("前端开发", "React 工程师"), scope=ALIAS_SCOPE_ROLE),
            )
        )
        overlap_case = score_match(facts(job_title="数据分析实习生", target_role="分析 数据 报表"))
        assert term(alias_case, "preference").value == 0.6
        assert term(overlap_case, "preference").value == 0.6
        # 同分而**不同来路**：这一格要的就是「分数一样、原因码不一样」，
        # 与那两格 0.0 的处理（§2.4 沿用老板 2026-11-09 的裁定）同一个形状。
        assert term(alias_case, "preference").note == PREFERENCE_NOTE_ALIAS_MATCH
        assert term(overlap_case, "preference").note == "token_overlap"

    def test_an_alias_row_moves_the_weighted_numerator_by_exactly_0_06(self) -> None:
        """§2.4 那句「权重 0.10 ⇒ 最多 +0.06」的准确形式：**加权分子**恰好 +0.06。

        对外的 `score` 走 §5.1 的重归一（`relevance` 恒退出分母 ⇒ `used = 0.85`），
        所以读数涨幅是 `0.06 / 0.85 ≈ 0.071`。端点级也钉了一份同样的对照，因为这一格
        正是 `ADR-016` §5.3「恰好 +0.06」与本仓重归一之间的差额所在（待老板过目，
        见 `tests/test_job_match.py::test_a_role_alias_row_moves_preference_from_conflict_to_alias_match`）。
        """
        case = {
            "job_title": "前端开发",
            "target_role": "React 工程师",
            "required": (RequiredSkill("Python"),),
            "student": ("Python",),
            "experiences": (experience("e1", description="用 Python 重写了订单服务"),),
            "deadline": date(2026, 12, 22),
        }
        before = score_match(facts(**case))
        after = score_match(
            facts(
                aliases=alias_index(("前端开发", "React 工程师"), scope=ALIAS_SCOPE_ROLE),
                **case,
            )
        )
        assert before.meta.weight_sum_used == after.meta.weight_sum_used == 0.85
        assert term(before, "preference").value == 0.0
        assert term(after, "preference").value == 0.6
        numerator_delta = after.score * 0.85 - before.score * 0.85
        assert numerator_delta == pytest.approx(0.06, abs=0.001)
        assert after.score - before.score == pytest.approx(0.06 / 0.85, abs=0.001)

    def test_locate_snippets_revalidates_an_alias_hit_and_returns_the_original_surface(
        self,
    ) -> None:
        """§2.3 的「同修」要求在本层的形式：片段复核也认别名，而片段仍是**原文连续子串**。

        用的是岗位要求 `JavaScript`、经历里逐字写着 `JavaScript` 的这一对：词面本来就判真，
        所以这条测的不是「别名让它找到了」，而是「接上别名层之后 §3 的反幻觉闸门仍然成立」——
        回给模型的必须是切出来的原文，不许因为我们查了词表就回一个规范名。
        """
        text = "这学期用 JavaScript 重写了三个页面"
        snippets = locate_snippets(text, "JavaScript", alias_index(("JavaScript", "JS")))
        assert snippets
        for surface, _ in snippets:
            assert surface in text

    def test_an_alias_hit_without_any_evidence_stays_in_the_union(self) -> None:
        """§5.2 的纯函数形式：**别名命中的技能零出处时进 `partial`，不许从并集里消失**。

        这是 `match_scoring.py:752` 那一支与别名的交叉点，也是并集不变式在纯函数层最容易破的一格：
        `strengths_by_skill` 从**候选对**分组，零候选的命中技能天生不在里面；
        那条补齐如果只对「词面命中」生效，别名命中的那条就会从三档清单里彻底消失。
        """
        required = (RequiredSkill("Python"), RequiredSkill("JavaScript"))
        student = ("Python", "JS")
        aliases = alias_index(("JavaScript", "JS"))
        match = score_match(facts(required=required, student=student, aliases=aliases))
        hit_names = {item.skill for item in match.covered} | {item.skill for item in match.partial}
        assert hit_names == {
            item.name for item in matched_required_skills(required, student, aliases)
        }
        assert "JavaScript" in hit_names
        assert match.gaps == ()

    def test_matched_with_forms_returns_the_students_own_spelling_for_an_alias_hit(
        self,
    ) -> None:
        """回显契约（§2.2）：别名命中时 `matched_form` 仍是**学生侧的原写法**。

        别名表存的是 key，所以这一格最容易写错成「回一个 key」或「回岗位侧写法」；
        两种都让用户看不到他自己打过的字，而那条信息恰恰是他改得动的（`ADR-013` §10 的原话）。
        """
        rows = matched_with_forms(
            [RequiredSkill("JavaScript")], ["JS"], alias_index(("JavaScript", "JS"))
        )
        assert [(item.name, form) for item, form in rows] == [("JavaScript", "JS")]

    def test_the_alias_table_is_a_fallback_not_a_loosening_of_the_four_rules(self) -> None:
        """§2.3 的次序在本层的形式：词面四条**先**判，别名只兜底，而且兜底不许放宽四条。

        故意用一条会被 §2.2 硬约束拒掉的行（`python 2` ↔ `python 3`）当探针：
        它能翻判据，恰恰说明「词表里每一行都会真实改变判定结果」（§3 原话），
        所以那条硬约束与它那条测试（`ADR-016` §5.4）不是形式主义。
        """
        harmful = alias_index(("python 2", "python 3"))
        # 规则 1（键全等）先赢，与词表无关。
        assert skill_forms_match("Python", "python", harmful) is True
        # 规则 3（一侧带号）也不靠词表就判真。
        assert skill_forms_match("Python3", "Python", harmful) is True
        # 词表里那条假配对确实翻得了判据（对照：没有词表时规则 4 判否）。
        assert skill_forms_match("Python 2", "Python 3") is False
        assert skill_forms_match("Python 2", "Python 3", harmful) is True
        # 而词表**没有**的 pair 一律不许被放宽：这是「兜底」与「什么算像」的分界。
        assert skill_forms_match("Python 2", "Vue 3", harmful) is False
        assert skill_forms_match("C", "C++", harmful) is False
        assert skill_forms_match("会计", "审计", harmful) is False
