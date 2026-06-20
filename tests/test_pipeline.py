"""Tests for src/pipeline.py.

Covers:
- TAG_KEYWORDS and LANGUAGE_TAGS maps
- _infer_tags_fallback() - rule-based tag inference
- _infer_selection_reason_fallback() - rule-based reason
- _infer_rating_fallback() - star-based rating
- MOCK_TRANSLATIONS - static mock translation data
- _parse_json_from_response() - JSON extraction from LLM output
- CurationPipeline initialization
- _prefilter_top_n() - star-based pre-filter
- _stage_analyze() mock path
- _stage_summarize_and_reflect() mock path
- _stage_translate() mock path
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.pipeline import (
    LANGUAGE_TAGS,
    MOCK_TRANSLATIONS,
    TAG_KEYWORDS,
    CurationPipeline,
    _ensure_markdown_spacing,
    _infer_rating_fallback,
    _infer_selection_reason_fallback,
    _infer_tags_fallback,
)

# ===================================================================
# TAG_KEYWORDS & LANGUAGE_TAGS
# ===================================================================

class TestTagKeywords:
    """Test the TAG_KEYWORDS and LANGUAGE_TAGS constants."""

    def test_tag_keywords_has_major_categories(self):
        assert "#LLM" in TAG_KEYWORDS
        assert "#Agent" in TAG_KEYWORDS
        assert "#MoE" in TAG_KEYWORDS
        assert "#RAG" in TAG_KEYWORDS
        assert len(TAG_KEYWORDS) >= 15

    def test_each_tag_has_keywords(self):
        for _tag, keywords in TAG_KEYWORDS.items():
            assert len(keywords) >= 1

    def test_language_tags_includes_major_languages(self):
        assert LANGUAGE_TAGS["Python"] == "#Python"
        assert LANGUAGE_TAGS["Rust"] == "#Rust"
        assert LANGUAGE_TAGS["Go"] == "#Go"


# ===================================================================
# _infer_tags_fallback
# ===================================================================

class TestInferTagsFallback:
    """Test rule-based tag inference from repo description."""

    def test_llm_keyword_matches(self):
        repo = {"description": "A large language model with GPT architecture", "language": "Python"}
        tags = _infer_tags_fallback(repo)
        assert "#LLM" in tags

    def test_agent_keyword_matches(self):
        repo = {"description": "Multi-agent framework for autonomous tasks", "language": "Python"}
        tags = _infer_tags_fallback(repo)
        assert "#Agent" in tags

    def test_moe_keyword_matches(self):
        repo = {"description": "Mixture-of-experts model architecture", "language": "Python"}
        tags = _infer_tags_fallback(repo)
        assert "#MoE" in tags

    def test_max_three_tags(self):
        """Should return at most 3 tags."""
        repo = {
            "description": "LLM agent with RAG, MoE, and diffusion models",
            "language": "Python",
        }
        tags = _infer_tags_fallback(repo)
        assert len(tags) <= 3

    def test_language_tag_added_when_room(self):
        """Language tag should be added if there's room (< 3 tags)."""
        repo = {"description": "A simple tool", "language": "Rust"}
        tags = _infer_tags_fallback(repo)
        assert "#Rust" in tags

    def test_language_tag_not_added_when_full(self):
        """Language tag should not push out existing tags."""
        repo = {
            "description": "LLM agent with RAG retrieval and MoE mixture of experts",
            "language": "Python",
        }
        tags = _infer_tags_fallback(repo)
        assert len(tags) <= 3

    def test_fallback_to_opensource_no_language(self):
        """If no keywords match and language is unknown, fall back to #OpenSource."""
        repo = {"description": "An unrelated photo album app", "language": "Unknown"}
        tags = _infer_tags_fallback(repo)
        assert "#OpenSource" in tags

    def test_language_tag_used_when_no_keywords_match(self):
        """When no keywords match but a language tag exists, use the language tag."""
        repo = {"description": "An unrelated photo album app", "language": "JavaScript"}
        tags = _infer_tags_fallback(repo)
        assert "#JavaScript" in tags

    def test_matches_in_full_name(self):
        """Should also search in full_name."""
        repo = {
            "full_name": "moe/something",
            "description": "A model",
            "language": "Python",
        }
        tags = _infer_tags_fallback(repo)
        assert "#MoE" in tags

    def test_none_description_handled(self):
        """None description should not crash."""
        repo = {"description": None, "language": "Python"}
        tags = _infer_tags_fallback(repo)
        assert isinstance(tags, list)
        assert len(tags) <= 3


# ===================================================================
# _infer_selection_reason_fallback
# ===================================================================

class TestInferSelectionReasonFallback:
    """Test rule-based selection reason generation."""

    def test_includes_period_stars(self):
        repo = {"period_stars": "500 stars today", "language": "Python", "stars": 1000, "description": "A good repo"}
        reason = _infer_selection_reason_fallback(repo)
        assert "500 stars today" in reason

    def test_includes_language(self):
        repo = {"period_stars": "", "language": "Rust", "stars": 500, "description": "A Rust tool"}
        reason = _infer_selection_reason_fallback(repo)
        assert "Rust" in reason

    def test_includes_stars_when_over_1000(self):
        repo = {"period_stars": "", "language": "Python", "stars": 5000, "description": "Popular"}
        reason = _infer_selection_reason_fallback(repo)
        assert "⭐" in reason
        assert "5,000" in reason

    def test_does_not_include_low_stars(self):
        repo = {"period_stars": "", "language": "Python", "stars": 100, "description": "Small"}
        reason = _infer_selection_reason_fallback(repo)
        assert "⭐" not in reason

    def test_truncates_long_description(self):
        long_desc = "A" * 200
        repo = {"period_stars": "", "language": "Python", "stars": 100, "description": long_desc}
        reason = _infer_selection_reason_fallback(repo)
        assert len(reason.split("核心定位: ")[-1]) <= 83  # 80 + "..."

    def test_fallback_message(self):
        repo = {"period_stars": "", "language": "", "stars": 0, "description": ""}
        reason = _infer_selection_reason_fallback(repo)
        assert "trending" in reason

    def test_none_values(self):
        repo = {"period_stars": None, "language": None, "stars": None, "description": None}
        reason = _infer_selection_reason_fallback(repo)
        assert isinstance(reason, str)


# ===================================================================
# _infer_rating_fallback
# ===================================================================

class TestInferRatingFallback:
    """Test star-based rating inference."""

    def test_s_rating_very_high_stars(self):
        assert _infer_rating_fallback({"stars": 100000, "period_stars": ""}) == "S"

    def test_s_rating_high_period_stars(self):
        assert _infer_rating_fallback({"stars": 1000, "period_stars": "3,500 stars today"}) == "S"

    def test_a_rating_high_stars(self):
        assert _infer_rating_fallback({"stars": 20000, "period_stars": ""}) == "A"

    def test_a_rating_medium_period_stars(self):
        assert _infer_rating_fallback({"stars": 500, "period_stars": "600 stars today"}) == "A"

    def test_b_rating_low_stars(self):
        assert _infer_rating_fallback({"stars": 1000, "period_stars": ""}) == "B"

    def test_b_rating_very_low_stars(self):
        assert _infer_rating_fallback({"stars": 10, "period_stars": ""}) == "B"

    def test_none_values_default_to_b(self):
        assert _infer_rating_fallback({"stars": None, "period_stars": None}) == "B"

    def test_unparseable_period_does_not_crash(self):
        """Unparseable period_stars should not crash (ValueError guard)."""
        assert _infer_rating_fallback({"stars": 100, "period_stars": "invalid"}) == "B"


# ===================================================================
# _ensure_markdown_spacing
# ===================================================================

class TestEnsureMarkdownSpacing:
    """Test the _ensure_markdown_spacing post-processor."""

    def test_empty_string(self):
        assert _ensure_markdown_spacing("") == ""

    def test_no_headers_passthrough(self):
        text = "Some plain text\n\nWith a paragraph."
        assert _ensure_markdown_spacing(text) == text

    def test_header_gets_leading_blank(self):
        """Content before ### should get a blank line inserted before ###."""
        text = "content\n### header"
        result = _ensure_markdown_spacing(text)
        assert result == "content\n\n### header"

    def test_header_gets_trailing_blank(self):
        """Content after ### should get a blank line inserted after ###."""
        text = "### header\ncontent"
        result = _ensure_markdown_spacing(text)
        assert result == "### header\n\ncontent"

    def test_header_already_spaced_unchanged(self):
        """Already well-spaced headers should not be modified."""
        text = "### header\n\ncontent\n\n### header2\n\ncontent2"
        result = _ensure_markdown_spacing(text)
        assert result == text

    def test_consecutive_headers_no_extra_blank(self):
        """Consecutive headers should NOT get blank between them."""
        text = "### header1\n### header2"
        result = _ensure_markdown_spacing(text)
        assert result == text

    def test_first_line_is_header(self):
        """First line being a header should not break."""
        text = "### header\ncontent"
        result = _ensure_markdown_spacing(text)
        assert result == "### header\n\ncontent"

    def test_multiple_headers_all_spaced(self):
        """Multiple headers with content gaps should all get spacing."""
        text = "intro\n### h1\nbody1\n### h2\nbody2\nend"
        result = _ensure_markdown_spacing(text)
        lines = result.split("\n")
        assert lines[0] == "intro"
        assert lines[1] == ""
        assert lines[2] == "### h1"
        assert lines[3] == ""
        assert lines[4] == "body1"
        assert lines[5] == ""
        assert lines[6] == "### h2"

class TestMockTranslations:
    """Test the MOCK_TRANSLATIONS constant."""

    def test_known_repos_have_translations(self):
        assert "deepseek-ai/DeepSeek-V3" in MOCK_TRANSLATIONS
        assert "deepseek-ai/DeepSeek-R1" in MOCK_TRANSLATIONS
        assert "lucidrains/MLA-pytorch" in MOCK_TRANSLATIONS

    def test_translation_has_required_sections(self):
        for _name, translation in MOCK_TRANSLATIONS.items():
            assert "### 要解决的核心痛点" in translation
            assert "### 设计巧思与架构取舍" in translation
            assert "### 工程启示与可迁移经验" in translation
            assert "### 关联生态与延展阅读" in translation


# ===================================================================
# _parse_json_from_response
# ===================================================================

class TestParseJsonFromResponse:
    """Test JSON parsing from various LLM response formats."""

    @pytest.fixture
    def parser(self, pipeline_config):
        """Create a CurationPipeline instance to access _parse_json_from_response."""
        from unittest.mock import MagicMock
        client = MagicMock()
        return CurationPipeline(pipeline_config, client)._parse_json_from_response

    def test_raw_json_list(self, parser):
        text = '[{"full_name": "a/b", "rating": "S"}]'
        result = parser(text)
        assert len(result) == 1
        assert result[0]["full_name"] == "a/b"
        assert result[0]["rating"] == "S"

    def test_json_in_code_block(self, parser):
        text = 'Here is the JSON:\n```json\n[{"full_name": "a/b", "rating": "S"}]\n```\nEnd.'
        result = parser(text)
        assert len(result) == 1
        assert result[0]["full_name"] == "a/b"

    def test_json_in_code_block_no_lang(self, parser):
        text = '```\n[{"full_name": "c/d", "rating": "A"}]\n```'
        result = parser(text)
        assert len(result) == 1
        assert result[0]["full_name"] == "c/d"

    def test_dict_containing_list(self, parser):
        text = '{"repos": [{"full_name": "e/f", "rating": "B"}]}'
        result = parser(text)
        assert len(result) == 1
        assert result[0]["full_name"] == "e/f"

    def test_dict_without_list_returns_empty(self, parser):
        """Valid JSON dict with no list values should return []."""
        text = '{"status": "ok", "count": 5}'
        result = parser(text)
        assert result == []

    def test_invalid_json_raises(self, parser):
        text = "This is not JSON at all."
        with pytest.raises((ValueError, json.JSONDecodeError)):
            parser(text)

    def test_empty_json_array(self, parser):
        result = parser("[]")
        assert result == []

    def test_json_with_extra_text_not_valid(self, parser):
        """When extra text surrounds JSON but no triple-backtick, it's not valid JSON."""
        text = 'Some prefix text [{"full_name": "x/y"}] and suffix text'
        with pytest.raises((ValueError, json.JSONDecodeError)):
            parser(text)

    def test_json_with_escaped_newlines(self, parser):
        """Test handling of escaped newlines in JSON strings."""
        text = '[{"full_name": "a/b", "refined_summary": "### Header\\nBody text\\n### Header2\\nMore"}]'
        result = parser(text)
        assert len(result) == 1


# ===================================================================
# CurationPipeline
# ===================================================================

class TestCurationPipelineInit:
    """Test pipeline initialization."""

    def test_init_loads_personas(self, pipeline_config):
        """Pipeline should load persona YAML on init."""
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client, persona_key="intermediate")
        assert pipeline.persona_key == "intermediate"
        assert "name" in pipeline.current_persona
        assert pipeline.current_persona["name"] == "中阶实践者"

    def test_init_invalid_persona_falls_back(self, pipeline_config):
        """An invalid persona key should fall back to a default dict."""
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client, persona_key="nonexistent")
        assert pipeline.persona_key == "nonexistent"
        assert "name" in pipeline.current_persona

    def test_use_mock_defaults_to_false(self, pipeline_config):
        """use_mock should be initialized to False."""
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client)
        assert pipeline.use_mock is False


class TestPrefilterTopN:
    """Test the pre-filter that reduces repos before Stage 2."""

    def test_disabled_filter_passes_all_through(self, pipeline_config):
        """When filter is disabled, all repos pass through."""
        pipeline_config.stage2_pre_filter.enabled = False
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client)
        repos = [{"stars": i} for i in range(100)]
        result = pipeline._prefilter_top_n(repos)
        assert len(result) == 100

    def test_filter_truncates_long_tail(self, pipeline_config):
        """When repos exceed max_repos, should keep top N by stars."""
        pipeline_config.stage2_pre_filter.max_repos = 5
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client)
        repos = [{"stars": 100 - i, "full_name": f"repo/{i}"} for i in range(20)]
        result = pipeline._prefilter_top_n(repos)
        assert len(result) == 5
        assert result[0]["stars"] == 100

    def test_filter_small_list_unchanged(self, pipeline_config):
        """When repos are under the limit, they should all pass through."""
        pipeline_config.stage2_pre_filter.max_repos = 10
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client)
        repos = [{"stars": i} for i in range(5)]
        result = pipeline._prefilter_top_n(repos)
        assert len(result) == 5


class TestStageAnalyzeMock:
    """Test the mock/fallback path of Stage 2."""

    def test_mock_mode_returns_sorted_repos(self, pipeline_config, sample_repos):
        """In mock mode, repos should be rated and sorted by rating then stars."""
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client)
        pipeline.use_mock = True  # Enable mock mode
        result = pipeline._stage_analyze(sample_repos)
        assert len(result) > 0
        for r in result:
            assert "rating" in r
            assert "tags" in r
            assert "selection_reason" in r

    def test_mock_rating_order(self, pipeline_config, sample_repos):
        """Sorted results should have S before A before B."""
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client)
        pipeline.use_mock = True
        result = pipeline._stage_analyze(sample_repos)
        rating_order = {"S": 0, "A": 1, "B": 2, "C": 3}
        for i in range(len(result) - 1):
            curr = rating_order.get(result[i]["rating"], 4)
            next_ = rating_order.get(result[i + 1]["rating"], 4)
            assert curr <= next_

    def test_mock_fills_missing_fields(self, pipeline_config, sample_repos):
        """Each repo should get rating, tags, and selection_reason."""
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client)
        pipeline.use_mock = True
        result = pipeline._stage_analyze(sample_repos)
        for r in result:
            assert r["rating"] in ("S", "A", "B", "C")
            assert isinstance(r["tags"], list)
            assert isinstance(r["selection_reason"], str)
            assert len(r["selection_reason"]) > 0


class TestStageSummarizeAndReflectMock:
    """Test the mock path of Stage 3+4."""

    def test_mock_mode_returns_repos_with_summaries(self, pipeline_config, sample_analyzed_repos):
        """In mock mode, each repo gets a refined_summary."""
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client)
        pipeline.use_mock = True
        result = pipeline._stage_summarize_and_reflect(sample_analyzed_repos)
        assert len(result) == len(sample_analyzed_repos)
        for r in result:
            assert "refined_summary" in r
            assert isinstance(r["refined_summary"], str)
            assert len(r["refined_summary"]) > 0

    def test_mock_summary_uses_preseeded_or_stub(self, pipeline_config, sample_analyzed_repos):
        """Known repos get MOCK_TRANSLATIONS content; unknown get stub."""
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client, persona_key="advanced")
        pipeline.use_mock = True
        result = pipeline._stage_summarize_and_reflect(sample_analyzed_repos)
        for r in result:
            assert "refined_summary" in r
            assert isinstance(r["refined_summary"], str)
            assert len(r["refined_summary"]) > 0
        # Known repo gets pre-authored Chinese; unknown repo gets English stub
        known_summary = next(r["refined_summary"] for r in result if r["full_name"] == "deepseek-ai/DeepSeek-V3")
        assert "要解决的核心痛点" in known_summary
        unknown_summary = next(r["refined_summary"] for r in result if r["full_name"] == "lowstars/tiny-tool")
        assert "要解决的核心痛点" in unknown_summary


class TestPipelineRunMock:
    """Test the full pipeline run in mock mode."""

    def test_mock_run_returns_full_result(self, pipeline_config):
        """Running the full pipeline in mock mode should produce a complete result."""
        client = MagicMock()
        # Configure get_stats to return real dicts for _log_llm_stage
        client.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }
        pipeline = CurationPipeline(pipeline_config, client)
        result = pipeline.run(since="daily", use_mock=True)
        assert "meta" in result
        assert "repos" in result
        assert "reports" in result
        assert result["meta"]["total_input_repos"] > 0
        assert result["meta"]["total_curated_repos"] > 0

    def test_mock_run_reports_have_required_keys(self, pipeline_config):
        """Reports should have markdown, feishu, and slack keys."""
        client = MagicMock()
        # Configure get_stats to return real dicts for _log_llm_stage
        client.get_stats.return_value = {
            "call_count": 0, "failed_attempt_count": 0,
            "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0,
        }
        pipeline = CurationPipeline(pipeline_config, client)
        result = pipeline.run(since="daily", use_mock=True)
        reports = result["reports"]
        assert "markdown" in reports
        assert "feishu" in reports
        assert "slack" in reports


class TestLogLLMStage:
    """Test the _log_llm_stage helper."""

    def test_log_llm_stage_with_zero_stats(self, pipeline_config):
        """_log_llm_stage with zero stats should not crash."""
        client = MagicMock()
        # Make get_stats return a real dict
        client.get_stats.return_value = {
            "call_count": 3,
            "failed_attempt_count": 1,
            "total_prompt_tokens": 15000,
            "total_completion_tokens": 5000,
            "total_tokens": 20000,
        }
        pipeline = CurationPipeline(pipeline_config, client)
        before = {
            "call_count": 0,
            "failed_attempt_count": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
        }
        pipeline._log_llm_stage("Test Stage", before)


class TestPipelineInitWithNoPersonasFile:
    """Test pipeline init when personas.yaml does not exist."""

    def test_missing_personas_file_fallback(self, pipeline_config, monkeypatch):
        """When personas.yaml is missing, should use a default persona dict."""
        monkeypatch.setattr("src.pipeline.BASE_DIR", Path("/nonexistent"))
        client = MagicMock()
        pipeline = CurationPipeline(pipeline_config, client, persona_key="intermediate")
        # The default fallback dict has name="中阶实践者" as fallback
        # So even without the file, we get a valid persona
        assert "name" in pipeline.current_persona
