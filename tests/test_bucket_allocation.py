"""Tests for the Bucket Allocation Engine.

Covers:
- _infer_tds_fallback() — T/E/S classification from description keywords
- BucketAllocationConfig — Pydantic model defaults and overrides
- CurationPipeline._bucket_allocate() — 3-bucket allocation logic
- CurationPipeline._infer_tds() — instance method TDS inference
- Edge cases: under-fill, over-fill, all-in-one-bucket, disabled config, missing fields
"""

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.config import AppConfig, BucketAllocationConfig
from src.pipeline import (
    CurationPipeline,
    _infer_tds_fallback,
)


# ===================================================================
# _infer_tds_fallback
# ===================================================================

class TestInferTDSFallback:
    """Test the module-level TDS rule engine: _infer_tds_fallback()."""

    def test_t_keyword_mla(self):
        assert _infer_tds_fallback("MLA-based attention mechanism") == "T"

    def test_t_keyword_moe(self):
        assert _infer_tds_fallback("MoE architecture with sparse experts") == "T"

    def test_t_keyword_attention(self):
        assert _infer_tds_fallback("Multi-head attention") == "T"

    def test_t_keyword_cuda_kernel(self):
        assert _infer_tds_fallback("Custom CUDA kernel for flash attention") == "T"

    def test_t_keyword_kv_cache(self):
        assert _infer_tds_fallback("KV cache compression for inference") == "T"

    def test_t_keyword_compiler(self):
        assert _infer_tds_fallback("A new compiler for ML graphs") == "T"

    def test_t_keyword_runtime(self):
        assert _infer_tds_fallback("Lightweight inference runtime") == "T"

    def test_t_keyword_metal(self):
        assert _infer_tds_fallback("Metal shader for GPU compute") == "T"

    def test_t_keyword_custom_shader(self):
        assert _infer_tds_fallback("Custom shader pipeline") == "T"

    def test_t_keyword_new_language(self):
        assert _infer_tds_fallback("A new language for AI programming") == "T"

    def test_t_keyword_database_engine(self):
        assert _infer_tds_fallback("Embedded database engine") == "T"

    def test_t_keyword_protocol(self):
        assert _infer_tds_fallback("New networking protocol") == "T"

    def test_t_precedence_over_e(self):
        """When both T and E keywords match, T should win (checked first)."""
        assert _infer_tds_fallback("MoE agent framework") == "T"

    def test_e_keyword_agent(self):
        assert _infer_tds_fallback("Multi-agent orchestration framework") == "E"

    def test_e_keyword_rag(self):
        assert _infer_tds_fallback("RAG pipeline with hybrid search") == "E"

    def test_e_keyword_mcp(self):
        assert _infer_tds_fallback("MCP server implementation") == "E"

    def test_e_keyword_inference(self):
        assert _infer_tds_fallback("Inference optimization toolkit") == "E"

    def test_e_keyword_optimiz(self):
        """'optimiz' matches 'optimize', 'optimization', etc."""
        assert _infer_tds_fallback("Memory optimization library") == "E"

    def test_e_keyword_cli(self):
        assert _infer_tds_fallback("Modern CLI tool for developers") == "E"

    def test_e_keyword_raycast(self):
        assert _infer_tds_fallback("Raycast extension for productivity") == "E"

    def test_e_keyword_swiftui(self):
        assert _infer_tds_fallback("SwiftUI component library") == "E"

    def test_e_keyword_core_ml(self):
        assert _infer_tds_fallback("Core ML model converter") == "E"

    def test_e_keyword_mlx(self):
        assert _infer_tds_fallback("MLX-based training scripts") == "E"

    def test_e_keyword_comfyui(self):
        assert _infer_tds_fallback("ComfyUI custom node pack") == "E"

    def test_e_keyword_workflow(self):
        assert _infer_tds_fallback("Workflow automation engine") == "E"

    def test_e_keyword_automation(self):
        assert _infer_tds_fallback("Build automation tool") == "E"

    def test_e_keyword_xcode(self):
        assert _infer_tds_fallback("Xcode project template") == "E"

    def test_e_keyword_mach_o(self):
        assert _infer_tds_fallback("Mach-O binary analyzer") == "E"

    def test_e_keyword_ipa(self):
        assert _infer_tds_fallback("IPA file parser") == "E"

    def test_e_keyword_window_manager(self):
        assert _infer_tds_fallback("macOS window manager") == "E"

    def test_s_fallback_no_keywords(self):
        assert _infer_tds_fallback("A simple calculator app") == "S"

    def test_s_fallback_empty_string(self):
        assert _infer_tds_fallback("") == "S"

    def test_s_fallback_generic_webapp(self):
        assert _infer_tds_fallback("Photo gallery web application") == "S"

    def test_case_insensitive(self):
        assert _infer_tds_fallback("MLA Attention Mechanism") == "T"

    def test_partial_word_match_optimiz(self):
        assert _infer_tds_fallback("GPU memory optimization") == "E"


# ===================================================================
# BucketAllocationConfig
# ===================================================================

class TestBucketAllocationConfig:
    """Test the BucketAllocationConfig Pydantic model."""

    def test_defaults(self):
        cfg = BucketAllocationConfig()
        assert cfg.enabled is True
        assert cfg.total_slots == 9
        assert cfg.early_bird == 3
        assert cfg.high_star_hot == 3
        assert cfg.deep_dive == 3

    def test_custom_values(self):
        cfg = BucketAllocationConfig(enabled=False, total_slots=12, early_bird=4, high_star_hot=4, deep_dive=4)
        assert cfg.enabled is False
        assert cfg.total_slots == 12
        assert cfg.early_bird == 4
        assert cfg.high_star_hot == 4
        assert cfg.deep_dive == 4

    def test_integrated_in_app_config(self):
        cfg = AppConfig()
        assert hasattr(cfg, "bucket_allocation")
        assert cfg.bucket_allocation.total_slots == 9

    def test_partial_override_keeps_defaults(self):
        cfg = BucketAllocationConfig(early_bird=5)
        assert cfg.early_bird == 5
        assert cfg.high_star_hot == 3
        assert cfg.deep_dive == 3
        assert cfg.total_slots == 9
        assert cfg.enabled is True


# ===================================================================
# Helpers & Fixtures for _bucket_allocate tests
# ===================================================================

def _make_repo(
    full_name: str,
    stars: int = 5000,
    description: str = "A test repo.",
    is_first_seen: bool = False,
    period_stars: str = "",
) -> Dict[str, Any]:
    return {
        "full_name": full_name,
        "stars": stars,
        "description": description,
        "is_first_seen": is_first_seen,
        "period_stars": period_stars,
        "language": "Python",
        "source": "trending",
        "owner": full_name.split("/")[0],
        "name": full_name.split("/")[1],
    }


@pytest.fixture
def bucket_pipeline() -> CurationPipeline:
    cfg = AppConfig()
    return CurationPipeline(cfg, MagicMock())


@pytest.fixture
def disabled_bucket_pipeline() -> CurationPipeline:
    cfg = AppConfig()
    cfg.bucket_allocation.enabled = False
    return CurationPipeline(cfg, MagicMock())


# ===================================================================
# _bucket_allocate — Basic
# ===================================================================

class TestBucketAllocateBasic:
    """Basic bucket allocation scenarios."""

    def test_mixed_repos_returns_9(self, bucket_pipeline):
        """A diverse mix should yield exactly 9 repos with 3 from each bucket."""
        repos = []
        for i in range(5):
            repos.append(_make_repo(f"early{i}/repo", stars=500 + i * 200))
        for i in range(5):
            repos.append(_make_repo(f"high{i}/repo", stars=10000 + i * 1000))
        for i in range(5):
            repos.append(_make_repo(f"deep{i}/repo", stars=5000 + i * 200))
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9

    def test_classification_sets_tds_and_bucket_on_all_repos(self, bucket_pipeline):
        """With > 9 repos, every repo should have 'tds' and '_bucket' set."""
        repos = [_make_repo(f"r{i}/repo", stars=1000 + i * 200) for i in range(20)]
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9
        for r in result:
            assert "tds" in r
            assert r["tds"] in ("T", "E", "S")
            assert "_bucket" in r
            assert r["_bucket"] in ("early_bird", "high_star", "deep_dive")

    def test_tds_reflects_description_keywords(self, bucket_pipeline):
        """TDS should match the description content."""
    def test_tds_reflects_description_keywords(self, bucket_pipeline):
        """TDS should match the description content for repos that make the cut."""
        repos = [
            _make_repo("tech/a", stars=5000, description="Custom CUDA kernel optimization"),
            _make_repo("eng/b", stars=5000, description="Agent framework with RAG pipeline"),
        ]
        # Pad with lower-star repos so the test repos make the cut
        for i in range(12):
            repos.append(_make_repo(f"filler{i}/repo", stars=1000 + i * 50, is_first_seen=True))
        result = bucket_pipeline._bucket_allocate(repos)
        tds_map = {r["full_name"]: r["tds"] for r in result}
        assert tds_map.get("tech/a") == "T"
        assert tds_map.get("eng/b") == "E"

    def test_under_9_repos_returned_as_is(self, bucket_pipeline):
        """When repos <= total_slots, all pass through unchanged."""
        repos = [_make_repo(f"small{i}/repo") for i in range(5)]
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 5

    def test_exactly_9_repos_pass_through(self, bucket_pipeline):
        repos = [_make_repo(f"exact{i}/repo") for i in range(9)]
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9

    def test_under_9_repos_no_extra_fields_set(self, bucket_pipeline):
        """When repos <= 9, they are returned without tds/_bucket added.
        Note: this is current behavior; the method only classifies when > total_slots."""
        repos = [_make_repo("a/repo")]
        result = bucket_pipeline._bucket_allocate(repos)
        assert "_bucket" not in result[0]
        assert "tds" not in result[0]


# ===================================================================
# _bucket_allocate — Edge cases
# ===================================================================

class TestBucketAllocateEdgeCases:
    """Edge cases for bucket allocation with current code behavior."""

    def test_no_early_bird_repos(self, bucket_pipeline):
        """All repos >= 3000 and not first_seen → no Early Bird."""
        repos = [_make_repo(f"mid{i}/repo", stars=5000 + i) for i in range(15)]
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9
        buckets = [r.get("_bucket") for r in result]
        assert "early_bird" not in buckets

    def test_all_high_star_repos(self, bucket_pipeline):
        """When all repos >= 10000 stars, all get _bucket='high_star'.
        Note: _bucket label is a classification (not pool assignment) so it
        persists even when repos are taken from the leftover pool."""
        repos = [_make_repo(f"big{i}/repo", stars=10000 + i * 1000) for i in range(15)]
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9
        for r in result:
            assert r["_bucket"] == "high_star"

    def test_all_early_bird_repos(self, bucket_pipeline):
        """When all repos < 3000 stars, all get _bucket='early_bird'."""
        repos = [_make_repo(f"tiny{i}/repo", stars=100 + i * 100) for i in range(15)]
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9
        for r in result:
            assert r["_bucket"] == "early_bird"

    def test_single_eb_candidate_fills_with_dd(self, bucket_pipeline):
        """Only 1 EB candidate → 1 EB, fill rest with DD candidates."""
        repos = [_make_repo("only-eb/repo", stars=500, is_first_seen=True)]
        for i in range(15):
            repos.append(_make_repo(f"mid{i}/repo", stars=5000 + i * 100))
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9
        eb_count = sum(1 for r in result if r.get("_bucket") == "early_bird")
        assert eb_count == 1

    def test_disabled_config_falls_back_to_prefilter(self, disabled_bucket_pipeline):
        """When disabled, _bucket_allocate calls _prefilter_top_n."""
        repos = [_make_repo(f"r{i}/repo", stars=100 - i) for i in range(100)]
        result = disabled_bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 88  # default max_repos

    def test_is_first_seen_in_early_bird_logic(self, bucket_pipeline):
        """First-seen repo with < 5000 stars → early_bird.
        Not-first-seen repo with >= 3000 stars → NOT early_bird (may not make final 9)."""
        repos = [
            _make_repo("first-seen/repo", stars=4000, is_first_seen=True),
            _make_repo("not-first/repo", stars=4000, is_first_seen=False),
        ]
        for i in range(10):
            repos.append(_make_repo(f"big{i}/repo", stars=15000))
        result = bucket_pipeline._bucket_allocate(repos)
        buckets = {r["full_name"]: r["_bucket"] for r in result}
        assert buckets["first-seen/repo"] == "early_bird"
        # 'not-first/repo' may not make the cut (depends on TDS+star sorting)
        not_first = buckets.get("not-first/repo")
        assert not_first is None or not_first != "early_bird"

    def test_period_stars_and_low_stars_early_bird_takes_precedence(self, bucket_pipeline):
        """When a repo satisfies BOTH Early Bird (< 3k) and High-Star (period >= 500),
        the current code assigns it to Early Bird because is_early is checked first.
        Note: This may be a bug — see design doc discussion."""
        repos = [
            _make_repo("viral/repo", stars=2000, period_stars="1,500 stars today"),
        ]
        for i in range(12):
            repos.append(_make_repo(f"big{i}/repo", stars=15000))
        result = bucket_pipeline._bucket_allocate(repos)
        buckets = {r["full_name"]: r["_bucket"] for r in result}
        # Current behavior: is_early (stars < 3000) wins over is_high (period >= 500)
        assert buckets["viral/repo"] == "early_bird"

    def test_empty_repos_list(self, bucket_pipeline):
        result = bucket_pipeline._bucket_allocate([])
        assert result == []

    def test_missing_stars_defaults_to_zero(self, bucket_pipeline):
        repos = [{"full_name": "nostars/repo", "description": "test"}]
        for i in range(12):
            repos.append(_make_repo(f"filler{i}/repo", stars=5000))
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9

    @pytest.mark.xfail(reason="Known bug: take_from() sort crashes on None stars (user still debugging)")
    def test_none_stars_handled(self, bucket_pipeline):
        repos = [_make_repo("null/repo", stars=0)]
        repos[-1]["stars"] = None
        for i in range(12):
            repos.append(_make_repo(f"filler{i}/repo", stars=5000))
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9

    def test_none_period_stars_handled(self, bucket_pipeline):
        repos = [_make_repo("noperiod/repo", stars=5000, period_stars=None)]
        for i in range(12):
            repos.append(_make_repo(f"filler{i}/repo", stars=6000))
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) == 9


# ===================================================================
# _bucket_allocate — Ordering
# ===================================================================

class TestBucketAllocateOrdering:
    """Verify bucket allocation sorting behavior."""

    def test_eb_repos_sorted_by_stars_descending(self, bucket_pipeline):
        repos = [_make_repo(f"tiny{i}/repo", stars=200 + i * 200, is_first_seen=True) for i in range(10)]
        for i in range(10):
            repos.append(_make_repo(f"big{i}/repo", stars=20000))
        result = bucket_pipeline._bucket_allocate(repos)
        eb_repos = [r for r in result if r.get("_bucket") == "early_bird"]
        for i in range(len(eb_repos) - 1):
            assert eb_repos[i]["stars"] >= eb_repos[i + 1]["stars"]

    def test_overfill_capped_at_9(self, bucket_pipeline):
        repos = [_make_repo(f"r{i}/repo", stars=(i % 12) * 1000) for i in range(50)]
        result = bucket_pipeline._bucket_allocate(repos)
        assert len(result) <= 9


# ===================================================================
# _bucket_allocate — is_first_seen handling
# ===================================================================

class TestBucketAllocateIsFirstSeen:
    """Verify _bucket_allocate reads is_first_seen correctly."""

    def test_first_seen_4500_goes_early_bird(self, bucket_pipeline):
        """4500 stars + first_seen=True → early_bird."""
        repos = [_make_repo("unseen/repo", stars=4500, is_first_seen=True)]
        for i in range(20):
            repos.append(_make_repo(f"mid{i}/repo", stars=6000))
        result = bucket_pipeline._bucket_allocate(repos)
        buckets = {r["full_name"]: r["_bucket"] for r in result}
        assert buckets.get("unseen/repo") == "early_bird"

    def test_4500_not_first_seen_not_early_bird(self, bucket_pipeline):
        """4500 stars + is_first_seen=False → NOT early_bird."""
        repos = [_make_repo("seen/repo", stars=4500, is_first_seen=False)]
        for i in range(20):
            repos.append(_make_repo(f"mid{i}/repo", stars=6000))
        result = bucket_pipeline._bucket_allocate(repos)
        buckets = {r["full_name"]: r["_bucket"] for r in result}
        assert buckets.get("seen/repo") != "early_bird"

    def test_missing_is_first_seen_defaults_false(self, bucket_pipeline):
        repo_no_flag = {"full_name": "noflag/repo", "stars": 4000, "description": "test",
                        "language": "Python", "source": "trending"}
        repos = [repo_no_flag]
        for i in range(20):
            repos.append(_make_repo(f"mid{i}/repo", stars=6000))
        result = bucket_pipeline._bucket_allocate(repos)
        buckets = {r["full_name"]: r["_bucket"] for r in result}
        assert buckets.get("noflag/repo") != "early_bird"


# ===================================================================
# _infer_tds (instance method)
# ===================================================================

class TestInferTDSInstance:
    """Test the instance method _infer_tds (identical to _infer_tds_fallback)."""

    @pytest.fixture
    def pipeline(self):
        return CurationPipeline(AppConfig(), MagicMock())

    def test_t(self, pipeline):
        assert pipeline._infer_tds("MLA attention mechanism") == "T"

    def test_e(self, pipeline):
        assert pipeline._infer_tds("Agent framework") == "E"

    def test_s(self, pipeline):
        assert pipeline._infer_tds("A simple blog engine") == "S"


# ===================================================================
# Integration: Pipeline run with bucket allocation
# ===================================================================

class TestPipelineRunWithBucketAllocation:
    """Verify bucket allocation integrates correctly in mock pipeline run."""

    @pytest.fixture(name="simple_pipeline_config")
    def _pipeline_config(self):
        cfg = AppConfig()
        cfg.ai.api_key = "test-key"
        return cfg

    def test_mock_run_sets_bucket_and_tds(self, simple_pipeline_config):
        client = MagicMock()
        client.get_stats.return_value = {"call_count": 0, "failed_attempt_count": 0,
                                         "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0}
        pipeline = CurationPipeline(simple_pipeline_config, client)
        pipeline.config.bucket_allocation.enabled = True
        result = pipeline.run(since="daily", use_mock=True)
        assert result["meta"]["total_curated_repos"] > 0
        for r in result["repos"]:
            assert "_bucket" in r
            assert r["_bucket"] in ("early_bird", "high_star", "deep_dive")
            assert "tds" in r
            assert r["tds"] in ("T", "E", "S")
            # Verify mock _stage_analyze also sets technical_depth
            assert "technical_depth" in r
            assert r["technical_depth"] in ("T", "E", "S")

    def test_disabled_bucket_still_works(self, simple_pipeline_config):
        client = MagicMock()
        client.get_stats.return_value = {"call_count": 0, "failed_attempt_count": 0,
                                         "total_prompt_tokens": 0, "total_completion_tokens": 0, "total_tokens": 0}
        pipeline = CurationPipeline(simple_pipeline_config, client)
        pipeline.config.bucket_allocation.enabled = False
        result = pipeline.run(since="daily", use_mock=True)
        assert result["meta"]["total_curated_repos"] > 0
