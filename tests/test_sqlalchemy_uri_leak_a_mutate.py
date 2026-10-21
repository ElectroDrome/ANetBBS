"""Half A of a regression pair for the TestingConfig.SQLALCHEMY_DATABASE_URI
leak fix (tests/conftest.py's `_restore_testing_db_uri_after_module`
autouse fixture).

~25 test files swap TestingConfig.SQLALCHEMY_DATABASE_URI for a
temp-file path in setUpClass and restore it in tearDownClass -- a
pattern that leaks the temp path into every later test MODULE's
default whenever a given file's tearDownClass forgets the restore
step. That leak is invisible running any single file alone and only
shows up deep into a full-suite run, in whichever later module's
setUpClass happens to capture the already-poisoned value.

This module plays the forgetful file: it mutates the class attribute
in setUpClass and deliberately never restores it (no tearDownClass at
all). Collection order matters and is intentional -- pytest collects
test files alphabetically by default, and this file's name is chosen
to sort before test_sqlalchemy_uri_leak_b_check_restored.py, which
asserts the leak was cleaned up between the two modules.

Two test methods, not one, on purpose: this also guards against a
regression in the OTHER direction -- an overly aggressive fix that
resets the URI *between* test methods of the same class would break
the real convention several call sites in this codebase rely on
(features/bbs_ui.py's `_app()` re-reads the URI and caches a Flask app
keyed on it, expecting it to stay put for a whole class's run). Both
methods here must see the SAME mutated value; only test_b's module (a
different module) is required to see it restored.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

_LEAK_URI = 'sqlite:////tmp/this-should-never-leak-across-modules.db'


class SqlalchemyUriLeakMutateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = _LEAK_URI
        # Deliberately no tearDownClass -- simulates the real bug shape.

    def test_1_sees_the_mutated_value(self):
        self.assertEqual(
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI, _LEAK_URI)

    def test_2_same_class_still_sees_it_unchanged(self):
        # If the fix incorrectly reset the attribute BETWEEN test
        # methods of the same class, this would fail -- see module
        # docstring.
        self.assertEqual(
            cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI, _LEAK_URI)


if __name__ == '__main__':
    unittest.main()
