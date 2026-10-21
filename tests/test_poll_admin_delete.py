"""Regression test: there was no way to remove a poll at all -- only
close() (admin or creator) existed. A poll created in poor taste stayed
visible forever. delete() is admin-only (unlike close(), which also
allows the poll's own creator) -- PollOption.votes and Poll.options are
both already cascade='all, delete-orphan' (models.py), so deleting a
poll cleanly removes every option and vote with it.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class PollAdminDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.poll_admin_delete_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='polldeladmin', email='polldeladmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            creator = User(username='polldelcreator', email='polldelcreator@example.com',
                           password_hash='x', access_level=10)
            voter = User(username='polldelvoter', email='polldelvoter@example.com',
                        password_hash='x', access_level=10)
            db.session.add_all([admin, creator, voter])
            db.session.commit()
            cls.admin_id = admin.id
            cls.creator_id = creator.id
            cls.voter_id = voter.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_poll_with_a_vote(self, question):
        from anetbbs.models import db, Poll, PollOption, PollVote
        with self.app.app_context():
            p = Poll(creator_id=self.creator_id, question=question, is_active=True)
            db.session.add(p)
            db.session.flush()
            opt_a = PollOption(poll_id=p.id, text='A', sort_order=0)
            opt_b = PollOption(poll_id=p.id, text='B', sort_order=1)
            db.session.add_all([opt_a, opt_b])
            db.session.flush()
            db.session.add(PollVote(poll_id=p.id, option_id=opt_a.id, user_id=self.voter_id))
            db.session.commit()
            return p.id, opt_a.id, opt_b.id

    def test_admin_can_delete_a_poll_and_its_votes_and_options_cascade(self):
        poll_id, opt_a_id, opt_b_id = self._make_poll_with_a_vote('Delete me (admin)')
        resp = self._client_as(self.admin_id).post(
            f'/polls/{poll_id}/delete', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import Poll, PollOption, PollVote
            self.assertIsNone(Poll.query.get(poll_id))
            self.assertIsNone(PollOption.query.get(opt_a_id))
            self.assertIsNone(PollOption.query.get(opt_b_id))
            self.assertEqual(PollVote.query.filter_by(poll_id=poll_id).count(), 0)

    def test_non_admin_creator_cannot_delete_their_own_poll(self):
        """Delete is admin-only, unlike close() -- a poll's own creator
        can still close it but not remove it outright."""
        poll_id, _, _ = self._make_poll_with_a_vote('Creator cannot delete')
        resp = self._client_as(self.creator_id).post(
            f'/polls/{poll_id}/delete')
        self.assertEqual(resp.status_code, 403)
        with self.app.app_context():
            from anetbbs.models import Poll
            self.assertIsNotNone(Poll.query.get(poll_id),
                                 'a non-admin creator must not be able to delete the poll')

    def test_ordinary_user_cannot_delete_a_poll(self):
        poll_id, _, _ = self._make_poll_with_a_vote('Voter cannot delete')
        resp = self._client_as(self.voter_id).post(
            f'/polls/{poll_id}/delete')
        self.assertEqual(resp.status_code, 403)
        with self.app.app_context():
            from anetbbs.models import Poll
            self.assertIsNotNone(Poll.query.get(poll_id))


if __name__ == '__main__':
    unittest.main()
