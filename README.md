# robosteam-competitions

![Co-funded by the European Union · ROBO STEAM ACADEMY · partner logos](images/logos_strip.png)

**Erasmus+ project ROBO STEAM ACADEMY** (KA220-VET-7CF4F308) — co-funded by the European Union — <https://robosteam.eu/>

Part of the **PicoBot Teachers' Toolkit** (lesson plans, student materials, slides and guides in five languages):
<https://github.com/robosteamdev/robo-steam-academy-teachers-toolkit>

**The ROBO STEAM ACADEMY competition platform** — the live scoreboard and competition management web application of the
project's robot competitions. Organisers create contests and categories, teams register and claim their robots, judges
start and stop runs from a phone, the **lap timer** reports every beam crossing, and the scoreboard updates live.

- Live scoreboard: <https://live.robosteam.eu/>
- Competition information (rules, how to take part, how to host a competition): <https://competitions.robosteam.eu/>

Schools and partners may run their **own copy** for a local, multisite or international competition.

## Competition Information Hub

At [competitions.robosteam.eu](https://competitions.robosteam.eu/), you can find all competition-related resource materials, including:

* 🤖 **How to assemble** and program the PicoBot robot
* 📅 **Upcoming** competitions and events
* 📊 **Live scoreboards** for ongoing competitions
* 🏆 **Results and scoreboards** from past competitions
* 🌐 **How to host** your own local, multisite, or international competition using the ROBO STEAM ACADEMY platform
* 📝 **How to register** and participate in a competition
* 📜 **Official** competition rules
* 🛠️ **Instructions** on how to create tracks for different competition categories
* 🎥 **Video tutorials** on how to set up and judge competitions
* 🔗 **Direct access** to the [Live Scoreboard](https://live.robosteam.eu/)
* ✨ **And much more** competition-related information!

## How it fits together

| Part | Repository |
|---|---|
| This platform: Django web application + MQTT bridge + scoring | this repository |
| Lap timer (NodeMCU ESP8266 + infrared beam) — the clock of every run | <https://github.com/robosteamdev/robosteam-lap-timer> |
| Lap-timer box (3D print) | <https://github.com/robosteamdev/robosteam-competition-3d-prints> |
| Robot side: START/STOP library for PicoBot (MicroPython) | <https://github.com/robosteamdev/picobot-competition-library> |

The devices talk to the platform over **MQTT** (Mosquitto broker); the protocol is described in `docs/MQTT_PROTOCOL.md`.

## Folders

| Folder | Contents |
|---|---|
| `scoreboard/` | the Django project (`manage.py`): apps `contest` (contests, teams, runs, judge and organiser pages), `devices` (lap timers and robots), `mqtt_bridge` (MQTT ↔ database), `scoring` (ranking rules), `public` (public pages) |
| `docs/` | `DEPLOYMENT.md` (going to production step by step), `MQTT_PROTOCOL.md` (topics and messages) |
| `deploy/` | systemd service files for the web process, the MQTT bridge and the run-timeout sweep |
| `tests/` | simulators (lap timer, robots), scenario scripts and test instructions |
| `media/` | pictures used by the public pages |

## Run your own copy

1. Python 3 and a virtual environment: `python -m venv venv`, then `venv/bin/pip install -r requirements.txt`
   (`dev-requirements.txt` for development).
2. Copy `.env.example` to `.env` and fill in your values — at least a new `SECRET_KEY`, the MQTT broker address and
   the broker user and password. `.env` holds secrets: never publish it.
3. In `scoreboard/`: `python manage.py migrate`, then `python manage.py createsuperuser`.
4. Start the web process, the MQTT bridge (`python manage.py mqtt_bridge`) and the run-timeout sweep
   (`python manage.py timeout_runs`) — for a server, use the service files in `deploy/` and follow
   `docs/DEPLOYMENT.md`.

### Production build (MySQL)
The production build requires **`mysqlclient`**. 

> [!NOTE]
> This library requires specific system-level binary packages to build successfully. Please refer to the official [mysqlclient PyPI documentation](https://pypi.org/project/mysqlclient/) for installation instructions specific to your operating system.

## In the toolkit

Module **M12** Capstone of the PicoBot Teachers' Toolkit (session 25: how a competition works; sessions 28–29:
Master Control and the race) and A4 Experiment set-ups (the competition track with the lap timer).

## Licence and credits

Code: MIT licence (see `LICENSE`). Please credit "ROBO STEAM ACADEMY, Erasmus+ project KA220-VET-7CF4F308" — <https://robosteam.eu/>

Funded by the European Union. Views and opinions expressed are however those of the author(s) only and do not
necessarily reflect those of the European Union or the Human Resource Development Centre (HRDC). Neither the European
Union nor the granting authority can be held responsible for them.
