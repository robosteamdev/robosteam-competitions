import json
import logging

import paho.mqtt.publish as publish
from django.conf import settings

logger = logging.getLogger(__name__)


def _auth():
    return {'username': settings.MQTT_USERNAME, 'password': settings.MQTT_PASSWORD}


def publish_competition_command(competition_id: int, cmd: str, **extra) -> None:
    """Publish a START or STOP command to all devices in a competition."""
    topic = f'robosteam/competition/{competition_id}/cmd'
    payload = json.dumps({'cmd': cmd, **extra})
    publish.single(
        topic,
        payload=payload,
        hostname=settings.MQTT_BROKER_HOST,
        port=settings.MQTT_BROKER_PORT,
        auth=_auth(),
        qos=1,
    )


def publish_robot_command(mac: str, cmd: str, **extra) -> None:
    """Publish a command to a specific robot."""
    _publish_device_command('robot', mac, cmd, **extra)


def publish_laptimer_command(mac: str, cmd: str, **extra) -> None:
    """Publish a command to a specific lap timer.

    Only PING uses this today. A timer takes no part in START/STOP — it reports
    crossings and the server decides what they mean — but it does have to answer
    a connection test, and its command topic has to match its own topic family
    or the firmware would need to subscribe to `robot/…` to hear it.
    """
    _publish_device_command('laptimer', mac, cmd, **extra)


def _publish_device_command(family: str, mac: str, cmd: str, **extra) -> None:
    topic = f'robosteam/{family}/{mac}/cmd'
    payload = json.dumps({'cmd': cmd, **extra})
    publish.single(
        topic,
        payload=payload,
        hostname=settings.MQTT_BROKER_HOST,
        port=settings.MQTT_BROKER_PORT,
        auth=_auth(),
        qos=1,
    )


def run_robot_mac(run):
    """The MAC of the robot bound to this run's team, or None if the team has no robot.

    Binding a robot is optional and always will be — an organiser-created team
    has no participant account to self-serve, and a JUDGED category may not
    involve a Pico at all. No robot simply means no targeted command.
    """
    robot = run.team.robot
    return robot.device_id if robot is not None else None


def publish_run_command(run, cmd: str, *, broadcast=True, broadcast_extra=None,
                        **targeted_extra) -> None:
    """Send `cmd` for a run: broadcast to the competition, and to its own robot.

    Two publishes, deliberately (Phase 5.21 E):

    * The **broadcast** on `robosteam/competition/{id}/cmd` is what the lap
      timer listens to, and what firmware that predates team binding listens
      to. Its payload is byte-for-byte what it was before this function
      existed — that is why `broadcast_extra` is a separate argument rather
      than sharing `**targeted_extra`. Already-flashed timers must see no
      change at all from this section.
    * The **targeted** publish on `robosteam/robot/{mac}/cmd` goes only to the
      robot bound to this run's team, and always carries `run_id`. Without it
      every robot in the competition starts when any one team is sent off,
      which is the bug this section exists to fix.

    Never raises. One failed publish must not stop the other, and neither may
    take down a judge action: the run state is already written by the time we
    get here, so a broker problem is a logged warning, not a lost run (Phase G
    rule — `logger.exception`, never a silent `pass`).
    """
    if broadcast:
        try:
            publish_competition_command(run.competition_id, cmd, **(broadcast_extra or {}))
        except Exception:
            logger.exception(
                'MQTT publish failed: broadcast %s for competition %s run %s',
                cmd, run.competition_id, run.pk,
            )

    mac = run_robot_mac(run)
    if mac is None:
        return

    try:
        publish_robot_command(mac, cmd, run_id=run.pk, **targeted_extra)
    except Exception:
        logger.exception(
            'MQTT publish failed: targeted %s to robot %s for run %s',
            cmd, mac, run.pk,
        )
