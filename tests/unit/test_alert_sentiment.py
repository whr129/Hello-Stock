from datetime import UTC, datetime, timedelta

import pytest

from news_agent.alerts.sentiment import (
    SentimentObservation,
    aggregate_windows,
    evaluate_change,
)

NOW = datetime(2026, 10, 30, 18, tzinfo=UTC)


def observation(article, label="negative", *, hours=1, **kwargs):
    published = NOW - timedelta(hours=hours)
    return SentimentObservation(
        **{
            "article_id": article,
            "ticker": "NVDA",
            "provider": "jev",
            "model_version": "1.14",
            "label": label,
            "source_id": article,
            "published_at": published,
            "first_seen_at": published + timedelta(minutes=5),
            **kwargs,
        }
    )


def windows(items, **kwargs):
    return aggregate_windows(
        items,
        **{
            "ticker": "NVDA",
            "provider": "jev",
            "model_version": "1.14",
            "as_of": NOW,
            **kwargs,
        },
    )


def covered_change():
    return [
        *(observation(f"old-{i}", "positive", hours=30 + i) for i in range(3)),
        *(observation(f"new-{i}", "negative", hours=1 + i / 10) for i in range(3)),
    ]


def test_windows_use_publication_boundaries_and_first_seen_availability():
    items = [
        observation("current-start", published_at=NOW - timedelta(hours=24)),
        observation("prior-start", published_at=NOW - timedelta(hours=48)),
        observation("before-prior", hours=49),
        observation("at-end", published_at=NOW),
        observation("not-known", first_seen_at=NOW + timedelta(seconds=1)),
        observation("other-company", ticker="AMD"),
        observation("other-provider", provider="polygon"),
        observation("other-model", model_version="1.13"),
    ]
    result = windows(items)
    assert result.current.story_count == 1
    assert result.prior.story_count == 1
    assert result.current.start == result.prior.end == NOW - timedelta(hours=24)


def test_duplicates_and_syndication_count_as_one_story_and_show_mixed_evidence():
    first = observation("a", "positive", event_id="event-1", source_id="publisher-1")
    second = observation("b", "negative", event_id="event-1", source_id="publisher-2")
    result = windows([first, first, second])
    assert result.current.story_count == 1
    assert result.current.source_count == 2
    assert result.current.counts["mixed"] == 1
    assert result.current.mean == 0
    assert result.current.disagreement == 1
    assert result.watermark == windows([second, first]).watermark


def test_neutral_mixed_and_unknown_are_distinct():
    result = windows(
        [
            observation("neutral", "neutral"),
            observation("mixed", "mixed"),
            observation("unknown", "unknown"),
        ]
    )
    assert result.current.observed_count == 3
    assert result.current.story_count == 2
    assert result.current.shares["neutral"] == 0.5
    assert result.current.shares["mixed"] == 0.5
    assert result.current.disagreement == 0.5
    empty = windows([observation("unknown", "unknown")])
    assert empty.current.mean is None
    assert empty.current.source_count == 0


def test_provider_revision_is_available_only_after_its_first_seen_timestamp():
    first = observation("a", "positive")
    revised = observation("a", "negative", revision=2, first_seen_at=NOW + timedelta(minutes=1))
    assert windows([first, revised]).current.mean == 1
    after = windows([first, revised], as_of=NOW + timedelta(minutes=2))
    assert after.current.mean == -1
    assert after.latest_eligible_at is None


def test_conflicting_same_revision_is_unknown_regardless_of_arrival_order():
    positive = observation("a", "positive")
    negative = observation("a", "negative")
    first = windows([positive, negative, positive])
    second = windows([negative, positive, negative])
    assert first == second
    assert first.current.story_count == 0
    assert first.latest_eligible_at is None


def test_old_event_reprint_does_not_become_a_new_current_story():
    result = windows(
        [
            observation("original", hours=49, event_id="old-event"),
            observation("reprint", hours=1, event_id="old-event"),
        ]
    )
    assert result.current.story_count == result.prior.story_count == 0


def test_change_requires_distinct_new_batches_and_does_not_fire_on_repeat_polling():
    items = covered_change()
    first = evaluate_change(windows(items))
    assert not first.eligible
    assert first.reason == "awaiting_confirmation"
    assert first.delta == -2
    repeated = evaluate_change(windows(items), previous=first.state)
    assert not repeated.eligible
    assert repeated.reason == "no_new_batch"
    assert repeated.state.consecutive_batches == 1
    second = evaluate_change(
        windows(
            [
                *items,
                observation("fresh", hours=0.2, first_seen_at=NOW - timedelta(minutes=5)),
            ]
        ),
        previous=repeated.state,
    )
    assert second.eligible
    assert second.reason == "sustained_change"
    assert second.state.last_alert_at is None


def test_late_and_revised_evidence_does_not_confirm_a_change():
    items = covered_change()
    first = evaluate_change(windows(items))
    late = observation("late", hours=5, first_seen_at=NOW - timedelta(minutes=1))
    result = evaluate_change(windows([*items, late]), previous=first.state)
    assert not result.eligible
    assert result.reason == "no_new_batch"
    assert result.state.consecutive_batches == 1


def test_low_coverage_outage_or_model_change_resets_confirmation():
    initial = evaluate_change(windows(covered_change()))
    outage = evaluate_change(
        windows(covered_change()), previous=initial.state, provider_healthy=False
    )
    assert outage.reason == "provider_unavailable"
    assert outage.state.consecutive_batches == 0
    low = evaluate_change(windows([observation("one")]), previous=initial.state)
    assert low.reason == "insufficient_coverage"
    assert low.state.consecutive_batches == 0
    changed = [item.model_copy(update={"model_version": "1.15"}) for item in covered_change()]
    new_model = evaluate_change(windows(changed, model_version="1.15"), previous=initial.state)
    assert not new_model.eligible
    assert new_model.state.consecutive_batches == 1


def test_stale_evidence_and_cooldown_block_delivery_candidates():
    items = covered_change()
    first = evaluate_change(windows(items))
    next_items = [*items, observation("fresh", hours=0.2, first_seen_at=NOW)]
    state = first.state.model_copy(update={"last_alert_at": NOW - timedelta(minutes=20)})
    cooling = evaluate_change(windows(next_items), previous=state)
    assert cooling.reason == "cooldown"
    assert not cooling.eligible
    stale = evaluate_change(windows(items, as_of=NOW + timedelta(hours=3)))
    assert stale.reason == "stale_evidence"


def test_all_same_source_fails_independent_coverage():
    items = [item.model_copy(update={"source_id": "single-publisher"}) for item in covered_change()]
    assert evaluate_change(windows(items)).reason == "insufficient_coverage"


def test_naive_timestamps_and_invalid_limits_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        observation("a", first_seen_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="limits"):
        evaluate_change(windows([]), change_threshold=float("nan"))
    with pytest.raises(ValueError, match="limits"):
        windows([], window=timedelta(0))
