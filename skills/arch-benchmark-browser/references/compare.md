# Compare with earlier runs

Step 11 reads this, only when the arguments have `compare=`.

List the earlier runs with `python3`:
`sorted((Path.home() / "Downloads" / "benchmark_browser").glob("*/results.json"))`,
since the tools have no `ls` and `glob` does not expand `~`. With run
ids named, read those only, and name a named run that has no
`results.json` as missing. Keep the runs whose `prompt` and `contract`
are both the same text as this run's, and name each run left out and
why. Runs with other `sizes` are kept apart, in a table of their own.
The comparison is a table in the output: a row per site, a column per
run headed by its `run_id` and `repository_head`, and in each cell the
score, the model and effort labels, and `read_version`. Only `ok`
sessions are compared; any other shows its status. A key an earlier run
lacks shows as `not recorded`.
