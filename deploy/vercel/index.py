"""Vercel entrypoint: the same FastAPI app `run.py serve` starts, as one Python function.

A function's own files are read-only and an instance can be recycled at any time, so the bundled snapshot of the
database (gzipped) and its model pickles are unpacked to /tmp on a cold start. What the pages change lives in that
copy only: a new instance starts again from the snapshot. The scheduler is off (no long-running process here), and
Claude is reached over the API (`AUXO_LLM=api`, which reads ANTHROPIC_API_KEY from the project's environment).
"""
import gzip
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
os.chdir(HERE)                                   # the leadership page reads docs/ and outputs/ from here

WORK = Path(os.environ.get("AUXO_WORK_DIR", "/tmp/auxo"))
DB = WORK / "auxocase.db"
if not DB.exists():
    WORK.mkdir(parents=True, exist_ok=True)
    part = WORK / "auxocase.db.part"
    with gzip.open(HERE / "seed" / "auxocase.db.gz", "rb") as src, open(part, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    shutil.copytree(HERE / "seed" / "models", WORK / "models", dirs_exist_ok=True)
    part.rename(DB)                              # last, so a half-unpacked file is never opened

os.environ["AUXO_DB"] = str(DB)
os.environ.setdefault("AUXO_LLM", "api")
os.environ.setdefault("AUXO_SCHEDULER", "0")
if not os.environ.get("AUXO_ALLOWED_HOSTS"):     # this deployment's own names, as Vercel gives them
    names = [os.environ.get(k, "") for k in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL")]
    os.environ["AUXO_ALLOWED_HOSTS"] = ",".join(n for n in names + ["localhost", "127.0.0.1"] if n)

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware   # noqa: E402

from auxocase.service.app import create_app  # noqa: E402

app = create_app(DB, scheduler=False)
# Vercel ends TLS in front of the function: take the scheme from X-Forwarded-Proto, so the origin check on every
# form post and the Secure flag on the session cookie see https.
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
