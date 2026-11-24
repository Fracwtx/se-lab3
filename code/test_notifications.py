"""Outbox failures, restart/retry, deduplication and notification object authorization."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
import subprocess
import sys
from threading import Event
from time import monotonic
from unittest.mock import patch

from flask.testing import FlaskClient

from notification_worker import NotificationWorker

from app import create_app

from notifications import deliver_one, deliver_pending
from storage import load_data, transaction
from test_app import MarketCase


class NotificationTest(MarketCase):
    """Business commits survive failed delivery; each outbox item has one inbox item."""

    def test_failure_retry_duplicate_delivery_and_read_access(self):
        """Retry after delivery failure yields one message, without replaying the booking."""
        product_id = self.publish()
        self.app.config['NOTIFICATION_TRANSPORT'] = lambda _item: (_ for _ in ()).throw(
            RuntimeError('PRIVATE_ERROR_MUST_NOT_BE_SAVED'))
        self.login('buyer', 'buyer123')
        self.assertEqual(self.reserve(product_id).status_code, 302)
        data = load_data(self.path)
        self.assertEqual(data['products'][0]['status'], 'reserved')
        pending = [item for item in data['notifications'] if item['status'] == 'retry']
        self.assertEqual(len(pending), 2)
        self.assertNotIn('PRIVATE_ERROR', self.path.read_text())
        original_business = self.business_bytes()
        self.app.config['NOTIFICATION_TRANSPORT'] = None
        restarted = create_app(self.path, notification_worker=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _index: deliver_pending(restarted), range(2)))
        deliver_one(restarted, pending[0]['id'])
        data = load_data(self.path)
        for item in pending:
            self.assertEqual(sum(entry['event_key'] == item['id'] for entry in data['inbox']), 1)
        self.assertEqual(self.business_bytes(), original_business)
        mine = next(item for item in data['inbox'] if item['recipient'] == 'buyer')
        other = next(item for item in data['inbox'] if item['recipient'] == 'user')
        self.assertEqual(self.client.get('/notifications/' + other['id']).status_code, 403)
        self.assertEqual(self.client.post('/notifications/' + other['id']).status_code, 403)
        self.assertEqual(self.client.post('/notifications/retry').status_code, 403)
        self.assertEqual(self.client.post('/notifications/' + mine['id']).status_code, 302)
        read_at = next(item for item in load_data(self.path)['inbox']
                       if item['id'] == mine['id'])['read_at']
        self.client.post('/notifications/' + mine['id'])
        self.assertEqual(next(item for item in load_data(self.path)['inbox']
                              if item['id'] == mine['id'])['read_at'], read_at)
        self.assertNotIn(other['id'], self.client.get('/notifications').get_data(as_text=True))

    def test_delivery_save_failure_rolls_back_inbox_and_ack(self):
        """A failed inbox save leaves a durable retry record rather than a phantom sent flag."""
        self.publish()
        with transaction(self.path) as data:
            data['notifications'].append({'id': 'injected-event', 'recipient': 'buyer',
                                          'event': 'reservation_confirmed', 'status': 'pending'})
        original = self.path.read_bytes()
        with patch('storage.os.replace', side_effect=OSError('disk unavailable')):
            deliver_pending(self.app)
        self.assertEqual(self.path.read_bytes(), original)
        deliver_pending(self.app)
        self.assertEqual(sum(item['event_key'] == 'injected-event'
                             for item in load_data(self.path)['inbox']), 1)

    def test_existing_outbox_and_inbox_are_reconciled_without_duplicates(self):
        """A legacy pending record whose message already exists is acknowledged once."""
        self.publish()
        with transaction(self.path) as data:
            message = data['inbox'][0]
            event_key = message['event_key']
            entry = next(item for item in data['notifications'] if item['id'] == event_key)
            entry['status'] = 'retry'
        before = len(load_data(self.path)['inbox'])
        deliver_pending(self.app)
        self.assertEqual(len(load_data(self.path)['inbox']), before)


class NotificationWorkerTest(MarketCase):
    """Slow delivery must never block business responses or hold the shared JSON lock."""

    def test_slow_delivery_does_not_delay_business_response(self):
        """Exercise the production hook using an ordinary client, without fixture draining."""
        entered, release = Event(), Event()
        self.app.config['NOTIFICATION_WORKER'] = True
        client = FlaskClient(self.app, response_wrapper=self.app.response_class)
        client.post('/login', data={'username': 'user', 'password': 'user123'})
        with transaction(self.path) as data:
            data['notifications'].append({'id': 'slow-event', 'recipient': 'user',
                                          'event': 'approved', 'status': 'pending'})

        def slow_transport(_item):
            entered.set()
            release.wait(5)

        self.app.config['NOTIFICATION_TRANSPORT'] = slow_transport
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                response = pool.submit(client.post, '/submit', data={
                    'action': 'save', 'title': '后台投递不阻塞草稿保存'})
                try:
                    self.assertTrue(entered.wait(2), 'delivery was never attempted')
                    try:
                        result = response.result(timeout=0.5)
                    except FutureTimeout:
                        self.fail('business response waited for notification transport')
                    self.assertEqual(result.status_code, 302)
                    self.assertEqual(load_data(self.path)['products'][0]['status'], 'draft')
                finally:
                    release.set()
        finally:
            worker = self.app.extensions.get('notification_worker')
            if worker:
                worker.stop()

    def test_worker_recovers_retry_after_process_restart_without_http(self):
        """A new process discovers durable retries and acknowledges an existing message once."""
        self.publish()
        with transaction(self.path) as data:
            data['notifications'].append({'id': 'restart-event', 'recipient': 'buyer',
                                          'event': 'approved', 'status': 'pending'})
        self.app.config['NOTIFICATION_TRANSPORT'] = lambda _item: (_ for _ in ()).throw(
            RuntimeError('offline'))
        worker = NotificationWorker(self.app, deliver_pending, interval=0.02)
        worker.start()
        try:
            deadline = monotonic() + 2
            while monotonic() < deadline:
                item = next(entry for entry in load_data(self.path)['notifications']
                            if entry['id'] == 'restart-event')
                if item['status'] == 'retry':
                    break
                Event().wait(0.01)
            self.assertEqual(item['status'], 'retry')
        finally:
            worker.stop()
        script = (
            "import sys,time; from app import create_app; from storage import load_data; "
            "app=create_app(sys.argv[1]); deadline=time.monotonic()+3; "
            "\nwhile time.monotonic()<deadline:\n"
            " data=load_data(sys.argv[1]); "
            "item=next(e for e in data['notifications'] if e['id']=='restart-event');\n"
            " if item['status']=='sent': break\n"
            " time.sleep(.01)\n"
            "app.extensions['notification_worker'].stop(); "
            "assert item['status']=='sent'; "
            "assert sum(e['event_key']=='restart-event' for e in data['inbox'])==1"
        )
        for repeat in range(2):
            if repeat:
                with transaction(self.path) as data:
                    next(entry for entry in data['notifications']
                         if entry['id'] == 'restart-event')['status'] = 'retry'
            result = subprocess.run([sys.executable, '-c', script, str(self.path)],
                                    capture_output=True, text=True, check=False, timeout=6,
                                    cwd=Path(__file__).parent)
            self.assertEqual(result.returncode, 0, result.stderr)
