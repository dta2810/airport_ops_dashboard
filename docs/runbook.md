# Runbook

## Always-on components

| Component | When running | Stop |
|---|---|---|
| Bronze pipeline (continuous) | During demo | `python scripts/deploy.py stop -t <target> -p <profile>` |
| Gold pipeline (continuous) | During demo | same |
| Live simulator job (`simulator_live`, ~1 h) | During demo | same (cancels its runs). Prefer letting it finish first: it writes the whole window's schedule at start, so a cancelled run leaves unflown flights SCHEDULED; the next run starts after the last event and those flights show as hours late until the window passes them |
| Classic gold job (`gold_classic`, optional) | After `start-classic` | same, or `deploy.py stop-classic` |
| Lakebase endpoint | Suspends after `lakebase_suspend` idle (default 300 s; set by `deploy.py` on new projects, `deploy.py lakebase-size` on reused ones) | automatic |
| App | While deployed | `databricks apps stop <name>` |

## Health checks

- Lakebase `ops.batch_log`: latest `written_at`, `writer` = gold identity (SP application id, or your email with `--identity deployer`), p95 of `written_at - max_event_ts`.
- UC replica row counts = Lakebase row counts per gold table.
- Freshness target: event → Lakebase p95 ≤ 30 s.

## Teardown

`python scripts/deploy.py destroy -t <target> -p <profile>` — stops the pipelines, then `bundle destroy` after you confirm the target name. The Lakebase project has `prevent_destroy`; remove it deliberately.
