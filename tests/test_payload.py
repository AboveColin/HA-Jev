"""The estimate is only worth as much as the body it measures.

payload_bytes builds the same dict jevclient posts. Nothing stops the library
adding a field to that body in a later release, and if it did, every estimate
would be quietly low with no test to say so. These tests drive a real JevClient
against a fake session and compare the bytes it actually sent.
"""

import json
from typing import Any, ClassVar

import pytest
from aiohttp.payload import JsonPayload
from jevclient import Choice, JevClient, Noul, Score

from custom_components.jev.payload import payload_bytes

REPLY = {
    "model": "jev-latest",
    "answers": {"open": {"type": "noul", "noul": 0.8}},
    "usage": {"input_tokens": 321, "output_tokens": 42},
}


class _Response:
    status = 200
    headers: ClassVar[dict[str, str]] = {}

    async def text(self) -> str:
        return json.dumps(REPLY)

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return REPLY


class _Post:
    def __init__(self, session: "_Session") -> None:
        self._session = session

    async def __aenter__(self) -> _Response:
        return _Response()

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Session:
    """Enough of aiohttp for JevClient, and it keeps what it was handed."""

    def __init__(self) -> None:
        self.sent: Any = None

    def post(self, url: str, *, json: Any, **kwargs: Any) -> _Post:
        self.sent = json
        return _Post(self)


QUESTIONS = {
    "open": Noul(instructions="Is the window open?"),
    "room": Choice(instructions="Which room?", criteria={"hall": None, "attic": None}),
    "urgency": Score(instructions="How urgent?", criteria=["not at all", "very"]),
}


@pytest.mark.parametrize(
    "state",
    [
        "the window is open",
        {"window": "open", "temperature": 21.5},
        ["one", "two"],
        # Escaped by json.dumps, so this is six bytes on the wire and two in the
        # file. An estimate that counted the file would be low for half of Europe.
        "de kachel staat aan in de keuken, 21,5 °C",
    ],
)
async def test_the_estimate_counts_the_bytes_the_client_really_sends(state):
    session = _Session()
    client = JevClient(
        "test-key-not-a-real-one",
        session=session,
        base_url="https://192.0.2.10/api",
        model="jev-latest",
    )

    await client.ask(state, QUESTIONS)

    assert JsonPayload(session.sent).size == payload_bytes(state, QUESTIONS, "jev-latest")


async def test_the_model_is_part_of_what_is_measured():
    """The model id travels in the body, so a longer one is a bigger request."""
    short = payload_bytes("x", QUESTIONS, "a")
    long = payload_bytes("x", QUESTIONS, "a" * 40)
    assert long - short == 39
