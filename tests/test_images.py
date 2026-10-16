"""Images: a camera, an image entity or an AI Task attachment, sized and billed.

Nothing here decodes an image the API would see. The client is replaced, so these
tests check what would be sent and what it was budgeted at.
"""

import io
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components import ai_task, camera, conversation, image
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from jevclient import Image, JevResponse, NoulAnswer, Usage
from PIL import Image as PILImage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.ai_task import JevAITaskEntity
from custom_components.jev.const import (
    CONF_DAILY_TOKEN_BUDGET,
    DOMAIN,
    REQUEST_OVERHEAD_TOKENS,
)
from custom_components.jev.images import image_tokens, prepare_image

from .conftest import build_response
from .test_ai_task import BOOLEAN_STRUCTURE, ENTITY_ID


def picture(
    width: int, height: int, fmt: str = "JPEG", mode: str = "RGB", **save
) -> bytes:
    buffer = io.BytesIO()
    color = (255, 0, 0, 0) if mode == "RGBA" else "red"
    PILImage.new(mode, (width, height), color).save(buffer, fmt, **save)
    return buffer.getvalue()


def opened(prepared) -> PILImage.Image:
    return PILImage.open(io.BytesIO(prepared.image.data))


# Every receipt from 2026-10-16: the size sent, and what the image part was billed.
@pytest.mark.parametrize(
    ("width", "height", "billed"),
    [
        (280, 280, 200),
        (300, 301, 242),
        (784, 588, 1176),
        (1000, 750, 1944),
        (1568, 1176, 4704),
        (1568, 1568, 6272),
    ],
)
def test_the_token_count_is_what_the_api_billed(width, height, billed) -> None:
    assert image_tokens(width, height) == billed


def test_a_full_hd_frame_is_scaled_to_what_the_api_takes() -> None:
    # The API answered 400 "Invalid image dimensions" to this size as it was.
    prepared = prepare_image(picture(1920, 1080), "camera.front_door")
    assert (prepared.width, prepared.height) == (1568, 882)
    assert (prepared.source_width, prepared.source_height) == (1920, 1080)
    assert prepared.tokens == 2 * 56 * 32
    out = opened(prepared)
    assert out.format == "JPEG"
    assert out.size == (1568, 882)
    assert not out.info.get("progressive")


def test_a_small_image_is_scaled_up_to_the_minimum() -> None:
    prepared = prepare_image(picture(100, 50, "PNG"), "image.doorbell")
    assert (prepared.width, prepared.height) == (560, 280)


def test_a_panorama_is_fitted_on_its_long_side_and_stretched_on_the_short() -> None:
    """Stretched, not padded: a box is a fraction of the picture sent, and must be
    the same fraction of the source."""
    source = PILImage.new("RGB", (4000, 200), "white")
    source.paste((255, 0, 0), (0, 0, 4000, 100))  # the top half red
    buffer = io.BytesIO()
    source.save(buffer, "PNG")
    prepared = prepare_image(buffer.getvalue(), "camera.wide")
    assert (prepared.width, prepared.height) == (1568, 280)
    out = opened(prepared).convert("RGB")
    assert out.getpixel((784, 2))[1] < 60  # red reaches the top edge
    assert out.getpixel((784, 135))[1] < 60  # and still fills the top half
    assert out.getpixel((784, 145))[1] > 200  # white below the middle
    assert out.getpixel((784, 277))[1] > 200


def test_exif_orientation_is_applied_to_the_pixels() -> None:
    exif = PILImage.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    prepared = prepare_image(picture(1000, 500, exif=exif), "a phone photo")
    assert (prepared.width, prepared.height) == (500, 1000)


def test_transparency_becomes_white_not_black() -> None:
    prepared = prepare_image(picture(400, 400, "PNG", "RGBA"), "image.logo")
    assert opened(prepared).convert("RGB").getpixel((200, 200)) == pytest.approx(
        (255, 255, 255), abs=8
    )


def test_bytes_that_are_no_image_are_refused_by_name() -> None:
    with pytest.raises(HomeAssistantError) as err:
        prepare_image(b"<html>login required</html>", "camera.front_door")
    assert err.value.translation_key == "image_unreadable"
    assert err.value.translation_placeholders["source"] == "camera.front_door"


@pytest.fixture
def snapshot():
    """camera.async_get_image, answering a 1920 x 1080 frame."""
    frame = camera.Image("image/jpeg", picture(1920, 1080))
    with patch.object(camera, "async_get_image", AsyncMock(return_value=frame)) as get:
        yield get


async def call(hass, action, data):
    return await hass.services.async_call(
        DOMAIN, action, data, blocking=True, return_response=True
    )


async def test_a_camera_goes_into_the_request_beside_the_state(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    hass.states.async_set("camera.front_door", "idle", {"friendly_name": "Front door"})
    response = await call(
        hass,
        "noul",
        {
            "images": ["camera.front_door"],
            "state": "Nobody is home.",
            "instructions": "Is there a parcel at the door?",
        },
    )
    assert response["noul"] == 0.81
    snapshot.assert_awaited_once()
    state, questions = mock_client.ask.call_args.args
    assets = mock_client.ask.call_args.kwargs["assets"]
    (ref,) = assets
    assert isinstance(assets[ref], Image)
    assert assets[ref].format == "jpg"
    assert state == {
        "note": "Nobody is home.",
        "images": [
            {"name": "Front door", "entity_id": "camera.front_door", "image": ref}
        ],
    }
    assert list(questions) == ["answer"]


async def test_images_alone_are_enough_to_ask(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    await call(
        hass,
        "noul",
        {"images": ["camera.front_door"], "instructions": "Is the gate open?"},
    )
    state = mock_client.ask.call_args.args[0]
    assert list(state) == ["images"]


async def test_images_join_the_entities_of_a_target(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    hass.states.async_set("binary_sensor.doorbell", "on")
    await call(
        hass,
        "ask",
        {
            "entity_id": "binary_sensor.doorbell",
            "images": ["camera.front_door"],
            "questions": {
                "visitor": {"type": "noul", "instructions": "Is someone at the door?"}
            },
        },
    )
    state = mock_client.ask.call_args.args[0]
    assert {"now", "entities", "images"} <= set(state)


async def test_an_image_entity_is_read_too(hass, loaded_entry, mock_client) -> None:
    still = image.Image("image/png", picture(640, 480, "PNG"))
    with patch.object(image, "async_get_image", AsyncMock(return_value=still)) as get:
        await call(
            hass,
            "noul",
            {"images": ["image.doorbell_snapshot"], "instructions": "Is it a courier?"},
        )
    get.assert_awaited_once()
    assert len(mock_client.ask.call_args.kwargs["assets"]) == 1


async def test_an_entity_with_no_picture_is_refused_before_a_request(
    hass, loaded_entry, mock_client
) -> None:
    mock_client.ask.reset_mock()
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "noul",
            {"images": ["light.kitchen"], "instructions": "Is it on?"},
        )
    assert err.value.translation_key == "not_an_image_entity"
    mock_client.ask.assert_not_awaited()


async def test_a_camera_that_does_not_answer_is_named(
    hass, loaded_entry, mock_client
) -> None:
    mock_client.ask.reset_mock()
    with (
        patch.object(
            camera,
            "async_get_image",
            AsyncMock(side_effect=HomeAssistantError("Unable to get image")),
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await call(
            hass,
            "noul",
            {"images": ["camera.garden"], "instructions": "Is the gate open?"},
        )
    assert err.value.translation_key == "image_unavailable"
    assert err.value.translation_placeholders == {
        "entity_id": "camera.garden",
        "reason": "Unable to get image",
    }
    mock_client.ask.assert_not_awaited()


async def test_the_budget_counts_the_pixels(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    """A text call that fits is refused once the frame's 3,584 tokens are added."""
    hass.config_entries.async_update_entry(
        loaded_entry, options={CONF_DAILY_TOKEN_BUDGET: 2_000}
    )
    await hass.async_block_till_done()
    usage = loaded_entry.runtime_data.usage
    usage.roll_over(dt_util.now().date())
    before = usage.input_tokens
    mock_client.ask.reset_mock()
    with pytest.raises(HomeAssistantError) as err:
        await call(
            hass,
            "noul",
            {"images": ["camera.front_door"], "instructions": "Is the gate open?"},
        )
    assert err.value.translation_key == "action_over_budget"
    assert int(err.value.translation_placeholders["estimate"]) > 3_584
    mock_client.ask.assert_not_awaited()
    assert usage.input_tokens == before


async def test_image_tokens_do_not_teach_the_text_ratio(
    hass, loaded_entry, mock_client, snapshot
) -> None:
    """The ratio comes from the text part of the bill, not from the pixels."""
    usage = loaded_entry.runtime_data.usage
    text_tokens = 600
    mock_client.ask.side_effect = None
    mock_client.ask.return_value = JevResponse(
        model="jev-1.14.0",
        answers={"answer": NoulAnswer(noul=0.2)},
        usage=Usage(
            input_tokens=REQUEST_OVERHEAD_TOKENS + text_tokens + 3_584, output_tokens=20
        ),
    )
    await call(
        hass,
        "noul",
        {
            "images": ["camera.front_door"],
            "state": "x" * 2_000,
            "instructions": "Is the gate open?",
        },
    )
    # Left in, the frame's 3,584 tokens would have put the ratio near 0.5.
    assert usage.bytes_per_token > 3


@pytest.fixture
async def task_entry(hass, mock_client):
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_API_KEY: "k"}, options={}, unique_id="images"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_an_ai_task_sends_its_image_attachments(
    hass, task_entry, mock_client, snapshot
) -> None:
    mock_client.ask.side_effect = None
    mock_client.ask.return_value = build_response(window_open=NoulAnswer(noul=0.9))

    result = await hass.services.async_call(
        "ai_task",
        "generate_data",
        {
            "entity_id": ENTITY_ID,
            "task_name": "window check",
            "instructions": "The back of the house",
            "structure": BOOLEAN_STRUCTURE,
            "attachments": [
                {
                    "media_content_id": "media-source://camera/camera.back",
                    "media_content_type": "image/jpeg",
                }
            ],
        },
        blocking=True,
        return_response=True,
    )
    assert result["data"]["window_open"] is True
    state = mock_client.ask.call_args.args[0]
    (ref,) = mock_client.ask.call_args.kwargs["assets"]
    assert state == {
        "note": "The back of the house",
        "images": [{"name": "camera.back", "image": ref}],
    }


async def test_an_ai_task_refuses_an_attachment_that_is_no_image(
    hass, task_entry, mock_client, tmp_path
) -> None:
    """ai_task only resolves local files, so a clip arrives with its own mime type."""
    mock_client.ask.reset_mock()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    task = ai_task.GenDataTask(
        name="window check",
        instructions="The back of the house",
        structure=None,
        attachments=[
            conversation.Attachment(
                media_content_id="media-source://media_source/local/clip.mp4",
                mime_type="video/mp4",
                path=clip,
            )
        ],
    )
    entity = JevAITaskEntity(task_entry)
    entity.hass = hass
    with pytest.raises(ServiceValidationError) as err:
        await entity._async_images(task)
    assert err.value.translation_key == "attachment_not_image"
    assert err.value.translation_placeholders["mime_type"] == "video/mp4"
    mock_client.ask.assert_not_awaited()
