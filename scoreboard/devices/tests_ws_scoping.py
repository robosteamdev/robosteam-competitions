"""
Phase 5.21 G — who may watch the devices WebSocket, and what they see.

Found during the section G read-through and confirmed with a live probe before
being fixed: the socket accepted any authenticated user and pushed **every**
device to one group. Every HTTP path in the robot feature is owner-scoped — the
claim form never lists unclaimed MACs, `_my_robot` 404s on someone else's
device — and this walked around all of it.

It matters because claiming is MAC-only and first-claimer-wins (Decision 042),
so a MAC *is* the credential. A participant watching the stream could harvest
the MAC of every robot not yet claimed — at exactly the moment before its owner
claims it, since robots heartbeat from the moment they are powered up — and
take it first.

These tests drive the real consumer through the real ASGI application.
"""
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import Group, User
from django.test import TransactionTestCase

from devices.models import DeviceType, LapTimerDevice, RegistrationStatus
from scoreboard.asgi import application as asgi_app

MINE   = 'D0:00:00:00:00:01'
RIVALS = 'D0:00:00:00:00:02'
TIMER  = 'D0:00:00:00:00:03'


def make_user(username, group_name):
    user = User.objects.create_user(username=username, password='test')
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
    return user


def make_device(mac, name, owner=None, device_type=DeviceType.ROBOT):
    return LapTimerDevice.objects.create(
        device_id=mac, friendly_name=name, device_type=device_type,
        registration_status=RegistrationStatus.ACTIVE, owner=owner,
    )


async def push(device_mac):
    """Push one device the way mqtt_bridge does on a heartbeat."""
    from devices.services import push_device_update

    @database_sync_to_async
    def _push():
        push_device_update(LapTimerDevice.objects.get(device_id=device_mac))

    await _push()


async def connect_as(user):
    """A connected communicator with `user` in scope, as AuthMiddleware would."""
    communicator = WebsocketCommunicator(asgi_app, '/ws/devices/')
    communicator.scope['user'] = user
    connected, _ = await communicator.connect()
    return communicator, connected


class DeviceSocketScopingTest(TransactionTestCase):
    """TransactionTestCase: the consumer runs outside the test's transaction."""

    def setUp(self):
        self.owner    = make_user('ws-owner', 'Participant')
        self.snooper  = make_user('ws-snooper', 'Participant')
        self.organiser = make_user('ws-organiser', 'Organiser')
        self.judge    = make_user('ws-judge', 'Judge')

        make_device(MINE, 'My Rover', owner=self.owner)
        make_device(RIVALS, 'Rivals Rover', owner=self.snooper)
        make_device(TIMER, 'Venue Timer', device_type=DeviceType.LAPTIMER)

    async def test_anonymous_is_refused(self):
        from django.contrib.auth.models import AnonymousUser

        communicator, connected = await connect_as(AnonymousUser())
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_owner_receives_their_own_device(self):
        communicator, connected = await connect_as(self.owner)
        self.assertTrue(connected)

        await push(MINE)
        message = await communicator.receive_json_from(timeout=3)
        self.assertEqual(message['device_id'], MINE)
        await communicator.disconnect()

    async def test_participant_never_sees_another_participants_robot(self):
        """The finding, as a regression test."""
        communicator, connected = await connect_as(self.owner)
        self.assertTrue(connected)

        await push(RIVALS)
        self.assertTrue(await communicator.receive_nothing(timeout=1))
        await communicator.disconnect()

    async def test_participant_never_sees_an_unclaimed_robot(self):
        """An unclaimed MAC is the whole credential needed to claim it."""
        @database_sync_to_async
        def unclaim():
            LapTimerDevice.objects.filter(device_id=RIVALS).update(owner=None)

        await unclaim()

        communicator, connected = await connect_as(self.owner)
        self.assertTrue(connected)

        await push(RIVALS)
        self.assertTrue(await communicator.receive_nothing(timeout=1))
        await communicator.disconnect()

    async def test_participant_never_sees_a_lap_timer(self):
        """Lap timers are organiser-only infrastructure (Decision 040)."""
        communicator, connected = await connect_as(self.owner)
        self.assertTrue(connected)

        await push(TIMER)
        self.assertTrue(await communicator.receive_nothing(timeout=1))
        await communicator.disconnect()

    async def test_organiser_sees_every_device(self):
        """The organiser console exists to show exactly this."""
        communicator, connected = await connect_as(self.organiser)
        self.assertTrue(connected)

        for mac in (MINE, RIVALS, TIMER):
            await push(mac)
            message = await communicator.receive_json_from(timeout=3)
            self.assertEqual(message['device_id'], mac)
        await communicator.disconnect()

    async def test_judge_sees_every_device(self):
        communicator, connected = await connect_as(self.judge)
        self.assertTrue(connected)

        await push(TIMER)
        message = await communicator.receive_json_from(timeout=3)
        self.assertEqual(message['device_id'], TIMER)
        await communicator.disconnect()

    async def test_owner_id_is_never_sent_to_the_browser(self):
        """It is routing information for the consumer, not client data."""
        communicator, connected = await connect_as(self.owner)
        self.assertTrue(connected)

        await push(MINE)
        message = await communicator.receive_json_from(timeout=3)
        self.assertNotIn('owner_id', message)
        await communicator.disconnect()

    async def test_a_stale_publisher_fails_closed(self):
        """mqtt_bridge does not autoreload, so during a deploy it can still be
        sending events without owner_id. A participant must get nothing rather
        than everything — the wrong direction here is a silent leak."""
        channel_layer = get_channel_layer()
        communicator, connected = await connect_as(self.owner)
        self.assertTrue(connected)

        await channel_layer.group_send(
            'devices',
            {'type': 'device.update', 'device': {'device_id': MINE}},   # no owner_id
        )
        self.assertTrue(await communicator.receive_nothing(timeout=1))
        await communicator.disconnect()
