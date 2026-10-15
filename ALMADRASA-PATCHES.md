# Almadrasa patches on top of frappe/agent

This branch (`almadrasa/develop`) is `upstream/develop` (frappe/agent) plus a
small stack of local patches that were running live on our NODE-2 build/press
agent and were never upstreamed. It exists so that:

1. Nothing running in production is only "on disk on one server" — it is
   committed, on GitHub, in `bdcbiz/agent`.
2. We can move NODE-2 onto a current, supported `develop` (including the
   `e0e3d3e` Node-engine-version fix) without losing our fixes or falling back
   to `git reset --hard` wiping them, which is what Press's "Update Agent"
   button currently does.

Do **not** confuse this branch with `almadrasa/snapshot-node2-2026-09-24`,
which is a byte-for-byte capture of what was actually live on NODE-2 at
commit `ccf9671` on 2026-09-24 (old base, uncommitted diff turned into one
commit). This `almadrasa/develop` branch is the maintained, rebasable version
of the same intent, built as one commit per logical fix on top of current
upstream.

## Patch list

| # | Commit | File(s) | What it does | Why it's still needed |
|---|--------|---------|---------------|------------------------|
| 1 | `fix(storage): pass custom S3 endpoint_url for R2-compatible backups` | `agent/database_server.py`, `agent/site.py`, `agent/snapshot_recovery.py` | Reads an optional `ENDPOINT_URL` out of the backup auth dict and passes it to the boto3 S3 client, so backups/restores can target a non-AWS, S3-compatible endpoint (Cloudflare R2 in our case). | Not touched anywhere in the 43 commits between the old NODE-2 base (`ccf9671`) and current `upstream/develop`. We rely on R2 for offsite backups; without this, boto3 always talks to AWS regardless of the account's real endpoint. |
| 2 | `fix(server): write rendered templates atomically` | `agent/server.py` | `_render_template()` now writes to a temp file in the same directory, fsyncs, copies the destination's file mode, then `os.replace()`s it into place, instead of truncating the destination file in place. | Prevents nginx/supervisor from ever reading a half-written config file mid-render (e.g. during a reload race). Not superseded by anything upstream — the function is otherwise unchanged since `ccf9671`. |
| 3 | `fix(supervisor): autorestart the bench-schedule program` | `agent/templates/bench/supervisor.conf` | Adds `autorestart=true` to the `bench schedule` supervisor program. | That program previously had no autorestart, so a crash/OOM kill left the scheduler down until the next full bench update. `e8da4a9` (in the 43-commit range) touches the *same file* but a different, unrelated program block (renames the `no_of_wokers` config key to `background_workers` for the RQ worker pool) — no overlap, no conflict. |
| 4 | `fix(bench): refuse to write empty nginx.conf when sites exist on disk` | `agent/bench.py` | Before generating `nginx.conf`, compares `self.valid_sites` against a directory scan of `sites_directory`. If `site_config.json` exists for one or more sites but `valid_sites` came back completely empty, raises instead of silently writing a zero-server-block nginx config (which would drop routing for every site on the bench). | Hand-reapplied: upstream commit `7b82b1b` changed `generate_nginx_config()`'s signature (added `ignore_missing_site_configs`) and added a retry loop, but that loop only handles **one stale/moved site** during a parallel site-update job (a `FileNotFoundError` on a single site's config file). It does not guard against `valid_sites` returning nothing at all, which is the scenario this patch protects against. Different failure mode, not superseded. |
| 5 | `fix(deps): bump boto3 off the Python-3.12-incompatible 1.18.0 pin` | `requirements.txt` | Bumps `boto3` from `1.18.0` to `1.35.99` (with `botocore==1.35.99`, `s3transfer==0.10.4`, `jmespath==1.1.0` resolved as transitive deps). `urllib3==1.26.18` is left untouched — botocore 1.35.99 is compatible with it. | `boto3==1.18.0` pulls `botocore==1.21.65`, which imports `botocore.vendored.six.moves`. That vendored-`six` shim was removed from the Python standard behavior it depended on and does not work under Python 3.12, which is what NODE-2's agent venv runs. Every offsite (R2) backup failed with `ModuleNotFoundError: botocore.vendored.six.moves`. Patch #1 (S3 `endpoint_url`) depends on boto3 actually importing, so this fix is a prerequisite for #1 working at all on Python 3.12. See "Patch #5 detail" below. |
| 6 | `fix(registry): redact credentials from commands and job records` | `agent/base.py`, `agent/builder.py`, `agent/job.py`, `agent/server.py` | Uses Docker password stdin for patch-image builds, writes empty stdin values too, and redacts a conservative set of credential fields before persisting job arguments. | Prevents registry credentials from appearing in process argv, execution records, or the agent jobs database while preserving the original values for queued work. |

None of the six patches contain server names, IP addresses, credentials, or
any Almadrasa-internal identifiers — they are generic `frappe/agent` bug
fixes, safe to keep in a public fork and to eventually upstream.

## Rebasing onto a newer upstream

```
git fetch upstream
git rebase upstream/develop
```

Resolve conflicts patch-by-patch (there are only 5 commits on top of
upstream). After every rebase, re-run:

```
python3 -m py_compile agent/bench.py agent/database_server.py agent/server.py \
  agent/site.py agent/snapshot_recovery.py
```

and re-check patch #4 in particular against whatever `generate_nginx_config()`
looks like on the new upstream — it is the one most likely to need a manual
reapply rather than a clean `git rebase`.

## Candidates for upstreaming (PR to frappe/agent)

- **#2 (atomic template write)** and **#3 (schedule autorestart)** are
  generically useful, low-risk, and have no Almadrasa-specific assumptions —
  good candidates for a PR to `frappe/agent`.
- **#4 (empty nginx.conf guard)** is also generic and worth proposing, ideally
  after confirming with upstream maintainers whether `valid_sites` returning
  empty while `site_config.json` files exist is a known/expected transient
  state they'd rather fix at the source.
- **#1 (S3 endpoint_url)** is useful to anyone using an S3-compatible store
  other than AWS (R2, MinIO, etc.) and is a reasonable PR candidate, but is
  lower priority since it's purely additive/optional (`auth.get("ENDPOINT_URL")`
  defaults to `None`, i.e. current AWS behavior is unchanged).
- **#5 (boto3 bump)** is arguably the highest-priority upstream candidate of
  the five: `boto3==1.18.0` is flat broken on any Python 3.12 agent host, not
  just ours, and the fix is a one-line version bump with no behavior change
  for anyone already on an older, working Python. **Not yet fixed upstream**
  as of this writing (`frappe/agent` `develop` still pins `boto3==1.18.0`).

## Patch #5 detail: boto3/botocore Python 3.12 incompatibility

**Impact:** Offsite (R2) backups failed on NODE-2 with:

```
ModuleNotFoundError: botocore.vendored.six.moves
```

**Root cause:** The agent's Python virtualenv on NODE-2 runs Python 3.12.3.
`requirements.txt` pinned `boto3==1.18.0`, which resolves to
`botocore==1.21.65`. That old botocore still imports its own vendored copy of
`six` (`botocore.vendored.six.moves`) internally, and that code path is
incompatible with Python 3.12 (`six`'s import-hook machinery was written
against import-system internals that changed). Every `import boto3` (and
therefore every S3 client creation used by the offsite-backup and
snapshot-restore code paths in `agent/database_server.py`, `agent/site.py`,
and `agent/snapshot_recovery.py`) crashed at that import.

This is a pre-existing landmine in `requirements.txt`, not something this
patch stack introduced — but the **2026-09-24 agent update re-pinned it**
(the update process reinstalls `requirements.txt` as-is), so the breakage
recurs on every future "Update Agent" unless the pin itself is fixed here, on
the branch Press actually pulls from.

**New constraint:**

```
boto3==1.35.99
```

(`botocore==1.35.99`, `s3transfer==0.10.4`, `jmespath==1.1.0` are pulled in
as resolved transitive pins — not hand-written into `requirements.txt`, since
upstream doesn't pin transitive deps there either.) `urllib3==1.26.18`
(already pinned in `requirements.txt`) did not need to change. Checked
botocore 1.35.99's actual declared constraint
(`importlib.metadata.requires('botocore')`): on Python ≥3.10 it requires
`urllib3>=1.25.4,<3,!=2.2.0`, which `1.26.18` satisfies, so the existing
`requirements.txt` line for `urllib3` was left untouched.

A pinned exact version (`==1.35.99`) was chosen deliberately over an open
`>=` range, so a future `pip install -r requirements.txt` can't silently pull
in a newer, untested boto3/botocore at update time.

**Test (2026-09-25, this session, local machine, not NODE-2):**

- Python 3.12.12 venv (via `uv venv --python 3.12`), separate from any
  server.
- `pip install boto3==1.35.99` (unpinned range tried first:
  `boto3>=1.34,<1.36` with `urllib3==1.26.18` pinned — resolved cleanly to
  `boto3==1.35.99`, `botocore==1.35.99`, `urllib3==1.26.18` unchanged; then
  pinned the resolved version exactly).
- Full `requirements.txt` (with the new boto3 line) installed cleanly except
  for 3 Linux-only wheels with no macOS build
  (`mariadb-binlog-indexer`, `mariadb-table-usage`, `py-spy` — these only
  ship `manylinux2014` wheels and are expected to be unavailable on a Mac
  dev machine; they were excluded from this local test only, not from
  `requirements.txt`).
- Ran:
  ```
  python -c "import boto3, botocore; import botocore.httpsession, botocore.utils; print(boto3.__version__, botocore.__version__)"
  ```
  → `1.35.99 1.35.99`, no `ModuleNotFoundError`.
- Imported `agent.site` and `agent.snapshot_recovery` directly (the two
  modules that construct boto3 S3 clients and don't hard-depend on the
  Linux-only `mariadb_binlog_indexer` package that `agent.database_server`
  imports at module load) — both imported cleanly.
- Created a mock S3 client with no network call:
  ```
  boto3.client('s3', endpoint_url='http://localhost:9',
               aws_access_key_id='x', aws_secret_access_key='x',
               region_name='us-east-1')
  ```
  → client constructed successfully, `client.meta.endpoint_url ==
  'http://localhost:9'`, confirming patch #1's `ENDPOINT_URL` plumbing still
  works against the new boto3.

**This was tested locally only. It was NOT applied to NODE-2 or any other
server in this session.**

### Applying to the server (NOT done in this session — do this separately)

Either:

- **Via Press's "Update Agent"**, once it pulls this commit from
  `almadrasa/develop` — but only trigger it at a moment when no build or
  site migration is in flight on that node, since `update_agent` reinstalls
  `requirements.txt` and restarts the agent.

- **Or manually**, on the target node:

  ```
  sudo -u frappe /home/frappe/agent/env/bin/pip install -r /home/frappe/agent/repo/requirements.txt
  ```

  then:

  ```
  sudo supervisorctl restart agent:
  ```

**Then verify, in order:**

1. Confirm the import actually works in the live agent venv:

   ```
   sudo -u frappe /home/frappe/agent/env/bin/python -c "import botocore.httpsession"
   ```

   (no output / no traceback = success)

2. Run **one** offsite backup against a canary/test site only — e.g.
   `canary-sbh-20260922.almadrasa.ai` — and confirm it completes without the
   `botocore.vendored.six.moves` error and that the object actually lands in
   the R2 bucket. Do not treat step 1 alone as sufficient — it only proves
   the import works, not that a real backup+upload succeeds end to end.
