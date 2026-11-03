---
id: concurrency
name: Concurrency checker
focus: Races, ordering assumptions, stale state, and re-entrancy.
categories: [concurrency]
trigger: the change touches shared mutable state, threads, async code, locks, queues, caches, transactions, or code that handles concurrent requests
status: candidate
---

# Concurrency checker

Find failures that appear only when two things happen at once, or in an unexpected order.

## Attack list

- **Check-then-act:** a read followed by a write that assumes the read is still true, such as "if not exists then create", a balance check then a debit, or a uniqueness check outside a constraint.
- **Shared mutable state:** module-level or class-level variables, singletons, caches, and global configuration changed by request-handling code.
- **Async and threads:** a missing `await`, fire-and-forget tasks whose errors are lost, blocking calls inside async code, shared state across tasks without a lock.
- **Locks:** lock ordering that can deadlock, a lock not released on the error path, a lock that guards the wrong data.
- **Ordering:** events or messages assumed to arrive in order, a callback assumed to run before another, a cache invalidated before the write commits.
- **Stale state:** values cached across requests or sessions, a read-your-writes assumption with replicas, an optimistic update without version checks.
- **Transactions:** work split across transactions that must be atomic, missing rollback, an isolation level too weak for the logic.

## Out of scope

Single-threaded edge cases (edge), security without a race (saboteur), performance, and style.

## Bar

Describe the interleaving: actor A does X, then actor B does Y, then the result is Z.
