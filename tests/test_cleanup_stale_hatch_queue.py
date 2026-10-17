"""Regression tests for the new cleanup_stale_hatch_queue scheduled
handler (anetbbs/events/handlers.py) -- the backstop half of a Medium
fix from a follow-up security audit. binkp_server.py's inbound WaZOO
FREQ handling now refuses to queue new HatchQueue rows for an
anonymous/unauthenticated session (see that module's own fix), but a
row queued before that fix -- or any row whose peer_address simply
doesn't match any currently-configured peer -- would otherwise sit
'pending' forever, since the poller/binkp client only ever match
peer_address against a real peer's own configured address by exact
string. This handler marks such orphaned rows 'failed' once they've
sat pending past stale_days.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class CleanupStaleHatchQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.cleanup_hatch_queue_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            from anetbbs.models import db, FileArea
            db.create_all()
            area = FileArea(tag='TEST.AREA', name='Test Area')
            db.session.add(area)
            db.session.commit()
            cls.area_id = area.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _add_row(self, peer_address, status, days_old):
        from anetbbs.models import db, HatchQueue
        row = HatchQueue(
            file_area_id=self.area_id,
            peer_address=peer_address,
            binary_path='/tmp/nonexistent.zip',
            filename='nonexistent.zip',
            status=status,
            queued_at=datetime.utcnow() - timedelta(days=days_old),
        )
        db.session.add(row)
        db.session.commit()
        return row.id

    def test_marks_old_unmatched_pending_row_failed(self):
        from anetbbs.events.handlers import cleanup_stale_hatch_queue
        with self.app.app_context():
            row_id = self._add_row('9:999/999', 'pending', 10)
            ok, msg = cleanup_stale_hatch_queue(self.app, {'stale_days': 7})
            self.assertTrue(ok, msg)
            from anetbbs.models import HatchQueue
            row = HatchQueue.query.get(row_id)
            self.assertEqual(row.status, 'failed')
            self.assertIn('no configured', row.error_message)

    def test_keeps_recent_unmatched_pending_row(self):
        from anetbbs.events.handlers import cleanup_stale_hatch_queue
        with self.app.app_context():
            row_id = self._add_row('9:999/998', 'pending', 1)
            cleanup_stale_hatch_queue(self.app, {'stale_days': 7})
            from anetbbs.models import HatchQueue
            row = HatchQueue.query.get(row_id)
            self.assertEqual(row.status, 'pending',
                              'a row younger than stale_days must be left alone')

    def test_keeps_old_row_matching_a_real_configured_network_hub(self):
        from anetbbs.events.handlers import cleanup_stale_hatch_queue
        from anetbbs.models import db, EchomailNetwork
        with self.app.app_context():
            net = EchomailNetwork(name='RealNet', network_type='binkp',
                                  hub_address='1:234/567')
            db.session.add(net)
            db.session.commit()
            row_id = self._add_row('1:234/567', 'pending', 10)
            cleanup_stale_hatch_queue(self.app, {'stale_days': 7})
            from anetbbs.models import HatchQueue
            row = HatchQueue.query.get(row_id)
            self.assertEqual(row.status, 'pending',
                              'a row matching a real configured peer must '
                              'never be auto-failed')

    def test_keeps_old_row_matching_a_real_configured_binkp_node(self):
        from anetbbs.events.handlers import cleanup_stale_hatch_queue
        from anetbbs.models import db, BinkPNode
        with self.app.app_context():
            node = BinkPNode(name='RealNode', ftn_address='1:234/568',
                             password='x')
            db.session.add(node)
            db.session.commit()
            row_id = self._add_row('1:234/568', 'pending', 10)
            cleanup_stale_hatch_queue(self.app, {'stale_days': 7})
            from anetbbs.models import HatchQueue
            row = HatchQueue.query.get(row_id)
            self.assertEqual(row.status, 'pending')

    def test_never_touches_already_sent_or_failed_rows(self):
        from anetbbs.events.handlers import cleanup_stale_hatch_queue
        with self.app.app_context():
            sent_id = self._add_row('9:999/997', 'sent', 30)
            failed_id = self._add_row('9:999/996', 'failed', 30)
            cleanup_stale_hatch_queue(self.app, {'stale_days': 7})
            from anetbbs.models import HatchQueue
            self.assertEqual(HatchQueue.query.get(sent_id).status, 'sent')
            self.assertEqual(HatchQueue.query.get(failed_id).status, 'failed')


if __name__ == '__main__':
    unittest.main()
