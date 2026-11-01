"""Who may do what: staff accounts, roles and sessions.

Everyone who opens the pages logs in with a username and a password and gets a
role, and the role decides what the sidebar offers and what a request may do:

- an SDR sees their own queue and the leads on it, and nothing else; their
  account is bound to their HubSpot owner id, so the queue is theirs by
  identity, not by URL;
- the sales manager sees the overview, every queue, the held and overdue
  list and the decisions, runs the jobs, and signs a model off for sales;
- marketing sees the model page and signs off for marketing, the flagged
  leads (and resolves them), the leadership page, the next phases and the
  decisions;
- leadership sees the leadership page, sets the capacity inputs, throws the
  phase switches, and reads the overview, the model page and the decisions;
- an admin may do all of it and adds accounts (`run.py staff`).

A write records who made it from the session, never from a typed name: the
approver on a sign-off, who released a held lead, who threw a switch.

Passwords are hashed with scrypt; five wrong passwords lock an account for
fifteen minutes. A session is a random 256-bit token in an HttpOnly cookie,
of which the database keeps only a hash, so a copy of the file opens no
session; it ends after twelve idle hours or seven days. The HTTP side (the
cookie, the middleware that turns a request into a principal) is in
service/security.py; this module is the part that would stay if the pages
moved behind a company's single sign-on, with the role read from it instead.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

PERMISSIONS = frozenset({
    "view_overview", "view_any_queue", "view_own_queue", "act_on_leads", "view_held", "release_held", "view_decisions",
    "view_leadership", "set_capacity", "view_model", "approve_marketing", "approve_sales", "view_phases", "throw_switches",
    "run_jobs", "manage_staff", "view_flags", "resolve_flags",
})
ROLES: dict[str, frozenset[str]] = {
    "sdr": frozenset({"view_own_queue", "act_on_leads"}),
    "manager": frozenset({"view_overview", "view_any_queue", "act_on_leads", "view_held", "release_held", "view_decisions",
                          "view_leadership", "set_capacity", "view_model", "approve_sales", "view_phases", "run_jobs", "view_flags"}),
    "marketing": frozenset({"view_decisions", "view_leadership", "view_model", "approve_marketing", "view_phases", "throw_switches",
                            "view_flags", "resolve_flags"}),
    "leadership": frozenset({"view_overview", "view_decisions", "view_leadership", "set_capacity", "view_model", "view_phases",
                             "throw_switches", "view_flags"}),
    "admin": PERMISSIONS,
}
ROLE_WORDS = {"sdr": "SDR", "manager": "Sales manager", "marketing": "Marketing", "leadership": "Leadership", "admin": "Admin"}
IDLE_SECONDS = 12 * 3600
MAX_SECONDS = 7 * 24 * 3600
LOCK_AFTER, LOCK_SECONDS = 5, 15 * 60
MIN_PASSWORD = 8
DEMO_PASSWORD = "brightpath"            # every account `seed_demo_accounts` makes, on the mock portal only


class AuthError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    username: str
    name: str
    role: str
    owner_id: Optional[str] = None      # an SDR's HubSpot owner id

    def can(self, permission: str) -> bool:
        return permission in ROLES.get(self.role, frozenset())

    @property
    def role_word(self) -> str:
        return ROLE_WORDS.get(self.role, self.role)

    @property
    def home(self) -> str:
        """The page a role lands on after logging in, and where `/` sends it."""
        if self.role == "sdr" and self.owner_id:
            return f"/queue/{self.owner_id}"
        if self.can("view_overview"):
            return "/"
        if self.can("view_model"):
            return "/model"
        return "/leadership"

    def owns(self, owner_id: Optional[str]) -> bool:
        """Whether a queue, or a lead on it, is this person's to see and act on."""
        return self.can("view_any_queue") or (self.role == "sdr" and self.owner_id is not None and self.owner_id == owner_id)


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        want = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=len(want))
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError):
        return False


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"a password needs at least {MIN_PASSWORD} characters")


def normalise_username(username: str) -> str:
    u = username.strip().lower()
    if not u or len(u) > 40 or not all(ch.isalnum() or ch in "._-" for ch in u):
        raise AuthError("a username is 1 to 40 letters, digits, dots, dashes or underscores")
    return u


# ---------------------------------------------------------------- accounts
def create_staff(conn: sqlite3.Connection, username: str, password: str, role: str, name: str = "",
                 owner_id: Optional[str] = None) -> str:
    u = normalise_username(username)
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
    if role == "sdr" and not owner_id:
        raise AuthError("an SDR account needs the HubSpot owner id whose queue it opens")
    check_password(password)
    if conn.execute("SELECT 1 FROM staff WHERE username=?", (u,)).fetchone():
        raise AuthError(f"{u} already has an account")
    conn.execute("INSERT INTO staff(username, name, role, owner_id, pw_hash, created_at) VALUES(?,?,?,?,?,?)",
                 (u, name.strip() or u, role, owner_id if role == "sdr" else None, hash_password(password), time.time()))
    return u


def set_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    check_password(password)
    u = normalise_username(username)
    if conn.execute("UPDATE staff SET pw_hash=?, failed=0, locked_until=NULL WHERE username=?", (hash_password(password), u)).rowcount == 0:
        raise AuthError(f"no account {u}")
    end_sessions_for(conn, u)


def set_disabled(conn: sqlite3.Connection, username: str, disabled: bool) -> None:
    u = normalise_username(username)
    if conn.execute("UPDATE staff SET disabled=? WHERE username=?", (1 if disabled else 0, u)).rowcount == 0:
        raise AuthError(f"no account {u}")
    if disabled:
        end_sessions_for(conn, u)


def list_staff(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT username, name, role, owner_id, disabled, failed, locked_until, created_at FROM staff ORDER BY role, username")]


def staff_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) n FROM staff").fetchone()["n"]


def bootstrap_from_env(conn: sqlite3.Connection, env: Optional[dict] = None) -> bool:
    """The first admin, from AUXO_ADMIN_USER and AUXO_ADMIN_PASSWORD, when no account exists yet."""
    env = os.environ if env is None else env
    user, password = (env.get("AUXO_ADMIN_USER") or "").strip(), env.get("AUXO_ADMIN_PASSWORD") or ""
    if not user or not password or staff_count(conn):
        return False
    create_staff(conn, user, password, "admin", name=user)
    return True


def seed_demo_accounts(conn: sqlite3.Connection, owners) -> list[str]:
    """The mock portal's logins, one an SDR (sdr1..sdrN, bound to their owner) and one each for the sales manager,
    marketing, leadership and the admin, all with DEMO_PASSWORD. Accounts that exist are left as they are."""
    made = []
    people = [(o.email.split("@")[0] if o.email else f"sdr{o.id}", DEMO_PASSWORD, "sdr", o.name, o.id) for o in owners]
    people += [("manager", DEMO_PASSWORD, "manager", "Sam Okafor", None), ("marketing", DEMO_PASSWORD, "marketing", "Nadia Reyes", None),
               ("leadership", DEMO_PASSWORD, "leadership", "Dana Whitfield", None), ("admin", DEMO_PASSWORD, "admin", "Admin", None)]
    for username, password, role, name, owner_id in people:
        try:
            made.append(create_staff(conn, username, password, role, name=name, owner_id=owner_id))
        except AuthError:
            pass                                    # already there
    conn.execute("INSERT INTO settings(key, value) VALUES('demo_logins', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
    return made


def demo_logins(conn: sqlite3.Connection) -> list[dict]:
    """The demo accounts for the login page of a mock portal that was seeded with them; [] otherwise."""
    row = conn.execute("SELECT value FROM settings WHERE key='demo_logins'").fetchone()
    if not row or row["value"] != "1":
        return []
    return [dict(r) for r in conn.execute("SELECT username, name, role, owner_id FROM staff WHERE disabled=0 ORDER BY "
                                          "CASE role WHEN 'sdr' THEN 0 WHEN 'manager' THEN 1 WHEN 'marketing' THEN 2 WHEN 'leadership' THEN 3 ELSE 4 END, username")]


# ---------------------------------------------------------------- logging in
def authenticate(conn: sqlite3.Connection, username: str, password: str) -> Optional[Principal]:
    """The principal for a right username and password; None for a wrong pair, a disabled account or a locked one.
    A wrong password counts against the account; the fifth locks it for LOCK_SECONDS."""
    try:
        u = normalise_username(username)
    except AuthError:
        return None
    row = conn.execute("SELECT * FROM staff WHERE username=?", (u,)).fetchone()
    now = time.time()
    if row is None:
        verify_password(password, hash_password("x" * MIN_PASSWORD))      # the same time whether or not the account exists
        return None
    if row["disabled"] or (row["locked_until"] and row["locked_until"] > now):
        return None
    if not verify_password(password, row["pw_hash"]):
        failed = (row["failed"] or 0) + 1
        locked = now + LOCK_SECONDS if failed >= LOCK_AFTER else None
        conn.execute("UPDATE staff SET failed=?, locked_until=? WHERE username=?", (0 if locked else failed, locked, u))
        return None
    conn.execute("UPDATE staff SET failed=0, locked_until=NULL WHERE username=?", (u,))
    return Principal(username=u, name=row["name"], role=row["role"], owner_id=row["owner_id"])


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(conn: sqlite3.Connection, p: Principal) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute("DELETE FROM sessions WHERE last_seen < ? OR created_at < ?", (now - IDLE_SECONDS, now - MAX_SECONDS))
    conn.execute("INSERT INTO sessions(token_hash, username, created_at, last_seen) VALUES(?,?,?,?)", (_token_hash(token), p.username, now, now))
    return token


def load_session(conn: sqlite3.Connection, token: Optional[str]) -> Optional[Principal]:
    """The principal a session token stands for, with the account as it is now (a role change or a disabling takes
    effect on the next request); None for no, an unknown or a run-out token."""
    if not token:
        return None
    now = time.time()
    row = conn.execute("SELECT s.created_at, s.last_seen, t.username, t.name, t.role, t.owner_id, t.disabled FROM sessions s "
                       "JOIN staff t ON t.username = s.username WHERE s.token_hash=?", (_token_hash(token),)).fetchone()
    if row is None:
        return None
    if row["disabled"] or row["last_seen"] < now - IDLE_SECONDS or row["created_at"] < now - MAX_SECONDS:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
        return None
    if now - row["last_seen"] > 60:
        conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, _token_hash(token)))
    return Principal(username=row["username"], name=row["name"], role=row["role"], owner_id=row["owner_id"])


def end_session(conn: sqlite3.Connection, token: Optional[str]) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def end_sessions_for(conn: sqlite3.Connection, username: str) -> None:
    conn.execute("DELETE FROM sessions WHERE username=?", (username,))
