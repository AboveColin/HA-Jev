"""The AI Task structure, mapped onto the three question types and back.

A structure is what a script writer types into an action. It is refused here or it
is answered, and every refusal has to name the field and what to do about it: the
person reading it is looking at a YAML block, not at this code.
"""

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import selector
from jevclient import Choice, ChoiceAnswer, Noul, NoulAnswer, Score, ScoreAnswer

from custom_components.jev.structure import (
    questions_from_structure,
    values_from_answers,
)


def structure(**fields) -> vol.Schema:
    """A structure the way ai_task builds one: markers, descriptions, selectors."""
    return vol.Schema(
        {
            vol.Required(name, description=description): selector.selector(config)
            for name, (description, config) in fields.items()
        }
    )


def test_a_boolean_field_is_a_noul():
    questions = questions_from_structure(
        structure(window_open=("Is the window open?", {"boolean": {}}))
    )
    assert questions == {"window_open": Noul(instructions="Is the window open?")}


def test_a_select_field_is_a_choice_over_its_own_values():
    questions = questions_from_structure(
        structure(room=("Which room?", {"select": {"options": ["hall", "attic"]}}))
    )
    assert questions == {
        "room": Choice(instructions="Which room?", criteria={"hall": None, "attic": None})
    }


def test_a_select_of_value_and_label_pairs_asks_about_the_value():
    """The value is what the caller reads out of the result, so it is what is asked."""
    questions = questions_from_structure(
        structure(
            room=(
                "Which room?",
                {
                    "select": {
                        "options": [
                            {"value": "hall", "label": "Hallway"},
                            {"value": "attic", "label": "Attic"},
                        ]
                    }
                },
            )
        )
    )
    assert list(questions["room"].criteria) == ["hall", "attic"]


def test_a_number_field_is_a_score_with_labelled_levels():
    questions = questions_from_structure(
        structure(
            urgency=(
                "How urgent is this?",
                {"number": {"min": 1, "max": 5, "step": 1, "unit_of_measurement": "h"}},
            )
        )
    )
    assert questions["urgency"] == Score(
        instructions="How urgent is this?",
        criteria=["1h", "2h", "3h", "4h", "5h"],
    )


def test_a_scale_finer_than_jev_allows_is_cut_into_the_levels_it_does_allow():
    """0 to 100 is 101 values and a score takes 10. Every rung is still a legal one."""
    questions = questions_from_structure(
        structure(percent=("How full?", {"number": {"min": 0, "max": 100, "step": 1}}))
    )
    assert questions["percent"].criteria == [
        "0",
        "11",
        "22",
        "33",
        "44",
        "56",
        "67",
        "78",
        "89",
        "100",
    ]


@pytest.mark.parametrize(
    "config",
    [
        {"min": 0, "max": 100, "step": 1},
        {"min": 0, "max": 10, "step": 1},
        {"min": 1, "max": 5, "step": 1, "unit_of_measurement": "h"},
        {"min": 16, "max": 24, "step": 0.5, "unit_of_measurement": "\u00b0C"},
        {"min": 0, "max": 1, "step": "any"},
    ],
)
def test_a_rung_is_labelled_with_the_number_it_hands_back(config):
    """The legend and the value are both visible, so they have to agree.

    `nearest_level` carries the label into the result beside the value. A legend
    reading 1.11 next to a value of 1 is two answers to one question.
    """
    schema = structure(level=("How full?", {"number": config}))
    levels = list(questions_from_structure(schema)["level"].criteria)
    unit = config.get("unit_of_measurement", "")
    legend = dict(enumerate(levels))
    for index, label in enumerate(levels):
        answer = ScoreAnswer(
            score=float(index),
            legend={str(k): v for k, v in legend.items()},
            probabilities={},
            confidence=0.9,
        )
        value = values_from_answers(schema, {"level": answer})["level"]
        assert f"{value}{unit}" == label
    assert len(set(levels)) == len(levels)


def test_a_field_with_no_description_asks_about_its_own_name():
    questions = questions_from_structure(structure(window_open=(None, {"boolean": {}})))
    assert questions["window_open"].instructions == "window_open"


def test_the_result_key_cannot_be_taken_by_a_field():
    with pytest.raises(ServiceValidationError) as err:
        questions_from_structure(structure(jev=("Anything", {"boolean": {}})))
    assert err.value.translation_key == "reserved_field"
    assert err.value.translation_placeholders == {"field": "jev"}


def test_a_structure_with_no_fields_is_refused():
    with pytest.raises(ServiceValidationError) as err:
        questions_from_structure(vol.Schema({}))
    assert err.value.translation_key == "no_fields"


def test_a_selector_jev_cannot_answer_names_itself_and_the_three_that_work():
    with pytest.raises(ServiceValidationError) as err:
        questions_from_structure(structure(note=("Write a note", {"text": {}})))
    assert err.value.translation_key == "unsupported_field"
    assert err.value.translation_placeholders == {
        "field": "note",
        "selector": "TextSelector",
    }


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            {"select": {"options": ["only"]}},
            ("field_options_out_of_range", {"count": "1"}),
        ),
        ({"number": {"min": 0}}, ("field_needs_scale", {"bound": "max"})),
        ({"number": {"max": 10}}, ("field_needs_scale", {"bound": "min"})),
        (
            {"number": {"min": 10, "max": 10}},
            ("field_scale_not_ordered", {"min": "10", "max": "10"}),
        ),
    ],
)
def test_a_field_jev_cannot_be_asked_is_refused_before_a_request_is_spent(
    config, expected
):
    key, placeholders = expected
    with pytest.raises(ServiceValidationError) as err:
        questions_from_structure(structure(field=("A field", config)))
    assert err.value.translation_key == key
    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["field"] == "field"
    assert placeholders.items() <= err.value.translation_placeholders.items()


@pytest.mark.parametrize(
    ("probability", "expected"), [(0.81, True), (0.5, True), (0.49, False)]
)
def test_a_noul_becomes_a_boolean_at_the_halfway_mark(probability, expected):
    schema = structure(window_open=("Is the window open?", {"boolean": {}}))
    values = values_from_answers(schema, {"window_open": NoulAnswer(noul=probability)})
    assert values == {"window_open": expected}


def test_a_choice_comes_back_as_the_option_itself():
    schema = structure(room=("Which room?", {"select": {"options": ["hall", "attic"]}}))
    values = values_from_answers(
        schema,
        {
            "room": ChoiceAnswer(
                choice="attic", probabilities={"hall": 0.1, "attic": 0.9}, confidence=0.9
            )
        },
    )
    assert values == {"room": "attic"}


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.0, 1), (4.0, 5), (2.0, 3), (2.4, 3), (2.6, 4)],
)
def test_a_score_lands_back_on_the_callers_own_scale(score, expected):
    """The score is 0 to 4 over five levels. The caller asked for 1 to 5."""
    schema = structure(
        urgency=("How urgent?", {"number": {"min": 1, "max": 5, "step": 1}})
    )
    legend = {str(index): f"{index + 1}" for index in range(5)}
    values = values_from_answers(
        schema,
        {
            "urgency": ScoreAnswer(
                score=score, legend=legend, probabilities={}, confidence=0.8
            )
        },
    )
    assert values == {"urgency": expected}
    assert isinstance(values["urgency"], int)


def test_a_continuous_number_keeps_its_fraction():
    schema = structure(
        ratio=("How far along?", {"number": {"min": 0, "max": 1, "step": "any"}})
    )
    legend = {str(index): str(index) for index in range(5)}
    values = values_from_answers(
        schema,
        {
            "ratio": ScoreAnswer(
                score=1.0, legend=legend, probabilities={}, confidence=0.5
            )
        },
    )
    assert values == {"ratio": 0.25}


def test_an_answer_that_never_came_is_an_error_rather_than_a_none():
    schema = structure(window_open=("Is the window open?", {"boolean": {}}))
    with pytest.raises(ServiceValidationError) as err:
        values_from_answers(schema, {})
    assert err.value.translation_key == "answer_missing"
    assert err.value.translation_placeholders == {"field": "window_open"}


def test_an_answer_of_the_wrong_shape_says_which_two_do_not_fit():
    schema = structure(window_open=("Is the window open?", {"boolean": {}}))
    with pytest.raises(ServiceValidationError) as err:
        values_from_answers(
            schema,
            {
                "window_open": ChoiceAnswer(
                    choice="hall", probabilities={"hall": 1.0}, confidence=1.0
                )
            },
        )
    assert err.value.translation_key == "answer_does_not_fit"
    assert err.value.translation_placeholders == {
        "field": "window_open",
        "answer": "ChoiceAnswer",
        "selector": "BooleanSelector",
    }


def test_a_select_that_repeats_one_option_is_one_option():
    """Two copies of one value passed the minimum of two, and were one answer."""
    with pytest.raises(ServiceValidationError) as err:
        questions_from_structure(
            structure(field=("A field", {"select": {"options": ["same", "same"]}}))
        )
    assert err.value.translation_key == "field_options_out_of_range"
    assert err.value.translation_placeholders["count"] == "1"
