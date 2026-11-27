# Good first issues

Seeded issues for new contributors. File each one on GitHub with
the `good first issue` and `help wanted` labels.

## 1. Add a worst-offenders table

Title: Show the loads with the most collisions

Description: After the per-driver day plan, print a short table
of the five loads with the most findings (load id, driver, truck,
finding count, worst rule), so a dispatcher can see at a glance
which loads to fix first. Loads with no findings are excluded.
Ties should break by load id.

Difficulty: Easy

Acceptance criteria: the table appears in text output with the
correct top five for the sample file (S-102 first with 2 findings,
then S-101, S-104, S-105, and S-106, each with 1 finding, in load
id order); JSON output is unchanged; a test checks the table
rows.

## 2. Add a per-truck day plan

Title: Show a per-truck day plan next to the per-driver plan

Description: Add a second table after the per-driver day plan,
one row per truck (truck, loads, first pickup, last delivery,
total rate), so a dispatcher can see equipment use as well as
driver use. Loads with no truck are grouped under `(no truck)`.

Difficulty: Easy

Acceptance criteria: the table appears with the correct rows for
the sample file (T-101: 6 loads, $3,950.00; T-102: 4 loads,
$2,700.00; T-103: 5 loads, $3,700.00; T-104: 3 loads, $2,650.00;
T-105: 3 loads, $2,850.00; T-106: 3 loads, $1,850.00; `(no
truck)`: 1 load, $650.00); JSON output is unchanged; a test
checks the table rows.

## 3. Support a custom past-pickup cutoff in days

Title: Let teams flag only pickups older than N days

Description: The past-pickup rule flags any pickup before the
as-of date, which can be noisy on a weekly schedule review. Add
a --past-days flag that only flags pickups more than that many
days past the as-of date, so a dispatcher can focus on loads
that have been sitting for a while. When the flag is absent, the
current behavior stays: any past pickup is flagged.

Difficulty: Easy

Acceptance criteria: with --past-days 3 the past-pickup error
for S-105 (pickup 2026-11-10, 8 days past the 2026-11-18 as-of
date) stays, and a pickup 1 day past the as-of date is not
flagged; a test covers both cases; README documents the flag.

## 4. Report the driver with the most collisions in the summary

Title: Print the most double-booked driver

Description: Add one line to the text summary naming the driver
with the most double-booked loads (driver name, load count), so
a dispatcher can act on the worst case without reading every
finding. When nothing is double-booked, the line is omitted.

Difficulty: Easy

Acceptance criteria: the line appears for the sample file naming
Alice Reed with 2 double-booked loads (S-102 on the driver and
S-102 on the truck both count toward her, plus S-124 counts
toward Grace Kim); it is absent for the clean file; JSON output
is unchanged; a test checks both cases.
