# Questions in the UI

**Settings**, **Devices and services**, **Jev**, **Add question**.

![Three kinds of answer](images/flow-menu.png)

## The form

![The form for a choice question](images/flow-form.png)

It reads in the order a question is actually thought out.

| Field | Required | Goes to the API as | Notes |
|---|---|---|---|
| Name | yes | | Names the sensor this creates |
| The question | yes | `instructions` | Ask one thing |
| Your options / levels | for Choice and Score | `criteria` | One per line |
| What to look at | one of these two | | Entities, devices, areas, floors or labels |
| Extra note or template | one of these two | | Text or Jinja, alone or beside the readings |
| Standing facts | no | folded into `instructions` | What a reading means |

Under **Advanced**:

| Field | Default | Notes |
|---|---|---|
| Wake on these instead | none | Evaluate when these entities change rather than on the clock, debounced by 5 seconds |
| Ask this often | 300 s | Minimum 30 |
| Send every attribute as well | off | A weather forecast runs to thousands of tokens per evaluation |

## Why there is no "context"

The YAML surface makes you declare a **context**: a named group of questions that
share one API call. The UI has no such thing, on purpose.

That grouping is not a preference, it is derivable. TypeSafe takes one state and N
questions, so two questions can share a call exactly when they describe the same
state, and cannot when they do not, whatever you write. Asking you for it would be
asking you to hand back an answer the integration already has.

So questions that point at the same thing, on the same schedule, with the same
triggers, are sent as one request. You do not configure that. The preview tells you
when it happens:

> Sent in one request together with: Cable line degrading, Modem attention.

Adding a question to an existing call costs tokens and almost no extra time. See
[what it costs](cost.md).

## The preview

![The preview, with the trial answer](images/flow-preview.png)

Before it saves, the form builds the state with the same code the coordinator uses,
then **asks the question once** so you can see what it answers.

It catches three things before the question exists:

- a template that does not render, with the Jinja error
- a target over the 250 entity cap, with the limit and your number
- a state too long to read, summarised with a count rather than dumped

A failed trial never blocks the save. If TypeSafe is unreachable you get a note and
the Submit button still works.

## Editing and removing

Each question is its own row on the integration page with its own settings button.
Editing opens the same form pre-filled, and the preview runs again on the way out.
Removing it removes its entities.
