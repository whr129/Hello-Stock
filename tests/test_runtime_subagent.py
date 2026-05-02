from news_agent.domains.runtime.subagent import _extract_error_query, _is_generic_error_query


def test_extract_error_query_strips_generic_followup_words() -> None:
    query = _extract_error_query("what was the error?")

    assert query == ""


def test_single_word_followup_is_treated_as_generic() -> None:
    assert _is_generic_error_query("reuters") is True
    assert _is_generic_error_query("") is True


def test_specific_error_query_is_not_generic() -> None:
    assert _is_generic_error_query("reuters forbidden") is False
