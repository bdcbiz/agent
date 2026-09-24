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

None of the four patches touch server names, IP addresses, credentials, or
any Almadrasa-internal identifiers — they are generic `frappe/agent` bug
fixes, safe to keep in a public fork and to eventually upstream.

## Rebasing onto a newer upstream

```
git fetch upstream
git rebase upstream/develop
```

Resolve conflicts patch-by-patch (there are only 4 commits on top of
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
