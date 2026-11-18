"""Tests for reciprocal rank fusion."""

import pytest

from fusion import competition_ranks, reciprocal_rank_fusion


# ========== competition ranking ==========

def test_equal_scores_share_a_rank():
    """The whole point: a tie must stay a tie.

    Naive enumeration would make these ranks 1 and 2 and invent a relevance
    difference out of row order.
    """
    assert competition_ranks([("a", 10.0), ("b", 10.0)]) == [("a", 1), ("b", 1)]


def test_competition_ranks_skip_after_a_tie():
    """Competition style (1, 2, 2, 4): rank is the true position."""
    ranks = competition_ranks([("a", 9.0), ("b", 5.0), ("c", 5.0), ("d", 1.0)])
    assert ranks == [("a", 1), ("b", 2), ("c", 2), ("d", 4)]


def test_competition_ranks_sorts_unsorted_input():
    ranks = competition_ranks([("low", 1.0), ("high", 9.0)])
    assert ranks == [("high", 1), ("low", 2)]


# ========== fusion arithmetic ==========

def test_agreement_beats_a_single_strong_vote():
    """A document two retrievers both like beats one only one retriever ranks.

    This is the behaviour hybrid search is bought for.
    """
    fused = dict(reciprocal_rank_fusion([
        (1.0, ["both", "solo_a"]),
        (1.0, ["solo_b", "both"]),
    ], k=60))
    assert fused["both"] > fused["solo_a"]
    assert fused["both"] > fused["solo_b"]


def test_weights_shift_the_outcome():
    """Weighting one retriever up promotes the document only it ranks first."""
    lists = [["x", "y"], ["y", "x"]]
    heavy_first = dict(reciprocal_rank_fusion(
        [(5.0, lists[0]), (1.0, lists[1])], k=60))
    heavy_second = dict(reciprocal_rank_fusion(
        [(1.0, lists[0]), (5.0, lists[1])], k=60))
    assert heavy_first["x"] > heavy_first["y"]
    assert heavy_second["y"] > heavy_second["x"]


def test_zero_weight_disables_a_retriever_entirely():
    """A disabled retriever must not even introduce its documents."""
    fused = reciprocal_rank_fusion([(0.0, ["ghost"]), (1.0, ["real"])], k=60)
    assert [doc for doc, _ in fused] == ["real"]


def test_k_controls_how_much_rank_one_wins_by():
    """Small k is steep and discriminating; large k is flat.

    This is the knob that decides whether any downstream signal can
    meaningfully compete with a rank difference.
    """
    steep = dict(reciprocal_rank_fusion([(1.0, ["first", "second"])], k=0))
    flat = dict(reciprocal_rank_fusion([(1.0, ["first", "second"])], k=1000))

    steep_gap = steep["first"] / steep["second"]
    flat_gap = flat["first"] / flat["second"]
    assert steep_gap == pytest.approx(2.0)
    assert flat_gap < 1.01


def test_scored_and_unscored_forms_agree_when_there_are_no_ties():
    ids = ["a", "b", "c"]
    scored = [("a", 9.0), ("b", 5.0), ("c", 1.0)]
    assert reciprocal_rank_fusion([(1.0, ids)], k=60) == \
        reciprocal_rank_fusion([(1.0, scored)], k=60)


def test_tie_in_scored_form_produces_equal_fused_scores():
    """Equal input scores must come out as equal fused scores.

    Without this, a caller cannot break the tie on its own criteria, which is
    how popularity gets its say in search.search_campsites.
    """
    fused = dict(reciprocal_rank_fusion([(1.0, [("a", 7.0), ("b", 7.0)])], k=60))
    assert fused["a"] == fused["b"]


def test_empty_input_is_empty_output():
    assert reciprocal_rank_fusion([], k=60) == []
    assert reciprocal_rank_fusion([(1.0, [])], k=60) == []


def test_output_is_sorted_descending():
    fused = reciprocal_rank_fusion([(1.0, ["a", "b", "c", "d"])], k=60)
    scores = [score for _, score in fused]
    assert scores == sorted(scores, reverse=True)


def test_ties_are_ordered_by_first_appearance_not_dict_order():
    """Determinism: the same inputs must always give the same output order."""
    first = reciprocal_rank_fusion([(1.0, [("zz", 1.0), ("aa", 1.0)])], k=60)
    assert [doc for doc, _ in first] == ["zz", "aa"]


def test_negative_k_is_rejected():
    """k=-1 would make rank 1 divide by zero and rank 2 score negatively."""
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([(1.0, ["a"])], k=-1)


def test_k_zero_is_allowed():
    """k=0 is the steepest valid curve, not an error."""
    fused = dict(reciprocal_rank_fusion([(1.0, ["a", "b"])], k=0))
    assert fused["a"] == pytest.approx(1.0)
    assert fused["b"] == pytest.approx(0.5)
