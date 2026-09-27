# Blueprints

A blueprint is an automation you import with one click and fill in with your own
entities and actions. Each one below asks Jev one question, or a few in one call,
and runs your actions on the answer. The import button opens your Home Assistant
and asks where to save it.

Every blueprint description says about how many calls a day it makes. The hold
time and the cooldown inputs keep a sensor that changes often from asking more
than once. [What it costs](cost.md) turns calls into money.

Each blueprint has a test that creates the automation in a real Home Assistant,
fires its trigger and checks which of your actions ran. None of them locks,
unlocks, arms or opens anything.

## Start from a question

Fill in your own question and actions. These fit any house.

| Blueprint | What Jev decides | By | |
|---|---|---|---|
| [Ask a yes or no question](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/yes_no_question.yaml) | a yes or no question about the entities you pick | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fyes_no_question.yaml) |
| [Pick one of several](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/pick_one_of_several.yaml) | which of up to four options fits, each with its own actions | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fpick_one_of_several.yaml) |
| [Rate something on a scale](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/score_ladder.yaml) | where the situation sits on four levels you name | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fscore_ladder.yaml) |
| [Act when sure, ask when unsure](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/ask_before_acting.yaml) | acts above one threshold, asks your phone between two | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fask_before_acting.yaml) |
| [Triage a message](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/message_triage.yaml) | whether a message needs a person, and how urgently | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fmessage_triage.yaml) |
| [Call an LLM only when it is worth it](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/llm_only_when_needed.yaml) | whether an AI Task call is worth making before it is made | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fllm_only_when_needed.yaml) |
| [Keep a situation helper up to date](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/situation_helper.yaml) | keeps an input_boolean on or off, with a gap between the two thresholds | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fsituation_helper.yaml) |

## Appliances

| Blueprint | What Jev decides | By | |
|---|---|---|---|
| [Laundry done and forgotten](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/laundry_forgotten.yaml) | whether the washing has finished and is still in the machine | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Flaundry_forgotten.yaml) |
| [Start the dishwasher at a good moment](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/dishwasher_good_moment.yaml) | whether now is a good moment, from the price, the solar output and who is home | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fdishwasher_good_moment.yaml) |
| [Appliance left on by mistake](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/appliance_left_on.yaml) | whether an oven, iron or heater was left on by mistake | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fappliance_left_on.yaml) |
| [Lights or media left on in an empty room](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/left_on_in_an_empty_room.yaml) | whether anybody still uses what is on after a room empties | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fleft_on_in_an_empty_room.yaml) |
| [Fridge or freezer getting warm](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/fridge_freezer_watch.yaml) | the likely cause of a warm fridge or freezer, and how urgent it is | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Ffridge_freezer_watch.yaml) |
| [Which low batteries matter this week](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/battery_triage.yaml) | whether a low or silent battery protects people, and how soon to swap it | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fbattery_triage.yaml) |

## Climate and weather

| Blueprint | What Jev decides | By | |
|---|---|---|---|
| [Window left open while heating](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/window_open_while_heating.yaml) | whether a window open while the heating runs is airing or waste | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fwindow_open_while_heating.yaml) |
| [Pick a ventilation speed](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/ventilation_speed.yaml) | the fan speed from CO2, humidity and occupancy, and sets it | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fventilation_speed.yaml) |
| [Rain coming and something is open](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/rain_and_open_windows.yaml) | whether rain will reach a window left open | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Frain_and_open_windows.yaml) |
| [Water the garden tonight](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/water_the_garden.yaml) | whether the garden needs water, from the soil and the forecast | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fwater_the_garden.yaml) |
| [Umbrella and coat for today](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/umbrella_and_coat.yaml) | whether to take an umbrella, and how warm to dress | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fumbrella_and_coat.yaml) |
| [Charge the car now or later](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/ev_charge_now.yaml) | whether to charge now, from the price, the solar output and the departure time, and switches the charger | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fev_charge_now.yaml) |

## People and the door

| Blueprint | What Jev decides | By | |
|---|---|---|---|
| [Something left on when everyone leaves](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/leaving_home_check.yaml) | whether something left on needs dealing with once the house is empty | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fleaving_home_check.yaml) |
| [Anything to deal with before bed](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/bedtime_check.yaml) | whether anything open or on needs dealing with before the night | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fbedtime_check.yaml) |
| [Who is at the door](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/doorbell_caller.yaml) | whether the caller is a delivery, a neighbour, a salesperson, a service visit, a resident or nobody | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fdoorbell_caller.yaml) |
| [Parcel on its way](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/parcel_today.yaml) | whether a message means a parcel arrives today, is still on its way, was delivered or needs action | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fparcel_today.yaml) |
| [Motion while nobody is home](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/motion_while_away.yaml) | whether motion in an empty house has an explanation, and how urgent it is | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fmotion_while_away.yaml) |
| [Does an appointment need preparing](https://github.com/AboveColin/HA-Jev/blob/main/blueprints/automation/jev/calendar_prep.yaml) | whether an appointment needs preparing, a set time before it starts, and what | [AboveColin](https://github.com/AboveColin) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FAboveColin%2FHA-Jev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjev%2Fcalendar_prep.yaml) |

## Share yours

If an automation at your home asks Jev something, other people have the same
problem. Put it in `blueprints/automation/jev/` with a test case and a row on this
page, and open a pull request. The
[contributing guide](https://github.com/AboveColin/HA-Jev/blob/main/CONTRIBUTING.md)
has the rules and a test case to copy. Your GitHub name goes in the blueprint's
`author:`.

Have an idea but no time to write it? Open a
[blueprint idea](https://github.com/AboveColin/HA-Jev/issues/new?template=blueprint_idea.yml).
