# Limitations

Written plainly, because deciding whether this fits is easier than undoing it later.

## Answers carry no reasoning

You get a number. You do not get why. There is nothing to audit after the fact
beyond the state that went in, which the integration records for you.

If you need to explain a decision to someone later, this is the wrong tool.

## Confidence has no published calibration

TypeSafe publishes no calibration evidence for confidence, and their own docs call it
a convenient default.

Treat 0.9 as higher than 0.6. Do not treat it as right nine times in ten until you
have measured it on your own questions.

## It judges, it does not calculate

Hand it a reading and a threshold and it will not reliably work out which side the
reading falls on. Measured, that gave 0.06 separation against 0.69 when the
comparison was done first.

Do arithmetic in Jinja, where it is exact and free, and hand over the conclusion in
words.

## Slower than the published figure, from Europe

TypeSafe publishes 70 to 500 ms. Measured across 16 calls from the Netherlands, a
warm connection answers in 250 to 580 ms and the first call after an idle spell takes
700 to 900 ms. Their figures were measured near their own service.

Fine for a doorbell. Too slow for a tight loop.

## Not for safety decisions

A probability with no explanation should not hold a lock, a heater or a smoke alarm.

Use it to decide whether to *tell* you something. Do not use it to decide whether to
*do* something that is hard to undo.

## Structured criteria bought nothing measurable

The API accepts objects and arrays for `instructions` and criteria values, and the
docs say structure sharpens the boundary when two options blur.

On five deliberately ambiguous doorbell callers, three runs each: 12 of 15 agreed
either way, mean confidence 0.90 flat against 0.87 structured, neither unstable. An
easier set gave 12 of 12 for both.

It is supported. Reach for it only when two options genuinely blur and a plain
sentence has already failed.

## The conversation agent handles five intents

On, off, toggle, brightness and state questions. Locks, climate setpoints, anything
needing words written and anything phrased as two commands go to the fallback agent.
Locks are refused on purpose: Home Assistant reads turn_on on a lock as `lock.lock`,
which is the opposite way round from the spoken command. Garage, gate and door covers
are refused for the same reason.

## It is not a core integration

`quality_scale.yaml` tracks this against Home Assistant's quality scale at 47 done
and 7 exempt, including all three Platinum rules. That file is a self-assessment:
hassfest does not validate it for a custom integration, which I checked by deleting a
rule and watching it pass.
