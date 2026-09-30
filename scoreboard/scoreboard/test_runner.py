"""Test runner that keeps the suite hermetic (Phase G1, finding H9).

`publish_competition_command` runs on every START / STOP / timeout-void, and
`paho.mqtt.publish.single` opens a real TCP socket to `MQTT_BROKER_HOST`. Left
unpatched the suite either depends on a reachable broker or stalls for the OS
connect timeout on every run transition — and because the call sites swallow the
failure, nobody notices which one it was.

Patching `single` once for the whole run makes every test hermetic. Tests that
assert on topic and payload patch `mqtt_bridge.publisher.publish.single`
themselves; that patch targets the same attribute and simply shadows this one
for its duration.
"""
from django.test.runner import DiscoverRunner

import paho.mqtt.publish


def _refuse_network_publish(*args, **kwargs):
    """Stand-in for `paho.mqtt.publish.single` — succeeds without a broker."""
    return None


class HermeticTestRunner(DiscoverRunner):
    """DiscoverRunner that forbids the test suite from reaching the broker."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._real_publish_single = paho.mqtt.publish.single
        paho.mqtt.publish.single = _refuse_network_publish

    def teardown_test_environment(self, **kwargs):
        paho.mqtt.publish.single = self._real_publish_single
        super().teardown_test_environment(**kwargs)
