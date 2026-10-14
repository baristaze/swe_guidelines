# Signals

What an ops skill meets of one namespace, or of one process, sits in
one file here: `<ns>.md`, named by the namespace's folder under
`om/src/<name>/om/`, or by the process's folder under `services/`,
`workers/`, or `apps/`. The ops skills that read signals read every
file in this folder and name no namespace in their own text. A copy
adds `<ns>.md` here for each namespace of its own, and edits no skill
and no other namespace's file to do it.

A file opens with `# Signals: <ns>` and one sentence on what the
namespace or the process does. Then it holds, in this order, each of
the headings below it has something under. A heading it leaves out is
a signal it does not emit.

- `## Kinds`: its work kinds, orchestration kinds, and leasable
  resource kinds, each with what it does and what a failure of it
  leaves behind.
- `## Outcomes`: its subsystems of the counter
  `acme_outcomes_total{subsystem, outcome}`, and what each outcome
  means.
- `## Gauges`: the numbers a sweep pass reads for it: a gauge on
  Prometheus locally, and a metric of the same name in the `Acme`
  namespace, with no dimension, in the cloud.
- `## Alarms`: each alarm, `acme-<env>-<suffix>` in the cloud, with
  the condition a local run reads on Prometheus and its threshold.
- `## Queues`: each queue, `acme-<env>-<queue>`, whose dead letter is
  the same name with `-dead` after it.
- `## Log lines`: the lines a process writes about it, with the level
  and the log group, and what each means: the finding it is, the ids it
  carries, and what to do about it.
- `## Search`: one Logs Insights pattern, in a code span, for its lines
  a read of `ERROR` lines misses, such as a start line, a warning, or an
  access line. A `/` in it is written `\/`.
- `## Change events`: the kinds its stream records that say a failure.
- `## Commands`: a person's command for one of its findings, run in
  their own terminal. A skill names it in its report and never runs it.
- `## Report`: the lines it adds to the Signals of an investigation's
  report, in a fenced block, as a skill's Output writes them. A file
  with no Report adds none, and what it explains reaches the report as
  a finding.
