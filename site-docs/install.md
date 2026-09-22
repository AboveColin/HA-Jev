# Install

Needs **Home Assistant 2026.9 or newer** and an API key from
[typesafe.ai](https://typesafe.ai). An endpoint of your own that needs no key can be
set up without one.

## Through HACS

Not in the HACS default list yet, so add it as a custom repository once.
[hacs/default#11052](https://github.com/hacs/default/pull/11052) is queued; when it
merges, steps 1 and 2 go away.

1. Open HACS, then the three dot menu, then **Custom repositories**
2. Paste `https://github.com/AboveColin/HA-Jev`, set Type to **Integration**, **Add**
3. Search HACS for **Jev**, then **Download**
4. Restart Home Assistant

[![Open your Home Assistant instance and open a repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=AboveColin&repository=HA-Jev&category=integration)

## By hand

Copy `custom_components/jev` from the
[latest release](https://github.com/AboveColin/HA-Jev/releases/latest) into your
`config/custom_components/` directory and restart.

!!! warning "HACS will not update a copy installed this way"
    A manual install is invisible to HACS, so you will not be told when a release
    lands. Prefer the custom repository route unless you have a reason not to.

## Setting it up

**Settings**, **Devices and services**, **Add integration**, then **Jev (TypeSafe)**.

It asks for an API key. **Advanced** is collapsed and holds the address to send it
to and the model to ask for; leave it closed to use TypeSafe. All three are checked
against a live request before the entry is created, so a wrong one fails here rather
than silently later.

The key may be left empty, for an endpoint of your own that asks for none. No
`Authorization` header is then sent at all. Empty against `https://api.typesafe.ai`
is refused without spending a request, because that API answers 401 to every
request without a key.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=jev)

An entry is identified by its address and its key together, hashed. Two entries
cannot hold the same key at the same address. The same key at two addresses is
allowed, and is two entries with two daily budgets, so that key can spend twice
what one budget allows. Two keyless endpoints are two entries as long as the
addresses differ.

Use **Reconfigure** to replace the key, the address or the model later. That keeps
your entities and their history.

## A different API address

The address defaults to `https://api.typesafe.ai` and most people should leave it
there. Change it when you run something of your own that speaks the same API: a
proxy that holds the key once for several clients, caches answers, or meters what is
spent across more than Home Assistant.

Whatever is behind it has to answer `POST /v1/systemone` the way TypeSafe does. A
path is kept as a prefix, so `http://gateway.local:8093/jev` is asked at
`http://gateway.local:8093/jev/v1/systemone`. A query, a fragment, a space, or a username
or password in the address are all refused: the first two cannot survive having that
path appended, a space is a typo rather than a host, and the last would be written
into a diagnostics file, which redacts secrets by name and would not recognise
those. Clearing the field goes back to
TypeSafe.

!!! warning "Give the address without the request path"
    `/v1/systemone` is added for you. An address that already carries it, or one
    copied out of an API reference, ends up asking for `/v1/systemone/v1/systemone`
    and the setup fails with "The server answered HTTP 404". That is a host that
    answered, not a host that could not be reached, and the form says so separately.

### Through OpenRouter

[OpenRouter](https://openrouter.ai) resells the model, so it works as an address here
with an OpenRouter key.

| Field | Value |
|---|---|
| API address | `https://openrouter.ai/api` |
| API key | your OpenRouter key |
| Model | `~typesafe/jev-latest` |

The leading `~` is part of the model id. OpenRouter uses it for an id that always
points at the newest model in a family, the same way `jev-latest` does at TypeSafe.

Not `https://openrouter.ai/api/v1`, and not a path out of the API reference. The
integration appends `/v1/systemone` to whatever you give it, so the part before that
is all it wants.

!!! note "The address is verified, the model id is reported"
    `POST https://openrouter.ai/api/v1/systemone` answers 401 without a key and
    `.../api/v1/systemone/v1/systemone` answers 404, so the address above is the one
    that reaches the route. The model id comes from a user who got it working
    ([#15](https://github.com/AboveColin/HA-Jev/issues/15)) and is not checked here
    against a paid key. OpenRouter's public model list covers its chat models and
    does not carry the decision models, so there is nothing to look it up in. If it
    is refused, the setup flow says which of the key, the address and the model was
    the problem.

Your OpenRouter spend is not visible from here. The cost sensor multiplies the tokens
the endpoint reports by the price you set in the options, so put OpenRouter's price
there rather than TypeSafe's, or read the cost as tokens only.

## A different model

The model defaults to `jev-latest`, which is what TypeSafe serves. Change it when
your endpoint publishes its own names. The id is sent in the request body, so what
counts is what that endpoint accepts. Only a space is refused locally. An id the
endpoint rejects comes back as "That model id was not accepted", with the form still
open. Clearing the field goes back to `jev-latest`.

!!! warning "An http address sends the key in clear"
    The key travels as a bearer header. Over `http` anything that can see that
    traffic can read it. Home Assistant writes one warning per setup saying so,
    unless the address is loopback. Prefer `https`, or keep the endpoint on a network
    you trust.

## Options

| Option | Default | What it does |
|---|---|---|
| API address | `https://api.typesafe.ai` | Where requests go. In the config flow, not the options |
| Model | `jev-latest` | Which model the endpoint is asked for. In the config flow, not the options |
| Daily input token budget | 0 | Stops evaluating for the day once spent. 0 means no limit |
| Price per million input tokens | 0.042 | Only affects the estimated cost sensor |
| Fall back to this agent | none | Where the conversation agent sends what it cannot route |
| Act only above this confidence | 0.6 | Below it, a spoken command goes to the fallback |
| Allow whole-house commands | off | Commands naming no room and no device. Turning everything off is always allowed |

The budget is a [tripwire](cost.md#the-budget-is-a-tripwire), not a quota to run
against. Put it past anything a working setup would use.

## Languages

The setup flow, the options, the question editor, the actions, the entity names, the
repair issues and everything the conversation agent says back are translated into
thirteen languages: English, Nederlands, Deutsch, Français, Italiano, Español,
Português (Brasil), Polski, Svenska, Dansk, Čeština, Русский and 简体中文.

Home Assistant falls back to English key by key, so a missing string never shows a
blank. The user interface follows your profile language. The conversation agent
follows the language of the Assist pipeline that called it, which is not always the
same one, and it accepts every language Assist offers.

Two of the agent's own lines reach further than those thirteen. "Sorry, I did not
understand that" and "Sorry, that did not work" are worded by Home Assistant in all
63 languages `home-assistant-intents` 2026.8.28 carries, so a Japanese pipeline
hears a Japanese sentence. Where this integration has its own translation, that one
is used.

The answer to a state question is not on the list either, and does not need to be.
The sentence comes from the template Home Assistant's own Assist ships for that
language, and the state word inside it comes from the state names Home Assistant
already shows on a badge, so Italian answers "Luce Tavolo è spento" rather than
"è off". The word follows the device class, which is what makes a door
"aperto" and a motion sensor "rilevato" instead of both being "acceso". A number
keeps its value and its unit, and German says "21,5 Grad" rather than "21.5
°C".

The measurements, against `home-assistant-intents` 2026.8.28 and Home Assistant
2026.8: 63 languages ship, 47 carry the state template. Three of those 47, Polish,
Russian and Thai, write the state word themselves by comparing against the English
one, so those three are handed the raw state and keep working. Of the remaining 44,
33 have Home Assistant's own word for the state. The other 11 keep the English word
inside a translated sentence, and the 16 languages with no template at all get an
English sentence.

Your own questions are not translated, because you wrote them. A choice comes back
as one of the options you named, in whatever language you named them.

A correction or a language that is not here is welcome as a pull request. One file
under `custom_components/jev/translations/`, same keys as `strings.json`.
