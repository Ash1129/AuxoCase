"""The service as an ASGI app for a host that runs it per request, such as Vercel (which finds `app` here).

The database is not in the repo. On a cold start a snapshot (the database, its model pickles and the queue-limits
study, as one tar.gz in the project's private Vercel Blob store; `scripts/vercel_deploy.sh` makes and uploads it) is
downloaded and unpacked to /tmp, and the service runs on that copy. What the pages change lives in the copy only:
a new instance starts again from the snapshot. The scheduler is off (no long-running process), Claude is reached
over the API (`AUXO_LLM=api`, reading ANTHROPIC_API_KEY), and the host's own names are the allowed hosts.

Locally: `uvicorn asgi:app` with AUXO_SNAPSHOT_URL and BLOB_READ_WRITE_TOKEN set (`vercel env pull` writes both).
"""
import os
import shutil
import sqlite3
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

WORK = Path(os.environ.get("AUXO_WORK_DIR", "/tmp/auxo"))
DB = WORK / "auxocase.db"


def _unpack() -> None:
    import httpx
    url, token = os.environ.get("AUXO_SNAPSHOT_URL", ""), os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    if not url or not token:
        raise RuntimeError("AUXO_SNAPSHOT_URL and BLOB_READ_WRITE_TOKEN must be set: run scripts/vercel_deploy.sh once")
    WORK.mkdir(parents=True, exist_ok=True)
    tgz = WORK / "snapshot.tar.gz"
    with httpx.stream("GET", url, headers={"Authorization": f"Bearer {token}"}, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(tgz, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    stage = WORK / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    with tarfile.open(tgz) as t:
        t.extractall(stage, filter="data")
    tgz.unlink()
    # The registry names each model's pickle by its path on the machine the snapshot came from: point it here.
    with sqlite3.connect(stage / "auxocase.db") as db:
        rows = db.execute("SELECT version, artifact_path FROM model_registry").fetchall()
        db.executemany("UPDATE model_registry SET artifact_path=? WHERE version=?",
                       [(str(WORK / "models" / Path(p).name), v) for v, p in rows if p])
    shutil.copytree(stage / "models", WORK / "models", dirs_exist_ok=True)
    (WORK / "outputs").mkdir(exist_ok=True)
    if (stage / "queue_limits.json").exists():
        shutil.copy(stage / "queue_limits.json", WORK / "outputs" / "queue_limits.json")
    (WORK / "docs").mkdir(exist_ok=True)
    shutil.copy(ROOT / "docs" / "evaluation.json", WORK / "docs" / "evaluation.json")
    (stage / "auxocase.db").rename(DB)             # last, so a half-unpacked database is never opened
    shutil.rmtree(stage, ignore_errors=True)


if not DB.exists():
    _unpack()
os.chdir(WORK)                                     # the leadership page reads docs/ and outputs/ from here

os.environ["AUXO_DB"] = str(DB)
os.environ.setdefault("AUXO_LLM", "api")
os.environ.setdefault("AUXO_SCHEDULER", "0")
if not os.environ.get("AUXO_ALLOWED_HOSTS"):       # this deployment's own names, as Vercel gives them
    names = [os.environ.get(k, "") for k in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL")]
    os.environ["AUXO_ALLOWED_HOSTS"] = ",".join(n for n in names + ["localhost", "127.0.0.1"] if n)

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware   # noqa: E402

from auxocase.service.app import create_app  # noqa: E402

app = create_app(DB, scheduler=False)
# The host ends TLS in front of the app: take the scheme from X-Forwarded-Proto, so the origin check on every form
# post and the Secure flag on the session cookie see https.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
