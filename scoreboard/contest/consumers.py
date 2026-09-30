import json

from channels.generic.websocket import AsyncWebsocketConsumer


class CompetitionConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for a single competition board.
    Clients connect to ws://.../ws/competition/<id>/
    and receive a JSON ping whenever results change.
    """

    async def connect(self):
        self.competition_id = self.scope['url_route']['kwargs']['competition_id']
        self.group_name = f'competition_{self.competition_id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        pass  # clients are receive-only; server pushes all updates

    async def competition_update(self, event):
        """Handler for group_send messages of type 'competition.update'."""
        await self.send(text_data=json.dumps({
            'competition_id': event['competition_id'],
        }))
