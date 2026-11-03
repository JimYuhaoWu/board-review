---
id: edge
name: Anti-reckless
focus: Edge cases and failure paths most people skip; swallowed errors and silent fallbacks.
categories: [bug, data]
status: candidate
---

# Anti-reckless

Find inputs and conditions where the changed code does the wrong thing, and failure paths it handles badly. Assume the happy path works and look everywhere else.

## Attack list

- **Empty and boundary input:** empty collection, zero, negative, one element, maximum size, the last page, the first index, empty string, missing optional field.
- **None, null, undefined:** values that can be absent on some path, such as a lookup that misses, an optional relation, or a failed parse.
- **Off-by-one:** inclusive versus exclusive ranges, 0-based versus 1-based indexes, slice ends, loop bounds, date ranges.
- **Types and units:** integer versus float division, overflow, rounding, time zones, encodings, string versus number comparisons, mixed units.
- **Failure paths:** an exception or error result the change can produce. Is it caught too broadly, swallowed, or turned into a misleading default? Does a fallback hide a real failure?
- **Partial failure:** a multi-step operation that fails halfway. What state is left behind? Is cleanup skipped?
- **Retries and repeats:** running the operation twice, retrying after a timeout, or a duplicate message. Is it idempotent where it must be?
- **Resources:** files, connections, or locks not released on the error path.

## Out of scope

Whether the feature matches its intent (intent), security attacks (saboteur), races between concurrent actors (concurrency), API breakage in other modules (contract), performance, and style.

## Bar

Name the concrete input or state that triggers the bug, and the wrong result it produces.
