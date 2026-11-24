"""One locked read/modify/atomic-save transaction for the local JSON document."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from eventlog import journal_changes, save_failure_metadata

STORAGE_LOCK = RLock()
DEFAULT_PATH = Path(__file__).with_name('data.json')


def load_data(path=DEFAULT_PATH):
    """Read the document, migrating legacy lists and rejected products in memory."""
    with STORAGE_LOCK:
        try:
            with Path(path).open(encoding='utf-8') as source:
                data = json.load(source)
        except FileNotFoundError:
            data = []
        if isinstance(data, list):
            data = {'products': data, 'reservations': []}
        data.setdefault('users', [])
        for key in ('termination_requests', 'termination_events',
                    'notifications', 'moderation_events', 'operation_logs', 'inbox'):
            data.setdefault(key, [])
        data.setdefault('reviews', [])
        data.setdefault('booking_requests', [])
        for product in data['products']:
            product.setdefault('version', 1)
            product.setdefault('violation_active', product['status'] == 'violation')
            product.setdefault('category', '其他')
            product.setdefault('delivery_location', '')
            product.setdefault('published_at', '')
            product.setdefault('images', [])
            product.setdefault('cover', '')
            if product['status'] == 'rejected':
                product['status'] = 'draft'
        return data


def load_products(path=DEFAULT_PATH):
    """Retain the original read interface for callers and legacy tests."""
    return load_data(path)['products']


def _save_data(data, path):
    """Flush a sibling temporary file and atomically replace the entire document."""
    path = Path(path)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent, delete=False) as target:
            temporary_path = Path(target.name)
            json.dump(data, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def transaction(path=DEFAULT_PATH):
    """Hold the same lock through read, validation, mutation and atomic save.

    Exceptions (including Flask abort) skip saving, so failed actions change nothing.
    """
    with STORAGE_LOCK:
        data = load_data(path)
        original = deepcopy(data)
        yield data
        if data != original:
            journal_changes(data, original)
            try:
                _save_data(data, path)
            except OSError:
                save_failure_metadata(original, path, data, _save_data)
                raise
