# RoboACADEMY — End-to-End Testing Quick Start

This directory contains the lap timer simulator and the full test scenario reference.

## Prerequisites

- Django dev server running: `http://localhost:8000`
- `mqtt_bridge` running in a dedicated terminal (see below)
- Python venv active: `source ~/venv/bin/activate`
- MQTT broker reachable at `<broker-address>:1883`

## Start the MQTT bridge (Terminal A — keep open throughout testing)

```bash
ssh <your-server>
cd ~/competitions/scoreboard && source ~/venv/bin/activate
python manage.py mqtt_bridge
```

The bridge log should show: `Connected to MQTT broker <broker-address>:1883`

## Start the simulator (Terminal B)

```bash
ssh <your-server>
source ~/venv/bin/activate
export MQTT_USERNAME=<mqtt-user>
export MQTT_PASSWORD='your-mqtt-password'
```

### Auto mode — scripted crossing intervals

```bash
python ~/competitions/tests/lap_timer_sim.py \
  --mac AA:BB:CC:DD:EE:01 \
  --competition 1 \
  --laps 3 \
  --sequence "2,6,5,4"
```

The simulator starts, sends a heartbeat, and waits for a `START` command from the server.
When the judge clicks **Start** in the browser, it fires 4 crossings at the specified intervals.

**`--sequence` format:** `"<delay0>,<delay1>,...,<delayN>"` where N = num_laps.
- `delay0` = seconds to wait before crossing 0 (pre-start settle — NOT counted in `time_ms`)
- `delay1..N` = seconds between subsequent crossings (these ARE counted in `time_ms`)
- Example: `"2,6,5,4"` → crossing 0 at +2s; lap 1 in 6s; lap 2 in 5s; lap 3 in 4s → `time_ms = 15,000 ms`

### Manual mode — press Enter per crossing (for DNF/void tests)

```bash
python ~/competitions/tests/lap_timer_sim.py \
  --mac AA:BB:CC:DD:EE:01 \
  --competition 1 \
  --laps 3 \
  --mode manual
```

Press Enter each time you want a crossing published. Click **Void** in the browser
at any point to end the run early (tests the VOIDED state path).

### Uniform interval mode (smoke test)

```bash
python ~/competitions/tests/lap_timer_sim.py \
  --mac AA:BB:CC:DD:EE:01 \
  --competition 1 \
  --laps 1 \
  --interval 5
```

Publishes 2 crossings 5 s apart → `time_ms ≈ 5,000 ms`.

## Admin setup (do once before running S1)

1. Log in: `http://localhost:8000/admin/` (user: `admin`)
2. Create **Contest**: `RoboACADEMY Test Cup 2026`, status=OPEN,
   `points_table = {"1": 10, "2": 8, "3": 6, "4": 4, "5": 2}`
3. Create **Category 1** (verbose: Competition): `Line Following`, contest=Test Cup,
   `competition_type=TIMED`, `num_laps=3`, `num_runs=3`, `timeout_seconds=120`
4. Create **Category 2**: `Object Manipulation`, contest=Test Cup,
   `competition_type=JUDGED`, `num_runs=3`
5. Create **Teams**: `Team Alpha`, `Team Bravo`, `Team Charlie` (all under Test Cup 2026)
6. **Do NOT** pre-create any LapTimerDevice — Scenario S1 tests auto-registration.
7. After S1 registers the device: rename `friendly_name = "Sim Timer 1"`,
   then assign it to the Line Following category (`lap_timer` field).

## Run the test scenarios

See **TEST_SCENARIOS.md** for the complete S1–S10 reference with expected outcomes
and verification checklists.

Recommended order: S1 → S2 → S3 → S4 → S5a → S5b → S5c → S6a → S6b → S6c → S7 → S8 → S9 → S10

**Critical:** Keep the same simulator process for S2–S4 (Team Alpha) and S5a–S5c (Team Bravo).
The sequence counter increments across runs within a session. Restarting resets it to 1,
which the bridge deduplicates silently — the run would never finalize.

## Run the Django test suite

After completing all scenarios, run from the server:

```bash
cd ~/competitions/scoreboard && source ~/venv/bin/activate
python manage.py test scoring devices mqtt_bridge contest
```

All 35+ tests must pass. If any regression is found, fix it before moving to Phase 6.

## Deduplication test (S10f)

```bash
mosquitto_pub -h <broker-address> -p 1883 -u "$MQTT_USERNAME" -P "$MQTT_PASSWORD" \
  -t robosteam/laptimer/AA:BB:CC:DD:EE:01/event \
  -m '{"seq": 1, "ts": "2026-06-27T12:00:00.000Z"}'
```

Expect: `LapEvent.objects.count()` unchanged; bridge log shows "Duplicate … ignored".


---

## Robot Simulator (`tests/robot_sim.py`)

Simulates a PicoBot robot over MQTT. Use this alongside `lap_timer_sim.py`
for S18–S20 scenarios that require a robot to receive START/STOP commands.

### Start the robot simulator (Terminal C)

```bash
ssh <your-server>
source ~/venv/bin/activate
export MQTT_USERNAME=<mqtt-user>
export MQTT_PASSWORD='your-mqtt-password'
```

#### Basic — listen for commands and log them

```bash
python ~/competitions/tests/robot_sim.py \
  --mac 11:22:33:44:55:01 \
  --competition 4
```

The sim connects, publishes a heartbeat, and waits silently.
When the judge clicks **Start** in the browser, you will see:

```
[12:34:56] >>> START received (source=competition, run_id=42) <<<
             full payload: {"cmd": "START", "run_id": 42, "competition_id": 4}
```

When **Void** or a timeout fires:

```
[12:35:28] >>> STOP received  (source=competition, run_id=42) <<<
             full payload: {"cmd": "STOP", "run_id": 42}
```

#### With ACK response (`--respond`)

Publishes a status message back to `robosteam/robot/{mac}/status` on every
START/STOP, confirming the robot received the command:

```bash
python ~/competitions/tests/robot_sim.py \
  --mac 11:22:33:44:55:01 \
  --competition 4 \
  --respond
```

Example output after a START:

```
[12:34:56] >>> START received (source=competition, run_id=42) <<<
             full payload: {"cmd": "START", "run_id": 42, "competition_id": 4}
[12:34:56] ACK sent: {"device_id": "11:22:33:44:55:01", "ts": "2026-06-27T12:34:56.123Z", "firmware_version": "robot-sim-1.0", "ack": "START", "run_id": 42}
```

#### Two robots simultaneously (S20 — multi-robot START)

Open two terminals, each with a different `--mac`:

```bash
# Terminal C
python ~/competitions/tests/robot_sim.py --mac 11:22:33:44:55:01 --competition 4

# Terminal D
python ~/competitions/tests/robot_sim.py --mac 11:22:33:44:55:02 --competition 4
```

Both sims subscribe to `robosteam/competition/4/cmd`. When the judge starts
a run, both consoles should print the START command within the same second.

#### Robot sim + lap timer sim together (S19)

```bash
# Terminal B — lap timer
python ~/competitions/tests/lap_timer_sim.py \
  --mac AA:BB:CC:DD:EE:01 --competition 4 --laps 3 --sequence "2,6,5,4"

# Terminal C — robot
python ~/competitions/tests/robot_sim.py \
  --mac 11:22:33:44:55:01 --competition 4
```

Judge starts run → robot sim logs START → lap timer fires 4 crossings →
server records `time_ms` → scoreboard updates via WebSocket.

### CLI argument reference — robot_sim.py

| Argument | Default | Description |
|----------|---------|-------------|
| `--mac` | `11:22:33:44:55:01` | Robot MAC (used as device_id and MQTT client_id) |
| `--competition` | `1` | Category DB id (for competition-level cmd topic) |
| `--broker` | `<broker-address>` | MQTT broker host |
| `--port` | `51883` | MQTT broker port |
| `--user` | `$MQTT_USERNAME` | MQTT username (overrides env var) |
| `--password` | `$MQTT_PASSWORD` | MQTT password (overrides env var) |
| `--respond` | off | Send ACK status message on each START/STOP |

---

## Phase H — validating a running deployment

The scenario drivers above use the Django test client, so they exercise views but
not the deployed stack. These two go over real HTTP/WebSocket against a server
that is actually running, which is what caught the redis-py socket-timeout bug
and the SQLite lock contention.

Bring the deployment up first — all four, in this order:

```bash
redis-server            # or: systemctl status redis
cd ~/competitions/scoreboard
setsid nohup ~/venv/bin/python -u manage.py runserver 0.0.0.0:8000 > /tmp/server.log 2>&1 < /dev/null &
setsid nohup ~/venv/bin/python -u manage.py mqtt_bridge          > /tmp/bridge.log 2>&1 < /dev/null &
setsid nohup ~/venv/bin/python -u manage.py timeout_runs --interval 5 > /tmp/timeout.log 2>&1 < /dev/null &
```

### role_walkthrough.py — one pass per role, over HTTP

55 checks covering anonymous, participant, judge, organiser and admin. Goes
through Daphne, the middleware stack, real session cookies and real CSRF, so it
also proves things a unit test cannot: that `XFrameOptionsMiddleware` is live,
that logout is POST-only, that an anonymous visitor gets boards with no controls,
and that a Participant is refused the judge endpoints by the deployed app.

```bash
cd ~/competitions/scoreboard
PYTHONPATH=. ~/venv/bin/python ../tests/role_walkthrough_setup.py
#   -> contest=<id> timed=<id> judged=<id> team=<id>
~/venv/bin/python ../tests/role_walkthrough.py <contest> <timed> <judged> <team>
PYTHONPATH=. ~/venv/bin/python ../tests/role_walkthrough_teardown.py
```

Setup creates throwaway `walk_*` users (password in the script), a `Walk Cup`
contest with a TIMED and a JUDGED category, a pending user for the admin to
approve and a pending device. Teardown removes all of it. Re-run setup before
each run — the script consumes run attempts. Exit code is non-zero if any check
fails.

### ws_watch.py — prove the board WebSocket stays up

```bash
~/venv/bin/python ../tests/ws_watch.py <competition_id> <seconds>
```

Connects to `ws://127.0.0.1:8000/ws/competition/<id>/` and logs every push.
Leave it idle for **more than 45 seconds**, then trigger an update from another
process (score a run, or call `push_competition_update`). It must still be
connected and must receive the push.

This is the regression test for the redis-py 8 `socket_timeout` bug: an idle
consumer used to be killed with close code 1011 after ~5 s, so every spectator
board sat in a reconnect loop. If you ever see `redis.exceptions.TimeoutError`
in the server log, check `REDIS_SOCKET_TIMEOUT` — it must stay well above
`channels_redis`'s 5 s BRPOP window.
