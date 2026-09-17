"""Drive two real Home Assistant automations that call Jev, and check what they wrote.

This is the path a REST call never takes: the script engine renders the templates in
the action data, and the response variable has to survive into the next step.
"""

import sys
import time

sys.path.insert(0, "/tmp")
import hactl

TOKEN = hactl.login()
WATTS = "input_number.washing_machine_power"
DOOR = "input_boolean.laundry_door_closed"
TEMPLATE_RESULT = "input_text.jev_template_result"
TARGET_RESULT = "input_text.jev_target_result"


def state_of(entity_id):
    st, out = hactl.req(f"/api/states/{entity_id}", token=TOKEN)
    return out["state"] if st == 200 else f"HTTP {st}"


def set_number(value):
    hactl.req(
        "/api/services/input_number/set_value",
        {"entity_id": WATTS, "value": value},
        token=TOKEN,
    )


def wait_for_change(entity_id, previous, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = state_of(entity_id)
        if current != previous:
            return current
        time.sleep(1)
    return None


def ensure_reading_differs(value):
    """Move the helper away from the target value first, and let that run finish.

    The automation triggers on a state change, so writing the same value does
    nothing. Nudging without waiting is worse: the next read picks up the nudge's
    own answer and reports it as the answer for the value under test.
    """
    if state_of(WATTS) != f"{float(value)}":
        return
    marker = state_of(TEMPLATE_RESULT)
    set_number(700.0)
    if wait_for_change(TEMPLATE_RESULT, marker) is None:
        print("  warning: the nudge produced no result, the numbers below are suspect")


failures = []
results = {}

print("=== automation 1: templates rendered by the script engine ===")
for watts, expectation in (
    (1.2, "idle, so the laundry is sitting there"),
    (1450.0, "running, so it is not sitting there"),
):
    ensure_reading_differs(watts)
    before = state_of(TEMPLATE_RESULT)
    set_number(watts)
    after = wait_for_change(TEMPLATE_RESULT, before)
    if after is None:
        failures.append(f"no result for {watts} W within 60 s")
        print(f"  {watts:>7} W -> nothing written")
        continue
    noul, is_true, tokens = after.split("|")
    print(
        f"  {watts:>7} W -> noul={noul} is_true={is_true} "
        f"tokens={tokens}   ({expectation})"
    )
    if not tokens.isdigit() or tokens == "0":
        failures.append(f"usage did not survive into the automation for {watts} W")
    results[watts] = float(noul)

if len(results) == 2:
    idle, running = results[1.2], results[1450.0]
    print(f"  separation: idle {idle} against running {running} = {idle - running:+.2f}")
    # Separation, not an absolute level. Nobody has published calibration evidence
    # for this model, so asserting that an idle machine reads above some particular
    # number would be asserting a figure I cannot defend. What an automation needs
    # is that the answer moves with the world, and by enough to put a threshold
    # between the two cases.
    if idle - running < 0.3:
        failures.append(
            f"the answer barely moved with the reading: idle {idle}, running "
            f"{running}, separation {idle - running:+.2f}"
        )

print("\n=== automation 2: entities as the input, no text ===")
before = state_of(TARGET_RESULT)
hactl.req("/api/services/input_boolean/toggle", {"entity_id": DOOR}, token=TOKEN)
after = wait_for_change(TARGET_RESULT, before)
if after is None:
    failures.append("the target path wrote no result")
    print("  nothing written within 60 s")
else:
    choice, confidence = after.split("|")
    print(f"  choice={choice} confidence={confidence}")
    if choice not in ("laundry", "kitchen", "bathroom", "other"):
        failures.append(f"choice returned something outside the option list: {choice}")

print("\n=== automation 3: trigger data and automation variables ===")
before = state_of("input_text.jev_vars_result")
hactl.req(
    "/api/services/input_text/set_value",
    {"entity_id": "input_text.jev_probe", "value": "a ZEBRA walked past"},
    token=TOKEN,
)
after = wait_for_change("input_text.jev_vars_result", before)
if after is None:
    failures.append("the variables path wrote no result")
    print("  nothing written within 60 s")
else:
    room, brand, zebra, watts = after.split("|")
    print(f"  a variable            -> room={room}")
    print(f"  a nested mapping      -> brand={brand}")
    print(f"  the trigger value     -> saw ZEBRA at {zebra}")
    print(f"  a number inside a map -> idle_watts is 5 at {watts}")
    # Each of these is only answerable if that part of the data actually arrived,
    # and the mapping ones only if it arrived as a mapping rather than as a string.
    if room != "laundry":
        failures.append(f"a plain variable did not reach the model: room={room}")
    if brand != "Miele":
        failures.append(f"a nested mapping did not reach the model: brand={brand}")
    if float(zebra) < 0.8:
        failures.append(f"trigger data did not reach the model: {zebra}")
    if float(watts) < 0.8:
        failures.append(f"a number inside a mapping did not survive: {watts}")

print("\n=== the context sensors from the same config ===")
for entity_id in (
    "sensor.jev_laundry_forgotten",
    "binary_sensor.jev_laundry_forgotten",
    "sensor.jev_calls_today",
    "sensor.jev_estimated_cost_today",
):
    value = state_of(entity_id)
    print(f"  {entity_id:44} = {value}")
    if value in ("unavailable", "unknown"):
        failures.append(f"{entity_id} is {value}")

print()
if failures:
    print(f"FAILED: {len(failures)}")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("all automation checks passed")
