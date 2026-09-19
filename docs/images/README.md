# Images

`entities.png`, `assist.png`, `auto-simple.png`, `auto-advanced.png`,
`auto-trace.png`, the `flow-*.png` set and the `example-*.png` set are real
screenshots of a throwaway Home Assistant,
not mockups. Both were taken against the live TypeSafe API, so the numbers in them
are answers the model actually gave.

The automation in `auto-advanced.png` is saved verbatim as
`examples/15_doorbell_triage_ui.yaml`, and `auto-trace.png` is a real run of it.

The `example-*.png` pair uses invented sensor readings chosen to make the answers
interesting: a washing machine idle 14 minutes after finishing, and a modem with one
reading near its limit.

The house in them is fake on purpose: a small integration creates four lights and a
switch that hold their state in memory, so a screenshot session cannot touch
hardware and nothing personal appears in a public repository.

`social-preview.png` is 1280x640 at 2x. GitHub has no API for the social preview, so
it is set by hand under Settings, General, Social preview.
