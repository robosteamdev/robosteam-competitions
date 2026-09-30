"""Watch a competition board WebSocket and log every push.

Phase H: proves the Redis channel layer really is cross-process — this client
talks only to the Daphne/runserver process, while the pushes it receives are
emitted by the mqtt_bridge and timeout_runs processes.

    python ws_watch.py <competition_id> <seconds>
"""
import asyncio
import sys
from datetime import datetime

import websockets

COMPETITION = int(sys.argv[1]) if len(sys.argv) > 1 else 4
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
URL = f'ws://127.0.0.1:8000/ws/competition/{COMPETITION}/'


def stamp():
    return datetime.now().strftime('%H:%M:%S.%f')[:-3]


async def main():
    received = 0
    async with websockets.connect(URL) as ws:
        print(f'[{stamp()}] connected to {URL}, listening {SECONDS}s', flush=True)
        deadline = asyncio.get_event_loop().time() + SECONDS
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            received += 1
            print(f'[{stamp()}] PUSH #{received}: {message}', flush=True)

    print(f'[{stamp()}] done — {received} push(es) received', flush=True)
    return received


print('RESULT:', 'PASS' if asyncio.run(main()) else 'FAIL — no pushes')
