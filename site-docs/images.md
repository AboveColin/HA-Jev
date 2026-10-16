# Cameras and images

Jev can look at a picture. Give an action the cameras or image entities to look at,
or attach a snapshot to an AI Task, and the current picture of each goes into the
request beside the state.

```yaml
- action: jev.noul
  response_variable: parcel
  data:
    images:
      - camera.front_door
    instructions: Is there a parcel on the doorstep?
- if: "{{ parcel.noul > 0.8 }}"
  then:
    - action: notify.mobile_app
      data: { message: A parcel is waiting at the front door. }
```

TypeSafe marks images "dreamer-ready": in its words, not yet up to its standards to
call ready for production. Check a question on your own camera before an automation
acts on the answer alone.

## Where pictures come from

| Path | How | Takes |
|---|---|---|
| `jev.noul`, `jev.choice`, `jev.score`, `jev.ask` | the `images:` field | camera and image entities |
| AI Task | `attachments:` on `ai_task.generate_data` | camera and image entities, and image files from media sources |

`images:` works alone or together with a target and a `state` template. The state
Jev reads then has an `images` list, each entry with the entity's name, its entity id
and the picture:

```json
{
  "now": "2026-10-16 14:05 Friday",
  "entities": [{"entity_id": "binary_sensor.doorbell", "state": "on"}],
  "images": [{"name": "Front door", "entity_id": "camera.front_door", "image": "<picture>"}]
}
```

So a question can name a camera the way you would: "Is there a parcel in the front
door camera?"

Targeting a camera sends its text state only, never its picture. Pictures go only
where you list them, because each one costs [hundreds to thousands of
tokens](#what-a-picture-costs).

Question sensors, the conversation agent, the question preview and the house check
send text only.

## Find where something is

`jev.box` asks where something is in one camera or image entity, and answers with a
box: its left, top, right and bottom edges as fractions of the picture, measured from
the top left corner. Box needs Jev 1.14, and the integration asks `jev-latest` unless
you set another model, so name the model on the call:

```yaml
- action: jev.box
  response_variable: parcel
  data:
    image: camera.front_door
    instructions: the parcel on the doorstep
    model: jev-1.14.0
```

| Key | What it holds |
|---|---|
| `left`, `top`, `right`, `bottom` | Edges, 0 to 1, as fractions of the picture's width and height |
| `confidence` | 0 to 1 |
| `width`, `height` | The picture as the camera sent it, in pixels, upright |
| `model`, `usage`, `latency_ms` | As on every action |

An edge in pixels is the fraction times the side:
`{{ (parcel.left * parcel.width) | round }}`. The edges are fractions of the camera's
own picture, not of the smaller one that was sent, so this holds for any camera.

A box always comes back, also when the thing is not in the picture. In one test on a
blank frame, the box covered the whole picture at confidence 0.12. With the thing in
the frame, the same question answered within 0.02 of each true edge at confidence
0.92. That is one sample of each, so ask a yes/no question beside it when the thing
may not be there:

```yaml
- action: jev.ask
  response_variable: door
  data:
    images: [camera.front_door]
    model: jev-1.14.0
    questions:
      parcel: { type: noul, instructions: Is there a parcel on the doorstep? }
      where: { type: box, instructions: the parcel on the doorstep }
- if: "{{ door.answers.parcel.noul > 0.8 }}"
  then:
    - action: notify.mobile_app
      data:
        message: >-
          Parcel at {{ (door.answers.where.left * 100) | round }}% from the left.
```

In `jev.ask`, a box question needs at least one picture under `images:`, and the
question has to say which picture when there is more than one. Question sensors take
noul, choice and score only.

## Sizing

The API takes PNG and JPEG with each side from 280 to 1568 pixels, and refuses
anything else rather than resizing it. A 1920 x 1080 camera frame is refused as it
comes. So every picture is prepared before it is sent:

- rotated upright from its EXIF orientation, which the API ignores;
- scaled to fit 1568 pixels on its long side, or scaled up to 280 on its short side;
- stretched on its short side when it is more than 5.6 times as long as it is wide,
  because both limits cannot be met by scaling alone. Stretching, unlike padding,
  keeps a [box](#find-where-something-is) in the same fractions of your picture;
- transparency filled with white, and sent as a JPEG.

## What a picture costs

A picture is billed by its size after scaling: 2 input tokens for every 28 x 28 pixel
square, a part square counted whole. Measured on 2026-10-16:

| Picture as sent | Input tokens |
|---|---|
| 280 x 280 | 200 |
| 784 x 588 | 1,176 |
| 1568 x 882, a 16:9 camera frame | 3,584 |
| 1568 x 1176, a 4:3 camera frame | 4,704 |
| 1568 x 1568 | 6,272 |

At the published $0.042 per million input tokens, a 16:9 frame costs $0.00015. A
request with no picture was billed 287 tokens, so one frame is about 12 times that. The [daily budget](cost.md#the-budget-is-a-tripwire) counts
pictures by these numbers, so a call that would not fit is refused before it is
sent.

## When it fails

| Message | Cause |
|---|---|
| "has no picture to send" | An entity in `images:` is not a camera or an image entity |
| "Could not get a picture from" | The camera did not answer within 10 seconds, or is unavailable |
| "is not an image Jev can read" | The camera sent something that is not a picture, often an error page |
| "Jev reads text and images" | An AI Task attachment is a video or another file type |
