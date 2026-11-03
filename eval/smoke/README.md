# Smoke test

A tiny repository with planted bugs, for checking that `/board-review:review` runs end to end. It is **not** a benchmark: it checks mechanics such as dispatch, JSON output, grouping, and labels, not review quality.

```bash
bash eval/smoke/make_repo.sh <empty dir outside this repo>
```

The script prints the base and head SHAs. Keep this README out of the generated repository, so reviewers can't read the answers.

## Planted issues (head commit)

| # | Where | Issue | Expected member | Expected label |
|---|---|---|---|---|
| 1 | `shop/users.py` `search_users` | SQL injection: `name` is formatted into the query | saboteur | CONFIRMED |
| 2 | `shop/users.py` `search_users` | Off-by-one: `offset = page * PAGE_SIZE` skips the first page, although pages start at 1 | intent or edge | CONFIRMED |
| 3 | `shop/orders.py` `average_order_value` | `ZeroDivisionError` for a user with no orders | edge | CONFIRMED |
| 4 | `shop/reports.py` `user_label` | Still calls `get_user(user_id)` after the signature changed to `get_user(db, user_id)` | contract (or intent) | CONFIRMED |
| 5 | `shop/users.py` `unique_emails` | Quadratic membership test on a list | perf, if it fires | PLAUSIBLE or CONFIRMED, Low/Medium |

**Control:** `order_count` is correct. A finding on it is a false positive.

`shop/db.py` also still creates the tables but nothing calls `users.set_db` any more. That is fine: `set_db` was removed with the global, and no caller is left.

## Results

Run on 2026-11-03 with `--members full --format json`, on the free local endpoint (`deepseek-flash`), so the reviewer is **not** the benchmark's Sonnet 5.5. These results check the mechanics only, not review quality on Claude.

| Check | Run 1 | Run 2, after fixes |
|---|---|---|
| Planted issues found and CONFIRMED | (output cut off) | **5 of 5** (#1 Critical; #2, #3, #4 High; #5 Medium) |
| Trigger skipped `concurrency` | no | yes, with a reason |
| Duplicate findings grouped | yes | yes: the off-by-one was found by 5 members, in one group |
| Final JSON valid | no: cut off at 51,000 characters | after one repair (a missing opening `{`) |
| Orchestrator turns / polling calls | 120 / 22 | 31 / 0 |
| Orchestrator input tokens | 6.5 M | 0.23 M |
| Wall time | 47 min | 31 min |

Fixes between the runs:

- Members and verifiers run as foreground agents (`run_in_background: false`), so the orchestrator never polls.
- Length limits on `claim`, `evidence`, and verdict fields, and a 30,000-character target for the final JSON.

Found in run 2, and fixed afterwards without a third run:

- **A wrongful refutation:** a NULL `orders.total` makes `average_order_value` raise `TypeError`. A verifier refuted it because nothing in the repository writes a NULL. The verifier rules now say that the absence of a producer is not counter-evidence; only a guard, type, or constraint is.

Noted, for Phase 4 to decide with data:

- Several low-value findings were CONFIRMED, because they are true: a missing index on a tiny table, and naming remarks. CONFIRMED means real, not important. Precision on Martian will show whether `perf` and `nitpicker` earn their place.
