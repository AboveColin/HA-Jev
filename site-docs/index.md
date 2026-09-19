# Jev for Home Assistant

Ask your house a typed question and get a number back.

[TypeSafe Jev](https://typesafe.ai) is a decision model rather than a chat model. You
hand it a question and a fixed set of answers, and it returns a probability over them.
Nothing is generated, so there is nothing to parse and nothing to invent.

This integration puts that in three places in Home Assistant.

<div class="grid cards" markdown>

-   **Questions become entities**

    A question you write becomes a sensor. Add a threshold and it becomes a binary
    sensor you can trigger on.

    [Your first question](first-question.md)

-   **Four actions for automations**

    `jev.noul`, `jev.choice`, `jev.score` and `jev.ask`, each returning the answer as
    a response variable.

    [Actions](actions.md)

-   **A conversation agent**

    Point Assist at Jev. One request per sentence, mapped onto Home Assistant's own
    intents, with a confidence the router can refuse to act on.

    [Conversation agent](conversation.md)

-   **It says what it spends**

    Calls, input tokens and estimated cost as entities, plus a daily budget that
    stops evaluation when it trips.

    [What it costs](cost.md)

</div>

## Is this for you?

It fits when the answer is a judgment about a small, fixed set of outcomes, and you
want a number you can branch on.

- Is the laundry finished but still sitting in the machine?
- Is the person at the door a delivery, a visitor or a cold caller?
- How urgently does this need somebody?

It does not fit when you need prose, arithmetic, or a decision that carries
consequences. Jev makes a judgment and does not calculate, and its answers carry no
reasoning you can audit. See [Limitations](limitations.md) before you wire it to
anything that matters.

## Not affiliated

This is an unofficial integration. The API client is
[jevclient](https://github.com/AboveColin/jevclient), published separately.
