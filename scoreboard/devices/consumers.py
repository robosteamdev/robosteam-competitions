import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class DeviceConsumer(AsyncWebsocketConsumer):
    """
    Live device status for anyone watching a page that shows badges.

    Connect to ws://.../ws/devices/ and receive a JSON payload whenever a device
    heartbeats, replies to a PING or is swept offline.

    **Authenticated, and then filtered by who you are.** Everything else in the
    robot feature is owner-scoped — the claim form never lists unclaimed MACs,
    `_my_robot` 404s on someone else's device — and a socket that pushed every
    device to every logged-in user walks straight around all of it. Claiming is
    MAC-only and first-claimer-wins (Decision 042), so a MAC *is* the
    credential: a participant watching this stream could harvest the MAC of
    every robot not yet claimed, at exactly the moment before its owner claims
    it, and take it first.

    So:

    * judges, organisers and administrators see every device — they run the
      event, and the organiser console exists to show them exactly this;
    * everyone else sees only devices they own;
    * anonymous sockets are refused outright. The public board renders the
      timer's state server-side and needs no socket.
    """

    GROUP = 'devices'

    #: Roles that operate the event and legitimately see all hardware.
    STAFF_GROUPS = ['Judge', 'Organiser', 'Administrator']

    async def connect(self):
        user = self.scope.get('user')
        if user is None or not user.is_authenticated:
            await self.close()
            return
        self.user_id  = user.pk
        self.sees_all = await self._is_staff_viewer(user)
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept()

    @database_sync_to_async
    def _is_staff_viewer(self, user):
        """Resolved once at connect. Checking group membership per pushed
        message would be a database query per heartbeat per open tab."""
        return bool(
            user.is_superuser
            or user.groups.filter(name__in=self.STAFF_GROUPS).exists()
        )

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)

    async def receive(self, text_data):
        pass  # receive-only; the server pushes everything

    async def device_update(self, event):
        """Handler for group_send messages of type 'device.update'.

        `owner_id` rides on the event alongside `device`, not inside it: it is
        routing information for this method and is never sent to a browser.
        """
        if not self.sees_all and event.get('owner_id') != self.user_id:
            return
        await self.send(text_data=json.dumps(event['device']))
