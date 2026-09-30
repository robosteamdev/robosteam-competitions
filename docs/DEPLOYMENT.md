# Deployment runbook — going to production

An ordered procedure for going to production. Written
2026-08-05, before any cutover has happened, from a live audit of the dev VM.

**Nothing here has been executed against a production host.** The `check --deploy`
results below were produced by running the real command with production values
supplied as environment overrides, so the *settings* half is verified. The systemd
units validate clean under `systemd-analyze verify` but have never been started.

---

## 0. The one-line summary

Every warning Django raises about this deployment is a **value in `.env`**, not a code
change. The settings module already reads all of them and fails loudly when
`SECRET_KEY` is absent (Phase G2). What is genuinely missing is **process
supervision** — see §4.

---

## 1. Decisions needed before you start

These are not technical blockers; they are decisions for the organiser who runs the server.

| Decision | Why it matters |
|----------|----------------|
| The production hostname | Goes in `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. Currently `*`, which Django accepts but which disables Host-header validation |
| SQLite or MySQL | SQLite is in WAL + IMMEDIATE (Decision 038) and survives the three-process stack, but a 30-team event with concurrent judges is the case it was never tested at |
| SMTP host and credentials | Without them the console backend prints password-reset mail to the journal, so nobody can reset a password |
| Whether TLS terminates in Nginx | `USE_HTTPS=True` assumes something in front is doing TLS. See §5 — flipping it early locks everyone out |

---

## 2. Verified: what `check --deploy` says

Today, against the dev `.env`:

```
security.W004  SECURE_HSTS_SECONDS not set
security.W008  SECURE_SSL_REDIRECT not True
security.W009  SECRET_KEY is the insecure dev one
security.W012  SESSION_COOKIE_SECURE not True
security.W016  CSRF_COOKIE_SECURE not True
security.W018  DEBUG is True
ckeditor.W001  CKEditor 4.22.1 is EOL
```

With production values supplied, **all six security warnings clear**:

```bash
cd ~/competitions/scoreboard
KEY=$(~/venv/bin/python -c 'from django.core.management.utils import get_random_secret_key as g; print(g())')
SECRET_KEY="$KEY" DEBUG=False USE_HTTPS=True \
ALLOWED_HOSTS='competitions.robosteam.eu' \
CSRF_TRUSTED_ORIGINS='https://competitions.robosteam.eu' \
SECURE_HSTS_SECONDS=3600 \
  ~/venv/bin/python manage.py check --deploy
```

What remains is two warnings, both deliberate:

- **`ckeditor.W001`** — H10, deferred pending a licence review of CKEditor 5.
- **`security.W021` (HSTS preload)** — *leave this one*. Submitting to the browser
  preload list is close to irreversible and is not something to switch on the day
  you first serve TLS. `SECURE_HSTS_SECONDS` follows the same logic: start at
  `3600`, raise to a year only after the certificate has renewed cleanly once.

`USE_HTTPS=True` alone clears W008, W012 and W016 — one switch, three warnings.

---

## 3. The cutover, in order

### 3.1 — Merge and tag first

Deploy from a tagged commit, not a branch tip, so a rollback has somewhere to go.

### 3.2 — `collectstatic` — **the step that fails silently**

```bash
cd ~/competitions/scoreboard
~/venv/bin/python manage.py collectstatic --noinput
```

With `DEBUG=True` Django serves CSS straight from each app directory, so a stale
collected copy is invisible in dev. With `DEBUG=False` the collected copy is what
ships. The collected files were once four weeks stale and nobody noticed.
**Make this part of every deploy, not just the first.**

### 3.3 — Generate a real `SECRET_KEY`

```bash
~/venv/bin/python -c 'from django.core.management.utils import get_random_secret_key as g; print(g())'
```

Into `.env`, never into git. There is no fallback: Django refuses to start without it.

### 3.4 — Write the production `.env`

`.env.example` is the reference and is kept in step with `settings.py`. The values
that change for production:

```ini
SECRET_KEY=<the generated 50-char key>
DEBUG=False
ALLOWED_HOSTS=competitions.robosteam.eu
CSRF_TRUSTED_ORIGINS=https://competitions.robosteam.eu
USE_HTTPS=False          # ← still False here. See §5
SECURE_HSTS_SECONDS=0    # ← raise later, after TLS has proven itself
DEVICE_REG_SECRET=<a shared secret>
EMAIL_HOST=...           # and the rest of the EMAIL_* block
```

Leave `REDIS_SOCKET_TIMEOUT=60`. It must stay well above the 5 s `BRPOP` window in
`channels_redis`, or **every board WebSocket dies silently after ~5 s idle**
(Decision 039 — this cost a whole debugging session once).

### 3.5 — Migrate

```bash
~/venv/bin/python manage.py migrate
```

### 3.6 — Install the services

See §4. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now robosteam-web robosteam-mqtt-bridge robosteam-timeout-runs
systemctl status robosteam-*
```

### 3.7 — Nginx and TLS

Terminate TLS in Nginx, proxy to `127.0.0.1:8000`, and **pass WebSocket upgrade
headers** — `/ws/competition/<id>/` and `/ws/devices/` both need them, or the
scoreboard silently stops updating and every device badge freezes.

### 3.8 — **Only now** flip `USE_HTTPS=True`

```bash
# in .env
USE_HTTPS=True
sudo systemctl restart robosteam-web
```

Verify with `check --deploy`, then log in as each role.

---

## 4. Process supervision — the real gap

**Today the app runs `manage.py runserver` under `nohup`, with no process manager and
nothing to restart it after a reboot.** `runserver` is Django's *development* server:
it auto-reloads, it is not built for concurrency, and the Django docs say plainly not
to use it in production. A power cut mid-competition currently means someone SSHing
in and starting three processes by hand.

Three unit files in `deploy/` fix that. They are validated (`systemd-analyze verify`
is clean) but **have never been started** — installing them stops the current dev
processes, so it was left as a deliberate step rather than done in passing.

| Unit | Replaces |
|------|----------|
| `robosteam-web.service` | `manage.py runserver 0.0.0.0:8000` → `daphne … scoreboard.asgi:application` |
| `robosteam-mqtt-bridge.service` | `manage.py mqtt_bridge` |
| `robosteam-timeout-runs.service` | `manage.py timeout_runs --interval 5` |

```bash
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now robosteam-web robosteam-mqtt-bridge robosteam-timeout-runs
```

Notes baked into the units:

- `Requires=redis-server.service`. Without Redis each process gets its own in-memory
  channel layer and **browsers never see lap events** (finding C2).
- `StartLimitIntervalSec=0`, in `[Unit]` — systemd **silently ignores it in
  `[Service]`**, which is exactly the trap that would leave a crashed service dead
  after five quick restarts, mid-event.
- `python -u` on the two workers, so `journalctl -f` shows lap events as they land.

Logs: `journalctl -u robosteam-mqtt -f` (also `robosteam-web`, `robosteam-timeout`).

---

## 5. Traps that have already cost time

| Trap | What happens |
|------|--------------|
| `USE_HTTPS=True` before TLS is live | Secure cookies over plain HTTP + an SSL redirect loop. **Everyone is locked out, including you.** Flip it last (Decision 037) |
| Skipping `collectstatic` | CSS changes silently do not appear. Invisible in dev because `DEBUG=True` bypasses the collected copy |
| `REDIS_SOCKET_TIMEOUT` at or below 5 s | Every board WebSocket dies after ~5 s idle, with no error (Decision 039) |
| Restarting only the web process after a settings change | `mqtt_bridge` and `timeout_runs` **do not autoreload**. They keep running the old code — and a stale `services.py` there means device badges stop updating (Phase 5.21 G) |
| `pkill -f 'manage.py mqtt_bridge'` over SSH | The pattern matches the SSH command's own command line and kills the shell first. Use `manage[.]py` |
| Multiple web workers with `CACHES` on LocMemCache | The robot-claim rate limit becomes per-process, so N workers means N× the limit. Point `CACHES` at the existing Redis if you scale out |
| Assuming SQLite is fine | It is in WAL + IMMEDIATE and works for the three-process stack (Decision 038), but has never been tested at 30 teams with concurrent judges |

---

## 6. Verifying a deployment

The unit suite proves logic; it does not prove a deployment. Every one of the three
Phase H bugs passed 397 unit tests and still broke on the running stack.

```bash
cd ~/competitions/scoreboard
~/venv/bin/python manage.py check --deploy       # only the two deliberate warnings
~/venv/bin/python manage.py test                 # 513, OK

# against the running stack, in order of what they prove:
PYTHONPATH=. ~/venv/bin/python ../tests/role_walkthrough_setup.py   # prints four ids
~/venv/bin/python ../tests/role_walkthrough.py <contest> <timed> <judged> <team>
PYTHONPATH=. ~/venv/bin/python ../tests/role_walkthrough_teardown.py

~/venv/bin/python ../tests/robot_walkthrough.py            # 29
~/venv/bin/python ../tests/live_targeted_start_check.py    # 18, needs the broker
~/venv/bin/python ../tests/live_connection_test_check.py   # 15, needs the broker
~/venv/bin/python ../tests/live_offline_badge_check.py     # 6
~/venv/bin/python ../tests/ws_scoping_check.py             # no MAC leak
~/venv/bin/python ../tests/reregistration_check.py         # claimed robot survives
```

Then by eye: open a board in two browsers and confirm a lap event appears in both
without a refresh. That is the one thing no script checks for you.

---

## 7. Rollback

`main` is tagged at each sign-off (`signoff-2026-08-04` and later). To go back:

```bash
cd ~/competitions
git checkout <tag>
~/venv/bin/python scoreboard/manage.py migrate     # only if migrations moved
~/venv/bin/python scoreboard/manage.py collectstatic --noinput
sudo systemctl restart robosteam-web robosteam-mqtt-bridge robosteam-timeout-runs
```

**Migrations do not roll back automatically.** Check whether the tag you are going
back to predates a migration; if it does, reverse it explicitly before switching.

Note the 2026-08-04 caveat: `main` was advanced with `git branch -f` rather than a
checkout, because all three processes serve from that working tree and a checkout
swaps files out underneath them. Under systemd, stop the services first.

---

## 8. Before you go live

🔑 **Use your own MQTT credentials.** Create a broker user and password for your event and put them only in
`.env` on the server. Never reuse example values from documentation.
