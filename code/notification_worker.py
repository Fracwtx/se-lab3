"""One daemon worker per application, scanning the durable JSON notification outbox."""
from threading import Event, Lock, Thread


class NotificationWorker:
    """Requests only signal work; disk-backed scans recover missed signals and restarts."""

    def __init__(self, app, deliver, interval=1.0):
        self.app = app
        self.deliver = deliver
        self.interval = interval
        self.stopped = Event()
        self.wakeup = Event()
        self.start_lock = Lock()
        self.thread = None

    def start(self):
        """Start once, including when multiple first requests arrive concurrently."""
        with self.start_lock:
            if self.thread is None:
                self.thread = Thread(target=self.run, name='market-notifications', daemon=True)
                self.thread.start()
        self.wakeup.set()

    def run(self):
        """Poll without needing an HTTP request or a process-local list of unsent ids."""
        while not self.stopped.is_set():
            self.wakeup.clear()
            try:
                self.deliver(self.app)
            except (OSError, ValueError):
                self.app.logger.error('Notification scan unavailable: storage error')
            self.wakeup.wait(self.interval)

    def stop(self):
        """Release the scanner before test/demo directories are removed or the server exits."""
        self.stopped.set()
        self.wakeup.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
