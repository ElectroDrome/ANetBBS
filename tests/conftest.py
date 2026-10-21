# tests/conftest.py
"""
Shared pytest fixtures for the whole tests/ tree.

anetbbs/features/rate_limit.py's `_buckets` is a process-global,
in-memory sliding-window dict (by design -- see its own docstring:
"Single-process only"), never reset between test files. Any test that
POSTs to /auth/login (many do, across many different test files/
classes) shares ONE cumulative counter for the whole pytest process,
regardless of which test DB/app instance each test class uses. A full
`pytest tests/` run can then trip a REAL 429 partway through -- not a
bug in the app, but a real test-isolation gap: whichever test happens
to be the one that pushes the shared bucket over "10 attempts / 5 min"
fails with a 429 where it expected a 200, and WHICH test that is
depends on collection order/count, so it can pass locally and fail in
CI (or vice versa) for no code reason at all. Confirmed by reproducing
it directly: every individual test file passed in isolation, but a
full-suite run deterministically failed on the same handful of
files/tests until this fixture was added.

Autouse + function-scoped so it runs before/after every single test,
including unittest.TestCase-style tests (pytest applies autouse
fixtures to those too) -- the safest, simplest fix rather than trying
to track down and coordinate every individual test file that happens
to touch /auth/login.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    from anetbbs.features import rate_limit
    rate_limit._buckets.clear()
    yield
    rate_limit._buckets.clear()


@pytest.fixture(scope='session')
def _pristine_testing_db_uri():
    """Captures TestingConfig.SQLALCHEMY_DATABASE_URI before any test
    class has had a chance to mutate it.

    Session scope means this fixture's setup runs before any narrower
    (module/class/function) fixture's setup for the very first test
    collected -- including unittest.TestCase's setUpClass, which pytest
    treats as its own class-scoped fixture -- so the value captured
    here is guaranteed to be the real, never-mutated default.
    """
    import anetbbs.config as cfg_mod
    return cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI


@pytest.fixture(autouse=True, scope='module')
def _restore_testing_db_uri_after_module(_pristine_testing_db_uri):
    """TestingConfig.SQLALCHEMY_DATABASE_URI is a shared, mutable class
    attribute. ~25 test files swap it for a temp-file path in
    setUpClass and restore it in tearDownClass -- a pattern that works
    in isolation but leaks the temp path into every later test file's
    default whenever a given file's tearDownClass forgets the restore
    step (or raises before reaching it). That leak is invisible running
    any single file alone and only shows up as a confusing failure deep
    into a full-suite run, in whichever later file's setUpClass happens
    to capture the already-poisoned value as "original".

    Module-scoped, NOT function-scoped: several call sites (e.g.
    features/bbs_ui.py's `_app()`) deliberately re-read
    TestingConfig.SQLALCHEMY_DATABASE_URI on every call and cache a
    Flask app keyed on its current value, precisely so a test file can
    repoint the DB URI once in setUpClass and have every test method in
    that class/module keep using it. A function-scoped reset defeated
    that -- confirmed by reproducing it directly: resetting between
    individual test methods within the same class made `_app()`'s cache
    key change mid-class, so it silently rebuilt a brand-new (tableless)
    in-memory db for the second test method onward ("no such table:
    users"), a regression across 40+ previously-passing tests. Module
    scope's teardown fires once, after the LAST test in a module
    finishes (confirmed empirically: it runs after that module's last
    class's tearDownClass, and before the next module's setUpClass) --
    late enough to never interfere with any class/module-internal
    reuse of the URI, but still early enough that a forgetful
    tearDownClass can never leak past its own file's test run. This
    does not affect already-created Flask apps either way -- resetting
    the class attribute doesn't touch an already-bound SQLAlchemy
    engine, only what a *later* create_app()/`_app()` call would read.
    """
    yield
    import anetbbs.config as cfg_mod
    cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = _pristine_testing_db_uri
