"""Box: where something is in a picture, from jev.box or as a question in jev.ask.

The client is replaced, so these tests check what would be sent, what comes back
to the automation, and what a refusal says. The live receipts are in
site-docs/measurements.md.
"""

import io
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.components import camera
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from jevclient import Box, BoxAnswer, JevError, JevValidationError, NoulAnswer
from PIL import Image as PILImage

from custom_components.jev import QUESTION_SCHEMA
from custom_components.jev.const import DOMAIN

from .conftest import build_response

WHERE = BoxAnswer(top=0.18, bottom=0.5, left=0.59, right=0.8, confidence=0.92)


def frame(width: int = 1920, height: int = 1080) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (width, height), "white").save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def snapshot():
    """camera.async_get_image, answering a 1920 x 1080 frame."""
    shot = camera.Image("image/jpeg", frame())
    with patch.object(camera, "async_get_image", AsyncMock(return_value=shot)) as get:
        yield get


def answering(mock_client, **answers) -> None:
    mock_client.ask.reset_mock()
    mock_client.ask.side_effect = None
    mock_client.ask.return_value = build_response(**answers)


async def call(hass, action, data):
    return await hass.services.async_call(
        DOMAIN, action, data, blocking=True, return_response=True
    )


async def test_box_finds_a_thing_in_one_camera(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    hass.states.async_set("camera.front_door", "idle", {"friendly_name": "Front door"})
    answering(mock_client, answer=WHERE)
    response = await call(
        hass,
        "box",
        {
            "image": "camera.front_door",
            "instructions": "the parcel",
            "model": "jev-1.14.0",
        },
    )
    assert response["left"] == 0.59
    assert response["top"] == 0.18
    assert response["right"] == 0.8
    assert response["bottom"] == 0.5
    assert response["confidence"] == 0.92
    # The camera's own size, not the 1568 x 882 it was sent at: the edges are
    # fractions, so this is what turns them into pixels.
    assert (response["width"], response["height"]) == (1920, 1080)

    state, questions = mock_client.ask.call_args.args
    kwargs = mock_client.ask.call_args.kwargs
    assert kwargs["model"] == "jev-1.14.0"
    (ref,) = kwargs["assets"]
    assert state == {
        "images": [{"name": "Front door", "entity_id": "camera.front_door", "image": ref}]
    }
    assert questions["answer"].as_payload() == {
        "type": "box",
        "instructions": "the parcel",
    }


async def test_background_goes_with_the_box_question(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    answering(mock_client, answer=WHERE)
    await call(
        hass,
        "box",
        {
            "image": "camera.front_door",
            "instructions": "the parcel",
            "background": "Parcels are left on the mat.",
        },
    )
    (question,) = mock_client.ask.call_args.args[1].values()
    assert question.as_payload()["instructions"] == {
        "question": "the parcel",
        "background": "Parcels are left on the mat.",
    }


async def test_without_a_model_the_entry_model_is_asked(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    answering(mock_client, answer=WHERE)
    await call(hass, "box", {"image": "camera.front_door", "instructions": "the parcel"})
    assert mock_client.ask.call_args.kwargs["model"] is None


async def test_a_model_override_leaves_the_entry_version_alone(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    """The version sensor reports what the entry asks, not a one-off call."""
    runtime = loaded_entry.runtime_data
    before = runtime.model_version
    answering(mock_client, answer=WHERE)
    mock_client.ask.return_value = replace(
        build_response(answer=WHERE), model="jev-1.14.0"
    )
    await call(
        hass,
        "box",
        {"image": "camera.front_door", "instructions": "x", "model": "jev-1.14.0"},
    )
    assert runtime.model_version == before

    await call(hass, "box", {"image": "camera.front_door", "instructions": "x"})
    assert runtime.model_version == "jev-1.14.0"


async def test_a_refused_box_names_the_model_and_the_version_it_needs(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    # What jev-1.13.0 answered a Box with on 2026-10-16.
    mock_client.ask.side_effect = JevValidationError("Invalid request.", status=400)
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "box", {"image": "camera.front_door", "instructions": "x"})
    assert err.value.translation_key == "box_refused"
    assert err.value.translation_placeholders == {
        "model": loaded_entry.runtime_data.model,
        "reason": "Invalid request",
    }


async def test_a_refusal_names_the_override_when_there_is_one(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    mock_client.ask.side_effect = JevValidationError("Invalid request.", status=400)
    with pytest.raises(HomeAssistantError) as err:
        await call(
            hass,
            "box",
            {"image": "camera.front_door", "instructions": "x", "model": "jev-1.13.0"},
        )
    assert err.value.translation_placeholders["model"] == "jev-1.13.0"


async def test_other_failures_of_a_box_request_are_not_blamed_on_the_version(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    mock_client.ask.side_effect = JevError("HTTP 503: busy", status=503)
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "box", {"image": "camera.front_door", "instructions": "x"})
    assert err.value.translation_key == "ask_failed"


async def test_a_400_without_a_box_is_the_plain_failure(
    hass, loaded_entry, mock_client
) -> None:
    mock_client.ask.side_effect = JevValidationError("Invalid request.", status=400)
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "noul", {"state": "x", "instructions": "y"})
    assert err.value.translation_key == "ask_failed"


async def test_box_refuses_an_entity_with_no_picture(
    hass, loaded_entry, mock_client
) -> None:
    mock_client.ask.reset_mock()
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "box", {"image": "light.kitchen", "instructions": "x"})
    assert err.value.translation_key == "not_an_image_entity"
    mock_client.ask.assert_not_awaited()


async def test_box_takes_one_picture_only(hass, loaded_entry) -> None:
    with pytest.raises(vol.Invalid):
        await call(
            hass,
            "box",
            {"image": ["camera.a", "camera.b"], "instructions": "the parcel"},
        )


async def test_a_wrong_answer_type_is_named(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    answering(mock_client, answer=NoulAnswer(noul=0.5))
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "box", {"image": "camera.front_door", "instructions": "x"})
    assert err.value.translation_key == "wrong_answer_type"
    assert err.value.translation_placeholders == {
        "question_type": "box",
        "got": "NoulAnswer",
    }


async def test_ask_mixes_a_box_with_other_questions(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    answering(mock_client, parcel=NoulAnswer(noul=0.93), where=WHERE)
    response = await call(
        hass,
        "ask",
        {
            "images": ["camera.front_door"],
            "model": "jev-preview",
            "questions": {
                "parcel": {"type": "noul", "instructions": "Is there a parcel?"},
                "where": {"type": "box", "instructions": "the parcel"},
            },
        },
    )
    assert response["answers"]["where"] == {
        "type": "box",
        "left": 0.59,
        "top": 0.18,
        "right": 0.8,
        "bottom": 0.5,
        "confidence": 0.92,
    }
    questions = mock_client.ask.call_args.args[1]
    assert isinstance(questions["where"], Box)
    assert mock_client.ask.call_args.kwargs["model"] == "jev-preview"


async def test_ask_refuses_a_box_with_no_picture_before_a_request(
    hass, loaded_entry, mock_client
) -> None:
    mock_client.ask.reset_mock()
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "ask",
            {
                "state": "The front door is open.",
                "questions": {"where": {"type": "box", "instructions": "the parcel"}},
            },
        )
    assert err.value.translation_key == "box_needs_image"
    assert err.value.translation_placeholders == {"key": "where"}
    mock_client.ask.assert_not_awaited()


async def test_the_other_actions_take_a_model_too(
    hass, loaded_entry, mock_client
) -> None:
    answering(mock_client, answer=NoulAnswer(noul=0.8))
    await call(hass, "noul", {"state": "x", "instructions": "y", "model": "jev-1.14.0"})
    assert mock_client.ask.call_args.kwargs["model"] == "jev-1.14.0"


def test_a_question_sensor_cannot_be_a_box() -> None:
    """A sensor has no picture to ask about, so YAML refuses the type."""
    with pytest.raises(vol.Invalid):
        QUESTION_SCHEMA({"name": "Where", "type": "box", "instructions": "the parcel"})
