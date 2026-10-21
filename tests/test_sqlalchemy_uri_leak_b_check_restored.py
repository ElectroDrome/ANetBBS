"""Half B of the TestingConfig.SQLALCHEMY_DATABASE_URI leak-fix
regression pair -- see test_sqlalchemy_uri_leak_a_mutate.py for the
full explanation. This module runs (alphabetically) after that one and
relies ONLY on the conftest.py autouse fixture -- no cleanup of its
own -- to see the pristine default rather than the leaked value.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class SqlalchemyUriLeakRestoredTests(unittest.TestCase):
    def test_leak_was_cleaned_up_before_this_module_ran(self):
        self.assertEqual(
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI,
            'sqlite:///:memory:',
            "TestingConfig.SQLALCHEMY_DATABASE_URI leaked across "
            "modules -- the conftest.py "
            "_restore_testing_db_uri_after_module autouse fixture is "
            "missing or broken")


if __name__ == '__main__':
    unittest.main()
