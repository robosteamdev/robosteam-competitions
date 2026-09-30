# MQTT Protocol Reference — RoboSTEAM Competitions

## Broker

| Setting | Value |
|---------|-------|
| Host | `<broker-address>` |
| Port | `51883` (plain TCP forwarded to Mosquitto 1883) |
| Protocol | MQTT v3.1.1 |
| Auth | username `<mqtt-user>` / password in `project.env` |
| QoS | 1 (at-least-once) — server deduplicates by sequence number |
| TLS | Deferred — add once Nginx TLS passthrough is configured |

---

## Topic Structure

```
robosteam/competition/{competition_id}/cmd     # server → all devices in competition
robosteam/laptimer/{mac_address}/event         # lap timer → server (beam crossing)
robosteam/laptimer/{mac_address}/status        # lap timer → server (heartbeat, PONG)
robosteam/laptimer/{mac_address}/cmd           # server → specific lap timer (PING)
robosteam/robot/{mac_address}/cmd              # server → specific robot (START/STOP/PING)
robosteam/robot/{mac_address}/status           # robot → server (heartbeat, PONG)
```

`{mac_address}` is the device MAC address in uppercase colon-separated format, e.g. `AA:BB:CC:DD:EE:FF`.  
`{competition_id}` is the Django PK of the `Competition` (Category) model.

**A device's command topic matches its own topic family.** A connection test for
a lap timer goes to `robosteam/laptimer/{mac}/cmd`, not the robot topic — so a
timer never has to subscribe to `robot/…` to hear a command addressed to it.

---

## Payloads

All payloads are JSON. All timestamps are ISO 8601 UTC.

### `robosteam/laptimer/{mac}/event` — Lap timer beam crossing

Published by the lap timer device each time the IR beam is broken.

```json
{
  "seq": 42,
  "ts": "2026-06-27T10:15:30.123Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `seq` | integer | Monotonically increasing counter per device. Used for deduplication under QoS 1. |
| `ts` | string | UTC timestamp of the beam crossing from the device's hardware clock. |

### `robosteam/laptimer/{mac}/status` — Lap timer heartbeat

Published periodically by the lap timer to signal it is alive.

```json
{
  "ts": "2026-06-27T10:15:00.000Z",
  "firmware": "1.0.0"
}
```

### `robosteam/robot/{mac}/status` — Robot heartbeat

Published periodically by the robot.

```json
{
  "ts": "2026-06-27T10:15:00.000Z",
  "firmware": "1.0.0"
}
```

### `robosteam/competition/{competition_id}/cmd` — Competition command (server → devices)

Published by the server to start or stop a run in a given category.

```json
{ "cmd": "START", "run_id": 17 }
```
```json
{ "cmd": "STOP" }
```

| Field | Type | Description |
|-------|------|-------------|
| `cmd` | string | `START` or `STOP` |
| `run_id` | integer | Django PK of the `Run` being started (START only) |

### `robosteam/robot/{mac}/cmd` — Robot-specific command (server → robot)

Sent **only to the robot bound to the team running** (`Team.robot`, Decision 040).
This is what a competition robot should act on. Firmware that acts on the
competition broadcast instead will start whenever *any* team in the category is
sent off.

```json
{ "cmd": "START", "run_id": 17 }
```
```json
{ "cmd": "STOP", "run_id": 17, "reason": "finished" }
```

| Field | Type | Description |
|-------|------|-------------|
| `cmd` | string | `START`, `STOP` or `PING` |
| `run_id` | integer | Django PK of the `Run`. **Always present** on START and STOP, unlike the broadcast |
| `reason` | string | `finished` (run completed on its own) or `timeout`. Absent when a judge voided the run by hand |

**When each command goes out**

| Event | Broadcast topic | Robot topic |
|-------|-----------------|-------------|
| Judge presses START | `{"cmd":"START","run_id":N}` | `{"cmd":"START","run_id":N}` |
| Judge voids the run | `{"cmd":"STOP"}` | `{"cmd":"STOP","run_id":N}` |
| Run completes on its final crossing | *(nothing)* | `{"cmd":"STOP","run_id":N,"reason":"finished"}` |
| `timeout_seconds` expires | `{"cmd":"STOP"}` | `{"cmd":"STOP","run_id":N,"reason":"timeout"}` |
| Broker disconnect voids runs | *(nothing — the broker is gone)* | *(nothing)* |

A team with no robot bound gets the broadcast only. JUDGED categories publish
nothing at all: they have no timing hardware to arm.

Broadcast payloads are **byte-for-byte** what they were before targeting existed,
so already-flashed lap timers see no change.

### `robosteam/robot/{mac}/cmd` — `PING` (connection test)

Sent when a participant presses **Test connection** on `/contest/participate/robots/`,
or an organiser does from the device console.

```json
{ "cmd": "PING", "nonce": "9c302b032ef6458c" }
```

The robot must reply on its **status** topic, echoing the nonce unchanged:

```json
{
  "device_id": "AA:BB:CC:DD:EE:F1",
  "ts":        "2026-08-05T04:06:29.480Z",
  "cmd":       "PONG",
  "nonce":     "9c302b032ef6458c",
  "firmware":  "1.0.0"
}
```

`reply: "PONG"` is accepted in place of `cmd`, and `firmware_version` in place of
`firmware`. The reply rides on the **status** topic, not a separate one, so it
doubles as a heartbeat.

`mqtt_bridge` matches the nonce to the open `DevicePing`, records the round trip
and pushes it to the browser over the `devices` WebSocket group. A nonce that is
unknown or already answered is **dropped, not credited** — a late reply must not
make a later test look instantaneous.

Firmware without a PING handler simply never replies; the UI falls back to
reporting heartbeat freshness, so an un-reflashed robot still tests usefully.
`tests/robot_sim.py --no-pong` reproduces exactly that, for testing the fallback.

### `robosteam/laptimer/{mac}/cmd` — Lap timer command (server → timer)

Only `PING` is sent here. A timer takes no part in START/STOP — it reports
crossings and the server decides what they mean — but it must answer a
connection test from the organiser device console.

```json
{ "cmd": "PING", "nonce": "9c302b032ef6458c" }
```

The timer replies on **its own** status topic, `robosteam/laptimer/{mac}/status`,
in the same shape as a robot's PONG. The server matches nonces without caring
which topic family the reply arrived on.

---

## Server-side processing (mqtt_bridge management command)

| Topic | Handler |
|-------|---------|
| `laptimer/+/event` | Create `LapEvent` (deduplicate by device+sequence); mark device ONLINE |
| `laptimer/+/status` | Update `LapTimerDevice.last_seen` and status=ONLINE; match a PONG |
| `robot/+/status` | Update `LapTimerDevice.last_seen` and status=ONLINE (device_type=ROBOT); match a PONG |

**Raw LapEvents** (competition/run = null) are written as they arrive. Phase 4 scoring logic assigns them to the correct Competition and Run when a run is in ACTIVE state.

A PONG rides on the status topic rather than a topic of its own, so it doubles
as a heartbeat and needs no extra subscription.

---

## Writing firmware against this protocol

Read this before flashing anything.

1. **Act on your own topic, not the broadcast.** `robosteam/robot/{mac}/cmd` is
   the real command channel. A robot that acts on
   `robosteam/competition/{id}/cmd` starts whenever *any* team in the category
   is sent off. The broadcast exists for the lap timer and for firmware that
   predates team binding.
2. **Latch it.** Once the server has addressed the robot on its own topic, it
   has been bound to a team — ignore the broadcast from then on. Before that,
   keep obeying the broadcast so an unbound robot still works. The Phase 7
   library does this in `_on_message` and exposes it as `comp.bound_to_team`.
3. **Deduplicate.** A bound robot receives each command on both topics within
   milliseconds. Track the last `(cmd, run_id)` you acted on. Remember the
   broadcast STOP carries **no** `run_id` while the targeted one does.
4. **Echo the PING nonce byte for byte.** The server drops a reply whose nonce
   it does not recognise, so a mangled nonce is indistinguishable from silence.
5. **Report `firmware_version`** (or `firmware`) in every heartbeat. It shows in
   the organiser device console and is how you tell at a glance which robots on
   the field are still running old code.
6. **Never report timing.** See below.

Reference implementations: `picobot-competition-library/competition.py` (Phase 7,
MicroPython) and `robosteam-lap-timer/src/main.cpp` (Phase 6, Arduino/C++).
`picobot-competition-library/test_competition.py` runs the library's protocol
logic on a PC with no hardware.

---

## Authoritative timing rule

The lap timer is the **only** timing source. The robot never self-reports start or end time. All elapsed times are calculated by the server from consecutive `LapEvent.timestamp_utc` values.
