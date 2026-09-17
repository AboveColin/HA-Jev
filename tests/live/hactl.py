"""Drive the throwaway Home Assistant at :8124 over its REST API.

Usage: python3 /tmp/hactl.py states | options <token_budget> | log
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8124"
CID = "http://127.0.0.1:8124/"


def req(path, data=None, token=None, form=False):
    body, headers = None, {}
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def login():
    _, f = req("/auth/login_flow", {"client_id": CID, "handler": ["homeassistant", None],
                                    "redirect_uri": CID})
    _, o = req(f"/auth/login_flow/{f['flow_id']}",
               {"client_id": CID, "username": "dev", "password": "devdevdev1234"})
    _, t = req("/auth/token", {"grant_type": "authorization_code",
                               "code": o["result"], "client_id": CID}, form=True)
    return t["access_token"]


def show_states(token):
    _, states = req("/api/states", token=token)
    for s in sorted(states, key=lambda x: x["entity_id"]):
        if s["entity_id"].startswith(("sensor.jev", "binary_sensor.jev")):
            print(f"  {s['entity_id']:46} = {s['state']!r}")


def set_budget(token, budget):
    _, entries = req("/api/config/config_entries/entry", token=token)
    entry = [e for e in entries if e["domain"] == "jev"][0]
    _, flow = req("/api/config/config_entries/options/flow",
                  {"handler": entry["entry_id"]}, token=token)
    st, _ = req(f"/api/config/config_entries/options/flow/{flow['flow_id']}",
                {"daily_token_budget": budget, "price_per_million": 0.042}, token=token)
    print(f"budget set to {budget} via the options flow -> {st}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    tok = login()
    if cmd == "states":
        show_states(tok)
    elif cmd == "options":
        set_budget(tok, int(sys.argv[2]))
