from django.urls import path

from contest.consumers import CompetitionConsumer
from devices.consumers import DeviceConsumer

websocket_urlpatterns = [
    path('ws/competition/<int:competition_id>/', CompetitionConsumer.as_asgi()),
    path('ws/devices/', DeviceConsumer.as_asgi()),
]
