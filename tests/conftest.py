"""Every page needs a login (src/auxocase/auth.py), so a test client logs in before a test uses it.

The fixture below wraps TestClient: a client for one of the service's apps gets an admin account in that app's
database (made the first time) and logs in with it, so a test reads and writes as an admin unless it logs in as
someone else with `log_in`. A client whose host the service refuses (test_security's evil.example) stays logged
out, which is what those tests want.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from auxocase import auth

TEST_ADMIN = ("test-admin", "test-admin-pw", "Test Admin")


def log_in(client: TestClient, username: str, password: str) -> None:
    """Log the client in as this account (the admin's session ends); raises if the pair is refused."""
    r = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303, f"login as {username} refused: {r.status_code}"


def ensure_account(conn, username: str, password: str, role: str, name: str = "", owner_id=None) -> None:
    try:
        auth.create_staff(conn, username, password, role, name=name or username, owner_id=owner_id)
    except auth.AuthError:
        pass                                                       # already there


@pytest.fixture(autouse=True, scope="session")
def _clients_log_in():
    original = TestClient.__init__

    def __init__(self, app, *args, **kwargs):
        original(self, app, *args, **kwargs)
        st = getattr(getattr(app, "state", None), "st", None)
        if st is None:
            return
        username, password, name = TEST_ADMIN
        ensure_account(st.conn, username, password, "admin", name)
        try:
            self.post("/login", data={"username": username, "password": password}, follow_redirects=False)
        except Exception:                                          # a refused host, say: the test wants it that way
            pass

    TestClient.__init__ = __init__
    yield
    TestClient.__init__ = original
