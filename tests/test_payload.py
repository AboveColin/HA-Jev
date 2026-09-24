"""The estimate is only worth as much as the body it measures.

payload_bytes builds the same dict jevclient posts. Nothing stops the library
adding a field to that body in a later release, and if it did, every estimate
would be quietly low with no test to say so. These tests drive a real JevClient
against a fake session and compare the bytes it actually sent.
"""

import json
from datetime import date
from typing import Any, ClassVar

import pytest
from aiohttp.payload import JsonPayload
from jevclient import Choice, JevClient, Noul, Score

from custom_components.jev.const import BUDGET_ESTIMATE_MARGIN
from custom_components.jev.coordinator import UsageAccount
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


# Measured live on 2026-09-24: body bytes, then the input tokens the API billed.
_BILLED = [
    (138, 278),  # jev.noul, one short state line
    (613, 377),  # jev.ask, eight questions
    (3142, 1371),  # the five-entity conversation payload, the dearest of sixteen
]


@pytest.mark.parametrize(("request_bytes", "billed"), _BILLED)
def test_the_cold_start_estimate_is_above_what_was_billed(request_bytes, billed):
    """Before any call has measured the ratio, the estimate must not be low.

    Without the fixed part, the 138 byte action was estimated at 70 tokens.
    """
    usage = UsageAccount(day=date(2026, 9, 24))
    assert billed <= usage.estimate_tokens(request_bytes) <= billed * 1.5


def test_one_measured_call_sets_the_estimate_for_the_next():
    """6,136 bytes of dense state was billed 3,277 tokens. The same again fits."""
    usage = UsageAccount(day=date(2026, 9, 24))
    usage.record(3277, 6136)
    assert 3277 <= usage.estimate_tokens(6136) <= 3277 * BUDGET_ESTIMATE_MARGIN + 1
