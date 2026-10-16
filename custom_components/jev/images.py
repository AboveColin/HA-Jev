"""Images for a request: fetched from a camera or an image entity, sized for the API.

TypeSafe rejects an image with a side under 280 or over 1568 pixels and never
resizes it. A 1920 x 1080 camera frame came back 400, "Invalid image dimensions"
(measured 2026-10-16), and that is the size most cameras give. So every image is
scaled here, and the size it is sent at is also what it is billed at.
"""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from math import ceil
from typing import Any

from homeassistant.components import camera, image
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, split_entity_id
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from jevclient import MAX_IMAGE_SIDE, MIN_IMAGE_SIDE, Image
from PIL import Image as PILImage
from PIL import ImageOps, UnidentifiedImageError

from .const import (
    DOMAIN,
    IMAGE_JPEG_QUALITY,
    IMAGE_PATCH_PIXELS,
    IMAGE_TOKENS_PER_PATCH,
    SNAPSHOT_TIMEOUT_SECONDS,
)

IMAGE_DOMAINS = (Platform.CAMERA, Platform.IMAGE)


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """One image as it will be sent, and the size it had before."""

    image: Image
    width: int
    height: int
    source_width: int
    source_height: int

    @property
    def tokens(self) -> int:
        return image_tokens(self.width, self.height)


@dataclass(slots=True)
class RequestImages:
    """The images of one request: the assets to send and the state entries naming them."""

    assets: dict[str, Image]
    entries: list[dict[str, Any]]
    tokens: int

    def __bool__(self) -> bool:
        return bool(self.assets)

    def add(self, label: dict[str, Any], prepared: PreparedImage) -> None:
        # A new UUID per image: the API matches references as plain text in every
        # string, so anything shorter could match inside a sentence of the state.
        ref = str(uuid.uuid4())
        self.assets[ref] = prepared.image
        self.entries.append({**label, "image": ref})
        self.tokens += prepared.tokens


def no_images() -> RequestImages:
    return RequestImages(assets={}, entries=[], tokens=0)


def image_tokens(width: int, height: int) -> int:
    """What an image of this size is billed, in input tokens.

    Measured on 2026-10-16 against jev-1.14.0 and jev-1.13.0 at six sizes from
    280 x 280 to 1568 x 1568 (site-docs/measurements.md). Each was exactly this.
    """
    return (
        IMAGE_TOKENS_PER_PATCH
        * ceil(width / IMAGE_PATCH_PIXELS)
        * ceil(height / IMAGE_PATCH_PIXELS)
    )


def with_images(state: Any, images: RequestImages) -> Any:
    """Put the images beside the state, with the same labels entities get.

    Text goes under `note`, the key async_build_state gives text that comes with
    entities, so a question reads one shape whatever the call was given.
    """
    if not images:
        return state
    if isinstance(state, dict):
        return {**state, "images": images.entries}
    payload: dict[str, Any] = {"images": images.entries}
    if state not in (None, ""):
        payload["note"] = state
    return payload


def prepare_image(data: bytes, source: str) -> PreparedImage:
    """Rotate, scale and re-encode one image. Blocking: run it in the executor.

    The API reads pixels as stored and ignores EXIF orientation, so a phone photo
    that looks upright can reach the model on its side. It takes PNG and JPEG
    only, and a progressive JPEG is the kind its docs warn about, so everything
    goes out as a baseline JPEG.
    """
    try:
        with PILImage.open(io.BytesIO(data)) as opened:
            picture = ImageOps.exif_transpose(opened)
            picture.load()
    except (UnidentifiedImageError, OSError, ValueError) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="image_unreadable",
            translation_placeholders={"source": source, "reason": str(err)},
        ) from err

    source_width, source_height = picture.size
    picture = _flatten(picture)
    picture = _fit(picture)
    buffer = io.BytesIO()
    picture.save(buffer, "JPEG", quality=IMAGE_JPEG_QUALITY)
    return PreparedImage(
        image=Image(buffer.getvalue(), "jpg"),
        width=picture.width,
        height=picture.height,
        source_width=source_width,
        source_height=source_height,
    )


def _flatten(picture: PILImage.Image) -> PILImage.Image:
    """RGB on white. A transparent PNG converted straight to RGB turns black."""
    if picture.mode in ("RGBA", "LA") or "transparency" in picture.info:
        rgba = picture.convert("RGBA")
        background = PILImage.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return picture.convert("RGB")


def _fit(picture: PILImage.Image) -> PILImage.Image:
    """Scale into the API's 280 to 1568 pixel range, keeping the aspect ratio.

    Both sides fit after one scale unless the image is more than 5.6 times as wide
    as it is tall, or the other way round. Then the long side is fitted and the
    short side stretched to 280. A box comes back in fractions of the picture as
    sent, and stretching keeps those the same fractions of the source, where
    padding would shift them.
    """
    width, height = picture.size
    scale = min(1.0, MAX_IMAGE_SIDE / max(width, height))
    if min(width, height) * scale < MIN_IMAGE_SIDE:
        scale = min(
            MIN_IMAGE_SIDE / min(width, height), MAX_IMAGE_SIDE / max(width, height)
        )
    size = (
        max(MIN_IMAGE_SIDE, round(width * scale)),
        max(MIN_IMAGE_SIDE, round(height * scale)),
    )
    if size != picture.size:
        picture = picture.resize(size, PILImage.Resampling.LANCZOS)
    return picture


async def async_entity_image(hass: HomeAssistant, entity_id: str) -> PreparedImage:
    """The current picture of a camera or an image entity, ready to send."""
    domain = split_entity_id(entity_id)[0]
    if domain not in IMAGE_DOMAINS:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_an_image_entity",
            translation_placeholders={"entity_id": entity_id},
        )
    try:
        if domain == Platform.CAMERA:
            snapshot: Any = await camera.async_get_image(
                hass, entity_id, timeout=SNAPSHOT_TIMEOUT_SECONDS
            )
        else:
            snapshot = await image.async_get_image(
                hass, entity_id, timeout=SNAPSHOT_TIMEOUT_SECONDS
            )
    except HomeAssistantError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="image_unavailable",
            translation_placeholders={"entity_id": entity_id, "reason": str(err)},
        ) from err
    return await hass.async_add_executor_job(prepare_image, snapshot.content, entity_id)


async def async_entity_images(
    hass: HomeAssistant, entity_ids: list[str]
) -> RequestImages:
    """Every named entity's picture, labelled the way the state labels entities."""
    images = no_images()
    for entity_id in entity_ids:
        prepared = await async_entity_image(hass, entity_id)
        images.add(entity_label(hass, entity_id), prepared)
    return images


def entity_label(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """What names a picture in the state, so a question can say "the front door"."""
    state = hass.states.get(entity_id)
    return {
        "name": state.name if state is not None else entity_id,
        "entity_id": entity_id,
    }
