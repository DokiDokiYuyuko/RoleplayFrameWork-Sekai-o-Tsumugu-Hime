from mrp.benchmarks.long_story import run_baseline


def test_synthetic_long_story_baseline_preserves_evidence_and_documents_fts_gap(tmp_path):
    result = run_baseline(tmp_path)
    assert result["fixture"]["turns"] >= 200
    assert result["fixture"]["scenes"] == 4
    assert result["fixture"]["embedding"] is None
    assert result["isolation_assertions_passed"]
    assert result["capability_passed"] == 9 and result["case_count"] == 10
    gap = next(row for row in result["cases"] if row["kind"] == "known_recall_limitation")
    assert not gap["passed"] and "bronze-bell" not in gap["returned_ids"]
    assert "bronze-bell" in result["raw_fts_queries"][0]["returned_ids"]
    assert "bronze-bell" not in result["raw_fts_queries"][1]["returned_ids"]
