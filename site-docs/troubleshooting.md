# Troubleshooting

Turn on debug logging first. It prints every state sent, which is usually the answer:

```yaml
logger:
  logs:
    custom_components.jev: debug
```

## Symptoms

| Symptom | Cause |
|---|---|
| An answer barely moves with the world | The state does not say what you assumed, or it holds a number the model is being asked to compare |
| Answers sit near 0.5 with low confidence | The question measures more than one thing. Split it |
| Entities unavailable, budget sensor on | The daily budget stopped evaluation |
| Entities unavailable, budget sensor off | Look for one line saying TypeSafe is not answering |
| Setup fails with "TypeSafe did not answer" | Connectivity, not configuration. Home Assistant retries |
| Setup fails with "The server answered HTTP 404" | The address carries the request path. `/v1/systemone` is added for you |
| An AI Task is refused before it is sent | Read the message. It names the field and what its selector would have to be |
| An error names a limit | It names your number too. 2 to 255 options, 2 to 10 levels, 250 entities |
| Voice commands all go to the fallback | Check the traces in diagnostics. Each one records the reason |
| Voice acts on the wrong device | The names and areas in your entity registry are what the model reads |
| A question you expected to batch went alone | Its target, template, schedule or triggers differ from the others. The preview says which |
| An action says the target holds no entities | Every entity it names has no state, often because it was renamed or removed |
| Reauthentication says a key is required | The hosted API needs a key. A key of only spaces counts as none |

## The answer does not track the world

This is the common one, and it is almost always the state rather than the model.

1. Open the question and read the [preview](questions-ui.md#the-preview). It shows
   the exact state and what it answers right now.
2. If the state is missing something, add it. The model cannot know what your sensors
   do not say.
3. If the state holds a raw number and a rule, move the rule into **Standing facts**
   or do the comparison in the template. Measured, that is worth +0.60 against +0.21.

See [writing a question that works](writing-questions.md).

## Diagnostics

**Settings**, **Devices and services**, **Jev**, three dot menu, **Download
diagnostics**.

It contains the last evaluated state for every question, the usage account, and the
last 20 conversation decisions. The API key, the spoken sentences, access tokens,
entity pictures and coordinates are redacted, and tests assert it. Entity names and
states stay in, so read the file before pasting it into a public issue.

## Reporting something

[Open an issue](https://github.com/AboveColin/HA-Jev/issues) with the diagnostics
file and, if the problem is an answer rather than a crash, the state from the
preview. An answer without the state it read is not something anyone can debug.
