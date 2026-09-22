"""Questions added in the UI, and the call grouping derived from them."""

import pytest
from homeassistant.config_entries import ConfigSubentry, ConfigSubentryData
from homeassistant.const import CONF_API_KEY
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from jevclient import Choice, ChoiceAnswer, NoulAnswer, Score, ScoreAnswer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.const import DOMAIN, SUBENTRY_QUESTION
from custom_components.jev.subentry import parse_levels, parse_options

from .conftest import API_KEY, build_response


def question(name, kind="noul", **extra):
    """A subentry shaped the way the flow writes one."""
    data = {
        "name": name,
        "type": kind,
        "instructions": f"Is {name} the case?",
        "target": {"entity_id": ["sensor.washer_power"]},
        "scan_interval": 300,
        "include_attributes": False,
        **extra,
    }
    return ConfigSubentryData(
        data=data, subentry_type=SUBENTRY_QUESTION, title=name, unique_id=None
    )


@pytest.fixture
def entry_with(request):
    """A config entry carrying whichever subentries the test asks for."""

    def build(*subentries):
        return MockConfigEntry(
            domain=DOMAIN,
            title="Jev",
            data={CONF_API_KEY: API_KEY},
            options={},
            unique_id="0123456789abcdef",
            subentries_data=list(subentries),
        )

    return build


async def setup(hass, entry):
    hass.states.async_set("sensor.washer_power", "1.2", {"friendly_name": "Washer"})
    hass.states.async_set("sensor.door", "closed", {"friendly_name": "Door"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# --- reading the text boxes ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "delivery: a courier\nvisitor: someone we know",
            {"delivery": "a courier", "visitor": "someone we know"},
        ),
        ("yes\nno", {"yes": None, "no": None}),
        ("a: one\n\n  b  :  two  \n", {"a": "one", "b": "two"}),
        ("only_one:", {"only_one": None}),
    ],
)
def test_options_are_read_one_per_line(text, expected):
    assert parse_options(text) == expected


def test_levels_keep_their_order():
    """Score reads levels lowest first, so the order in the box is the order sent."""
    assert parse_levels("Never\nWhenever\nRight now\n\n") == [
        "Never",
        "Whenever",
        "Right now",
    ]


# --- the flow ---


async def test_the_menu_offers_one_step_per_answer_type(hass, mock_client, config_entry):
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_QUESTION), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"noul", "choice", "score"}


async def test_adding_a_question_makes_its_sensor(hass, mock_client, config_entry):
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.washer_power", "1.2", {"friendly_name": "Washer"})

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_QUESTION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "noul"}
    )
    assert result["type"] is FlowResultType.FORM

    mock_client.ask.return_value = build_response(**{"ui_x": NoulAnswer(noul=0.81)})
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Laundry forgotten",
            "instructions": "Is the laundry finished but still in the machine?",
            "target": {"entity_id": ["sensor.washer_power"]},
            "threshold": 0.7,
            "advanced": {"scan_interval": 300, "include_attributes": False},
        },
    )
    # The preview comes before the entry exists, which is the point of it.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "preview"
    assert "sensor.washer_power" in result["description_placeholders"]["state"]

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Laundry forgotten"
    await hass.async_block_till_done()
    assert len(config_entry.subentries) == 1


async def test_a_question_with_nothing_to_look_at_is_refused(
    hass, mock_client, config_entry
):
    """A question needs a state. Neither a target nor a note is not a question."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_QUESTION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "noul"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Nothing",
            "instructions": "Is it?",
            "advanced": {"scan_interval": 300, "include_attributes": False},
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "nothing_to_judge"}


async def test_a_choice_needs_at_least_two_options(hass, mock_client, config_entry):
    """The limit is the library's, and the error names it rather than failing later."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_QUESTION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "choice"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Caller",
            "instructions": "Who is at the door?",
            "target": {"entity_id": ["sensor.washer_power"]},
            "options": "delivery: a courier",
            "advanced": {"scan_interval": 300, "include_attributes": False},
        },
    )
    assert result["errors"] == {"options": "choice_options_out_of_range"}


# --- the grouping, which is the whole point ---


async def test_questions_that_share_a_state_share_one_request(
    hass, mock_client, entry_with
):
    """Same readings, same schedule, one call.

    This is what the YAML surface made the user declare as a context. Two questions
    can share a request exactly when they describe the same state, so the
    integration works it out instead of asking.
    """
    entry = entry_with(question("First"), question("Second"), question("Third"))
    await setup(hass, entry)

    assert mock_client.ask.await_count == 2, "setup probe plus one grouped evaluation"
    questions = mock_client.ask.await_args.args[1]
    assert len(questions) == 3


async def test_questions_looking_at_different_things_cannot_share(
    hass, mock_client, entry_with
):
    """The API takes one state per request, so a different target is a different call."""
    entry = entry_with(
        question("Washer"),
        question("Door", target={"entity_id": ["sensor.door"]}),
    )
    await setup(hass, entry)

    sent = [c.args[1] for c in mock_client.ask.await_args_list[1:]]
    assert len(sent) == 2
    assert all(len(q) == 1 for q in sent)


async def test_a_different_schedule_is_a_different_call(hass, mock_client, entry_with):
    entry = entry_with(question("Often"), question("Rarely", scan_interval=900))
    await setup(hass, entry)
    assert mock_client.ask.await_count == 3, "probe plus one call per schedule"


async def test_a_choice_subentry_builds_a_choice_question(hass, mock_client, entry_with):
    entry = entry_with(
        question("Caller", kind="choice", options="delivery: a courier\nvisitor: known")
    )
    await setup(hass, entry)

    asked = mock_client.ask.await_args.args[1]
    [question_sent] = asked.values()
    assert isinstance(question_sent, Choice)
    assert set(question_sent.criteria) == {"delivery", "visitor"}


async def test_a_score_subentry_keeps_the_level_order(hass, mock_client, entry_with):
    entry = entry_with(
        question("Urgency", kind="score", levels="Never\nWhenever\nRight now")
    )
    await setup(hass, entry)

    asked = mock_client.ask.await_args.args[1]
    [question_sent] = asked.values()
    assert isinstance(question_sent, Score)
    assert list(question_sent.criteria) == ["Never", "Whenever", "Right now"]


async def test_yaml_and_ui_questions_live_side_by_side(hass, mock_client, entry_with):
    """Existing YAML keeps working, so upgrading breaks nobody's configuration."""
    from homeassistant.setup import async_setup_component

    hass.states.async_set("sensor.washer_power", "1.2", {"friendly_name": "Washer"})
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: [
                {
                    "name": "From YAML",
                    "entities": ["sensor.washer_power"],
                    "questions": [
                        {
                            "name": "Still declared here",
                            "type": "noul",
                            "instructions": "Is it?",
                        }
                    ],
                }
            ]
        },
    )
    entry = entry_with(question("From the UI"))
    await setup(hass, entry)

    assert len(entry.runtime_data.coordinators) == 2


async def test_questions_added_in_the_same_second_keep_separate_entities(
    hass, mock_client, entry_with
):
    """Subentry ids are ULIDs, so their first ten characters are a timestamp.

    A key built from that prefix gave three questions added together the same key,
    and they collapsed into one before anything reached the API. The trailing
    characters are the random half, so the key has to come from there.
    """
    # Fixed ids with one timestamp prefix. Real ULIDs only share it when all three
    # fall in the same millisecond, and a test that depends on that is flaky.
    prefix = "01M35664T4"
    entry = entry_with(
        *(
            ConfigSubentryData(**question(name), subentry_id=f"{prefix}{suffix}")
            for name, suffix in (
                ("First", "AAAAAAAAAAAAAAAA"),
                ("Second", "BBBBBBBBBBBBBBBB"),
                ("Third", "CCCCCCCCCCCCCCCC"),
            )
        )
    )
    await setup(hass, entry)

    [context] = entry.runtime_data.coordinators.values()
    keys = [q.key for q in context.context_config.questions]
    assert len(set(keys)) == 3, f"question keys collided: {keys}"


async def test_adding_a_question_reloads_so_its_sensor_appears(
    hass, mock_client, entry_with
):
    """A question you cannot see until you restart is not configured in the UI."""
    entry = entry_with(question("First"))
    await setup(hass, entry)
    assert len(entry.runtime_data.coordinators) == 1

    hass.config_entries.async_add_subentry(
        entry,
        __import__(
            "homeassistant.config_entries", fromlist=["ConfigSubentry"]
        ).ConfigSubentry(
            data=question("Second")["data"],
            subentry_type=SUBENTRY_QUESTION,
            title="Second",
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()

    questions = [
        q.name
        for c in entry.runtime_data.coordinators.values()
        for q in c.context_config.questions
    ]
    assert sorted(questions) == ["First", "Second"]


# --- the preview ---


async def test_the_preview_shows_the_readings_that_will_be_sent(hass):
    """What the screen shows is what the coordinator sends, same call, same output."""
    from custom_components.jev.subentry import build_preview

    hass.states.async_set("sensor.washer_power", "1.2", {"friendly_name": "Washer"})
    shown, _state = await build_preview(
        hass, {"target": {"entity_id": ["sensor.washer_power"]}}
    )
    assert "sensor.washer_power" in shown
    assert "1.2" in shown


async def test_the_preview_renders_the_template_rather_than_echoing_it(hass):
    from custom_components.jev.subentry import build_preview

    hass.states.async_set("sensor.washer_power", "1450", {})
    shown, _state = await build_preview(
        hass, {"state": "{{ states('sensor.washer_power') }} watts right now"}
    )
    assert shown == "1450 watts right now"


async def test_a_broken_template_is_shown_not_raised(hass):
    """A typo is exactly what this screen exists to catch, so it must not crash it."""
    from custom_components.jev.subentry import build_preview

    shown, _state = await build_preview(hass, {"state": "{{ this is not valid jinja"})
    assert "does not render" in shown


async def test_too_many_entities_reports_the_limit_before_saving(hass):
    """The cap names the limit and the ask, and now it does so before the cost."""
    from custom_components.jev.const import MAX_TARGET_ENTITIES
    from custom_components.jev.subentry import build_preview

    ids = []
    for i in range(MAX_TARGET_ENTITIES + 5):
        entity_id = f"sensor.probe_{i}"
        hass.states.async_set(entity_id, "1")
        ids.append(entity_id)

    shown, _state = await build_preview(hass, {"target": {"entity_id": ids}})
    assert str(MAX_TARGET_ENTITIES) in shown
    assert "too_many_entities" not in shown, "the key leaked, not the sentence"


async def test_a_long_state_is_summarised(hass):
    from custom_components.jev.subentry import PREVIEW_CHARS, build_preview

    ids = []
    for i in range(60):
        entity_id = f"sensor.long_{i}"
        hass.states.async_set(entity_id, "1", {"friendly_name": f"Long sensor {i}"})
        ids.append(entity_id)

    shown, _state = await build_preview(hass, {"target": {"entity_id": ids}})
    assert "more characters" in shown
    assert "60 entities in total" in shown
    assert len(shown) < PREVIEW_CHARS + 200


async def test_the_preview_names_what_it_will_be_grouped_with(
    hass, mock_client, entry_with
):
    """The grouping is derived, which makes it invisible unless it is said."""
    from custom_components.jev.subentry import describe_grouping

    entry = entry_with(question("First"), question("Second"))
    await setup(hass, entry)

    alone = await describe_grouping(
        hass,
        entry,
        {"target": {"entity_id": ["sensor.door"]}, "scan_interval": 300},
        None,
    )
    assert alone == "Sent as its own request."

    together = await describe_grouping(
        hass,
        entry,
        {
            "target": {"entity_id": ["sensor.washer_power"]},
            "scan_interval": 300,
            "include_attributes": False,
        },
        None,
    )
    assert together == "Sent in one request together with: First, Second."


# --- the trial answer ---


def test_a_probability_is_drawn_as_a_bar():
    from custom_components.jev.subentry import render_answer

    drawn = render_answer(NoulAnswer(noul=0.81), None, _say)
    assert "0.81" in drawn
    assert drawn.count("█") == 15, "18 blocks scaled by 0.81"
    assert drawn.count("░") == 3


def test_a_threshold_says_which_way_the_binary_sensor_would_go():
    from custom_components.jev.subentry import render_answer

    assert "would be **on**" in render_answer(NoulAnswer(noul=0.81), 0.7, _say)
    assert "would be **off**" in render_answer(NoulAnswer(noul=0.42), 0.7, _say)


def test_a_choice_draws_every_option_with_the_winner_marked():
    from custom_components.jev.subentry import render_answer

    drawn = render_answer(
        ChoiceAnswer(
            choice="delivery",
            probabilities={"delivery": 0.81, "visitor": 0.14, "sales": 0.05},
            confidence=0.92,
        ),
        None,
        _say,
    )
    assert "**delivery**" in drawn
    assert "visitor" in drawn and "**visitor**" not in drawn
    # Highest first, so the eye lands on the winner without reading the numbers.
    assert drawn.index("delivery") < drawn.index("visitor") < drawn.index("sales")
    assert "confidence 0.92" in drawn


async def test_the_trial_answer_reaches_the_form(hass, mock_client, config_entry):
    """A state preview says what will be read, not whether the question works."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.washer_power", "1450", {"friendly_name": "Washer"})
    mock_client.ask.return_value = build_response(preview=NoulAnswer(noul=0.81))

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_QUESTION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "noul"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Running",
            "instructions": "Is a programme running?",
            "target": {"entity_id": ["sensor.washer_power"]},
            "threshold": 0.7,
            "advanced": {"scan_interval": 300, "include_attributes": False},
        },
    )
    answer = result["description_placeholders"]["answer"]
    assert "0.81" in answer
    assert "would be **on**" in answer
    assert "input tokens" in answer, "the preview says what it cost"


async def test_a_failed_trial_answer_does_not_block_saving(
    hass, mock_client, config_entry
):
    """The preview is a convenience. It must never stand between you and a save."""
    from jevclient import JevError

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.washer_power", "1450", {})
    mock_client.ask.side_effect = JevError("upstream is down")

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_QUESTION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "noul"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Running",
            "instructions": "Is a programme running?",
            "target": {"entity_id": ["sensor.washer_power"]},
            "advanced": {"scan_interval": 300, "include_attributes": False},
        },
    )
    assert "No trial answer" in result["description_placeholders"]["answer"]

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


def _say(key, **placeholders):
    """Stand in for the catalogue lookup, using the same fallback text."""
    from custom_components.jev.subentry import _PREVIEW_FALLBACK

    return _PREVIEW_FALLBACK[key].format(**placeholders)


def test_the_preview_fallbacks_match_the_english_catalogue():
    """Two wordings for the same sentence means one of them is never edited."""
    import json
    import pathlib

    from custom_components.jev.subentry import _PREVIEW_FALLBACK

    root = pathlib.Path(__file__).parent.parent / "custom_components/jev"
    common = json.loads((root / "strings.json").read_text())["common"]
    for key, text in _PREVIEW_FALLBACK.items():
        assert common[key] == text, f"{key} drifted from strings.json"


def test_every_preview_sentence_is_translated():
    """A Dutch dialog answering in English is the half a user notices."""
    import json
    import pathlib

    from custom_components.jev.subentry import _PREVIEW_FALLBACK

    root = pathlib.Path(__file__).parent.parent / "custom_components/jev"
    for path in sorted((root / "translations").glob("*.json")):
        common = json.loads(path.read_text()).get("common", {})
        missing = set(_PREVIEW_FALLBACK) - set(common)
        assert not missing, f"{path.name} is missing {sorted(missing)}"


# --- editing a question that already exists ---


async def test_a_question_can_be_edited_in_the_ui(hass, mock_client, entry_with):
    """reconfiguration-flow is a claimed quality rule, so it needs a test."""
    entry = entry_with(question("Before", kind="score", levels="Low\nHigh"))
    await setup(hass, entry)
    [subentry] = entry.subentries.values()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_QUESTION),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    mock_client.ask.return_value = build_response(preview=NoulAnswer(noul=0.5))
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "After",
            "instructions": "Changed my mind about the wording",
            "target": {"entity_id": ["sensor.washer_power"]},
            "levels": "Low\nMedium\nHigh",
            "advanced": {"scan_interval": 600, "include_attributes": False},
        },
    )
    assert result["step_id"] == "preview", "editing shows the preview too"

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()

    [updated] = entry.subentries.values()
    assert updated.title == "After"
    assert updated.data["levels"] == "Low\nMedium\nHigh"
    assert updated.data["type"] == "score", "the type is kept, not re-asked"
    assert len(entry.subentries) == 1, "editing must not create a second question"


async def test_editing_rejects_a_change_that_breaks_the_limits(
    hass, mock_client, entry_with
):
    entry = entry_with(question("Levels", kind="score", levels="Low\nHigh"))
    await setup(hass, entry)
    [subentry] = entry.subentries.values()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_QUESTION),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Levels",
            "instructions": "Is it?",
            "target": {"entity_id": ["sensor.washer_power"]},
            "levels": "OnlyOne",
            "advanced": {"scan_interval": 600, "include_attributes": False},
        },
    )
    assert result["errors"] == {"levels": "score_levels_out_of_range"}


def test_a_score_draws_every_level_in_order():
    from custom_components.jev.subentry import render_answer

    drawn = render_answer(
        ScoreAnswer(
            score=2.4,
            legend={"0": "Not at all", "1": "Worth a glance", "2": "Look today"},
            probabilities={"0": 0.1, "1": 0.3, "2": 0.6},
            confidence=0.88,
        ),
        None,
        _say,
    )
    # Lowest level first, because that is the order Score reads them.
    assert drawn.index("Not at all") < drawn.index("Worth a glance")
    assert "nearest level **Look today**" in drawn
    assert "confidence 0.88" in drawn


# --- what the review found ---


def test_a_renamed_question_keeps_its_entity():
    """A key is an identity, not a label.

    It used to be the subentry id plus the slugified title, which read nicely
    and moved when the title did. Renaming a question changed its key, which
    changed its unique_id, which orphaned the entity and threw away its history.
    """
    from custom_components.jev.subentry import _question_key

    class Fake:
        subentry_id = "01M2ABCDEFGHJKMNPQRSTVWXYZ"

        def __init__(self, title):
            self.title = title

    assert _question_key(Fake("Before")) == _question_key(Fake("After"))


def test_a_repeated_option_name_is_refused():
    """Two lines named the same parse into one option, losing the other."""
    from custom_components.jev.subentry import option_problem, parse_options

    assert parse_options("a: one\na: two") == {"a": "two"}, "still lossy by itself"
    assert option_problem("a: one\na: two") == "option_name_duplicate"


def test_an_empty_option_name_is_refused():
    from custom_components.jev.subentry import option_problem

    assert option_problem(": nothing\nb: fine") == "option_name_empty"
    assert option_problem("a: one\nb: two") is None


def test_the_same_entities_in_any_order_share_one_request():
    """The picker returns lists, and a list has an order the user did not choose."""
    from custom_components.jev.subentry import _call_key

    forwards = {"target": {"entity_id": ["sensor.a", "sensor.b"]}, "scan_interval": 300}
    backwards = {"target": {"entity_id": ["sensor.b", "sensor.a"]}, "scan_interval": 300}
    assert _call_key(forwards) == _call_key(backwards)


def test_what_a_high_number_means_reaches_the_payload():
    """The form collected these two and the payload dropped them.

    The UI calls them true_means and false_means. build_question reads true and
    false, which is what YAML has always used. Without the mapping, filling in
    "what a high number means" did nothing whatsoever.
    """
    from custom_components.jev.models import build_question
    from custom_components.jev.subentry import _as_raw_question

    payload = build_question(
        _as_raw_question(
            {
                "name": "x",
                "type": "noul",
                "instructions": "Is it?",
                "true_means": "HIGH MEANS THIS",
                "false_means": "LOW MEANS THAT",
            }
        )
    ).as_payload()
    assert payload["criteria"]["true"] == "HIGH MEANS THIS"
    assert payload["criteria"]["false"] == "LOW MEANS THAT"


async def test_the_preview_does_not_spend_past_the_budget(hass, mock_client, entry_with):
    """A tripwire the preview can walk past is not a tripwire."""
    entry = entry_with(question("First"))
    await setup(hass, entry)
    hass.config_entries.async_update_entry(entry, options={"daily_token_budget": 10})
    await hass.async_block_till_done()
    entry.runtime_data.usage.input_tokens = 9_999
    mock_client.ask.reset_mock()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_QUESTION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "noul"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Costly",
            "instructions": "Is it?",
            "target": {"entity_id": ["sensor.washer_power"]},
            "advanced": {"scan_interval": 300, "include_attributes": False},
        },
    )
    assert mock_client.ask.await_count == 0, "it asked anyway"
    assert "budget" in result["description_placeholders"]["answer"]

    # Saving still works. The preview is a convenience, not a gate.
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_a_target_picked_and_removed_again_is_no_target(
    hass, mock_client, config_entry
):
    """The picker submits empty lists then, which passed as something to judge."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_QUESTION), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "noul"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Nothing",
            "instructions": "Is it?",
            "target": {"entity_id": [], "area_id": []},
            "advanced": {"scan_interval": 300, "include_attributes": False},
        },
    )
    assert result["errors"] == {"base": "nothing_to_judge"}


def _coordinator_of(entry, name):
    return next(
        c
        for c in entry.runtime_data.coordinators.values()
        if any(q.name == name for q in c.context_config.questions)
    )


async def test_adding_a_question_leaves_the_other_contexts_where_they_were(
    hass, mock_client, entry_with
):
    """The context key held its sort position, so a new question moved the others.

    The latency and payload sensors are keyed on it, and they were orphaned.
    """
    entry = entry_with(question("First"))
    await setup(hass, entry)
    before = _coordinator_of(entry, "First").context_config.key

    # A different target is a separate call, and this one sorts ahead of the first.
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data={**question("Second")["data"], "target": {"entity_id": ["sensor.door"]}},
            subentry_type=SUBENTRY_QUESTION,
            title="Second",
            unique_id=None,
        ),
    )
    await hass.async_block_till_done()

    assert len(entry.runtime_data.coordinators) == 2
    assert _coordinator_of(entry, "First").context_config.key == before


async def test_clearing_a_threshold_removes_its_binary_sensor(
    hass, mock_client, entry_with
):
    """It stayed in the registry, restored and unavailable, with nothing behind it."""
    entry = entry_with(question("First", threshold=0.7))
    await setup(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get("binary_sensor.jev_first") is not None

    [subentry] = entry.subentries.values()
    data = {k: v for k, v in subentry.data.items() if k != "threshold"}
    hass.config_entries.async_update_subentry(entry, subentry, data=data)
    await hass.async_block_till_done()

    assert registry.async_get("binary_sensor.jev_first") is None
    assert registry.async_get("sensor.jev_first") is not None
