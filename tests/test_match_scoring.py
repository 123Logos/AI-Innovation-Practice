"""`app/services/match_scoring.py` 的单元测试（ADR-013 §7/§8，落地顺序 7a）。

这一层是纯函数，所以这里**不碰数据库、不碰 HTTP**：五项分项、片段定位、四桶核验、
权重归一全部用值来测。集成侧（读库 + 调模型）的用例在 `tests/test_job_match.py`，
两边的断言不混在一起——混了之后失败原因读不出来。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.match_scoring import (
    EVIDENCE_MODE_AI,
    EVIDENCE_MODE_KEYWORD,
    KEYWORD_FALLBACK_HITS_CAP,
    MATCH_SNIPPET_RADIUS,
    MATCH_WEIGHTS,
    MATCH_WEIGHTS_VERSION,
    MAX_SNIPPETS_PER_SKILL,
    PARTIAL_STRENGTH_THRESHOLD,
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
    normalize_score,
    overlap_ratio,
    remaining_time_value,
    score_match,
    tokens,
    unique_required,
)

TODAY = date(2026, 11, 7)


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
) -> MatchFacts:
    return MatchFacts(
        job_title=job_title,
        required_skills=required,
        student_skills=student,
        experiences=experiences,
        target_role=target_role,
        deadline=deadline,
        today=TODAY,
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

    def test_nothing_computed_is_zero_not_crash(self) -> None:
        assert normalize_score([Term("a", 0.5, None, False)]) == (0.0, 0.0)


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

    def test_unrelated_target_gets_0_2_not_0(self) -> None:
        match = score_match(facts(target_role="想做游戏策划"))
        item = term(match, "preference")
        assert item.value == 0.2
        assert item.computed is True

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
        """关键词模式下要两条经历才算「证据站得住」：1/3 < 0.4 <= 2/3。"""
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

    def test_single_mention_is_partial_not_covered(self) -> None:
        item = experience("e1", description="接触过 Python")
        match = score_match(facts(experiences=(item,)))
        assert match.covered == ()
        assert [partial.skill for partial in match.partial] == ["Python"]
        assert keyword_strength(1) < PARTIAL_STRENGTH_THRESHOLD

    def test_matched_name_without_any_evidence_is_a_real_zero(self) -> None:
        match = score_match(facts(experiences=()))
        item = term(match, "evidence")
        assert item.computed is True
        assert item.value == 0.0
        assert item.note == "matched_without_evidence"
        assert match.partial == ()

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
