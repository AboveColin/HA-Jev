# Install

Needs **Home Assistant 2026.9 or newer** and an API key from
[typesafe.ai](https://typesafe.ai).

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

It asks for an API key, and for the address to send it to. Both are checked against
a live request before the entry is created, so a wrong one fails here rather than
silently later.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=jev)

An entry is identified by its API key, so the same key cannot be set up twice at two
addresses. A second endpoint needs its own key.

Use **Reconfigure** to replace the key or the address later. That keeps your entities
and their history.

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

!!! warning "An http address sends the key in clear"
    The key travels as a bearer header. Over `http` anything that can see that
    traffic can read it. Home Assistant writes one warning per setup saying so,
    unless the address is loopback. Prefer `https`, or keep the endpoint on a network
    you trust.

## Options

| Option | Default | What it does |
|---|---|---|
| API address | `https://api.typesafe.ai` | Where requests go. In the config flow, not the options |
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

Your own questions are not translated, because you wrote them. A choice comes back
as one of the options you named, in whatever language you named them.

A correction or a language that is not here is welcome as a pull request. One file
under `custom_components/jev/translations/`, same keys as `strings.json`.
