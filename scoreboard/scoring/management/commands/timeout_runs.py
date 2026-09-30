import asyncio
import signal

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

from devices.services import sweep_offline_devices
from scoring.engine import check_and_void_timed_out_runs


class Command(BaseCommand):
    help = (
        'Periodically void ACTIVE TIMED runs that exceed their timeout_seconds, '
        'and mark devices OFFLINE when their heartbeat goes stale. '
        'Run alongside the server: python manage.py timeout_runs'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval', type=int, default=10,
            help='Seconds between checks (default: 10).',
        )
        parser.add_argument(
            '--once', action='store_true',
            help='Run one check and exit (useful for testing and cron).',
        )

    def handle(self, *args, **options):
        interval = options['interval']
        once = options['once']

        if once:
            voided = check_and_void_timed_out_runs()
            offline = sweep_offline_devices()
            self.stdout.write(
                f'timeout_runs: voided {voided} run(s), marked {offline} device(s) offline.'
            )
            return

        self.stdout.write(f'timeout_runs: starting — checking every {interval}s. Ctrl-C to stop.')
        asyncio.run(self._loop(interval))

    async def _loop(self, interval):
        stop = asyncio.Event()

        def _handle_signal(*_):
            stop.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handle_signal)

        while not stop.is_set():
            voided = await sync_to_async(check_and_void_timed_out_runs)()
            if voided:
                self.stdout.write(f'timeout_runs: voided {voided} timed-out run(s).')
            offline = await sync_to_async(sweep_offline_devices)()
            if offline:
                self.stdout.write(f'timeout_runs: marked {offline} device(s) offline.')
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

        self.stdout.write('timeout_runs: stopped.')
