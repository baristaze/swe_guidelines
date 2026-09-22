# Operations

Group id: `ops`. Covers Operations and Documentation as Code of
`architecture.md`.

This group judges how the system is operated once it is deployed: who
holds which credential, what a skill may do under it, what is declared
as code beside the environment, and what a reader is served. It leaves
the deployment posture and the environments themselves to
`delivery` (DEL-02, DEL-03, DEL-38, DEL-41, DEL-42, DEL-43), what a
process emits and where it lands to `delivery` (DEL-19, DEL-20,
DEL-32, DEL-39), the `devx` profile and the
local URLs to `delivery` (DEL-33, DEL-34), the in-process suite and the
deployed smoke test's place beside it to `delivery` (DEL-36), the
operator plane's gate and its context to `context` (CTX-20), and the
platform's own secrets, the ones the secret store holds, to `async`
(ASY-13, ASY-28).

## OPS-01 Every task is a skill, and the credential is the boundary

**Principle.** Every operational task is a skill a person runs with an
agent. The safety boundary is the credential the skill holds, never
the prompt. A cloud credential a person or an agent holds reads and
never writes; one that writes is held by a pipeline, or by the
administrator for its two named steps, creating and destroying an
environment.

**Source.** Operations.

**Look for.** The operational tasks the tree holds (investigating an
alarm, tracing a complaint, planning an infrastructure change, driving
traffic) and whether each is a skill; the credential each skill runs
under and whether it can write; what stands between an agent and a
write, a prompt or a credential.

**Violation.** An operational task done by hand from a runbook with no
skill; a skill an agent runs under a credential that writes; a prompt's
instruction ("do not apply") as the only thing keeping an agent from a
write; a writing cloud credential held by a person for anything but the
administrator's two steps.

**Severity.** high

## OPS-02 Four roles; the administrator creates, destroys, and nothing else

**Principle.** Four roles operate a platform. The administrator,
deployer, and investigator are cloud roles under named profiles; the
supporter is the investigator's plus an operator-plane identity. The
administrator is a role a person holds, granted for the run: it creates
and destroys an environment and is the break-glass, recorded and
time-bound, and no other skill runs under it.

**Source.** Operations, Operator Roles.

**Look for.** The roles each bootstrap root declares and the profiles
that hold them; the permissions of the administrator permission set;
which skills name an administrator profile.

**Violation.** A fifth operator role, or a role with no profile (a
person's everyday permission set is not one: no skill runs under it);
a person's everyday profile holding the administrator role; an
administrator permission set held between runs; a skill other than
the create and destroy runs that names the administrator profile; an
administrator role assumed by a pipeline; a break-glass grant with
no record, no time bound, or no reconciling pull request after it.

**Severity.** high

## OPS-03 The deployer is the pipeline, and the only role that writes

**Principle.** The deployer is the pipeline: one per environment,
assumed by the workflow through the repository host's identity
federation, never by a person. Production has two, one that plans and
one that applies. An infrastructure change is a pull request the
deployer applies; a data change is an operation of the platform, and
the manager decides it.

**Source.** Operations, Operator Roles.

**Look for.** The trust policy of each deployer role and who can assume
it; the production workflow's plan role against its apply role; how an
infrastructure change and a data change each reach the cloud.

**Violation.** A deployer role a person can assume, or a long-lived key
for it; one production role that both plans and applies; a change
applied from a laptop, the administrator's two runs aside (OPS-19); a
data fix run as a statement against the
database instead of an operation under a context. (The plan approval
itself is DEL-38.)

**Severity.** high

## OPS-04 The investigator reads everything and writes nothing

**Principle.** The investigator, one per environment, reads everything
and writes nothing: every log group, metric, trace, error, alarm, the
description of every resource, and the state of the infrastructure,
which holds no secret (DEL-46). It cannot read a secret's value, a data
bucket's objects, or a database row, or assume any other role.

**Source.** Operations, Operator Roles.

**Look for.** The investigator role's permission set: the read
statements, any write or assume-role statement, and the denies on the
secret values, the data buckets' objects, and the database; whether
one exists per environment.

**Violation.** An investigator role with a write permission, a
secret-value read, an object read on a data bucket, a database
connection, or an assume-role on any other role; one investigator role
shared by two environments.

**Severity.** high

## OPS-05 The supporter reads a named tenant through the operator plane

**Principle.** The supporter is the investigator plus one thing: a read
of a named tenant's rows through the platform's operator plane, never
a database login. The tenant is a parameter of every read. The
credential is an identity on the operator allowlist whose entry says
read. Every such read is logged with the tenant and the operator.

**Source.** Operations, Operator Roles; Operational Skills.

**Look for.** The cloud role the supporter's skill holds, which is the
investigator's; the allowlist entry the supporter's identity holds and
what it says; the tenant parameter on every operator read route the
skill calls; the log line the operator plane writes on a tenant read.

**Violation.** A cloud role for the supporter with a permission the
investigator lacks, a database login above all; an allowlist entry that
says write; an operator read with no tenant parameter, or one that reads
across tenants; a tenant read that leaves no line naming the tenant and
the operator. (The gate and the context of the operator plane are
CTX-20.)

**Severity.** high

## OPS-06 Roles are named `<product>-<verb>-<environment>`, fenced by tag

**Principle.** Roles are named `<product>-<verb>-<environment>`, the
verb `deploy`, `plan`, or `investigate`, so the name says what it is
and where it reaches. A role lives in its
environment's account and its permissions stop there. Its fences also
deny every other environment by tag, which holds if a root is ever
applied in the wrong account.

**Source.** Operations, Operator Roles.

**Look for.** The role names in each bootstrap root; the account each
role lives in; the deny on the other environment's tag in each role's
fences; whether a staging role reaches a production resource.

**Violation.** A role whose name lacks the product, the verb, or the
environment, or puts them in another order; an investigator profile not
named `<product>-<environment>-investigate`; a role in an account that
is not its environment's; a role with no deny on the other environment's
tag; a staging role that lists a production resource.

**Severity.** medium

## OPS-07 People sign in through the identity center; agents chain from it

**Principle.** People sign in through the identity center, with a second
factor and short-lived credentials: no cloud user, no long-lived key.
The investigator trusts its account's everyday role by pattern, and an
agent chains from the person's session, holding no more than the person.
In production the everyday set writes nothing.

**Source.** Operations, Operator Roles.

**Look for.** Any cloud user or access key in the roots, the scripts,
or the cloud tool's configuration; the trust policy of each
investigator role and the principal pattern it matches; the profile
chain from the signed-in profile to each investigator role.

**Violation.** A cloud user, or an access key minted for a person or an
agent; an investigator role that trusts another account, or a
principal by a copied generated name; an agent profile holding a key
of its own instead of chaining from a person's session; a production
everyday permission set that writes; a sign-in with no second factor.

**Severity.** high

## OPS-08 A skill verifies its credential and refuses a wider one

**Principle.** A skill names the profile it needs and runs under it
alone. Before it reads anything, it asks the cloud who it is and
compares the role and the account with the ones the environments' file
names. Under a wider credential it stops. A script that writes clears
the shell's exported keys first, and asks again before every apply.

**Source.** Operations, Operator Credentials.

**Look for.** The profile each skill names; the identity call at the
top of every skill and the comparison that follows it, the account
included; what the skill does when the answer is a wider role; the
same check, reversed, in the create and destroy scripts, before each
apply, after the exported keys are cleared.

**Violation.** A skill that reads before it checks who it is; a skill
that proceeds under the administrator or the deployer because more is
enough; a skill that takes whatever profile or keys the shell has set;
a create or destroy run that accepts another profile, applies without
checking the account, or compares it with nothing written down.

**Severity.** high

## OPS-09 No credential enters the repository, a skill, a log, or a report

**Principle.** The cloud profiles live in the cloud tool's own
configuration, one per role per environment. Everything else an
operator reaches lives in one owner-only file per environment outside
the repository. Neither an operator's credentials nor the platform's
secrets enter the repository, a skill's text, a log line, or a report.

**Source.** Operations, Operator Credentials.

**Look for.** Where each skill reads the error tracker's token, the
operator plane's identity, and the base URL of its environment; the
file's path and permission bits; the tree, the skill texts, the log
lines, and the reports for a token, a key, or a profile's secret.

**Violation.** A token in a skill's text or in a file the repository
tracks (a public name or an account id in the environments' file is no
credential, DEL-42); a credentials file readable by the group or the
world; a report or a log line that prints what the file holds; a skill
that reads a profile's key from anywhere but the cloud tool's
configuration. (The platform's own secrets are ASY-13 and ASY-28.)

**Severity.** high

## OPS-10 The local stack is an environment every skill runs against

**Principle.** The local stack is an environment too. Its file names
the compose stack and the developer dashboards of the `devx` profile,
so every skill runs against the developer's machine with no cloud at
all. Every skill takes the environment it acts on, and `local` is one
of them for every skill but the administrator's two.

**Source.** Operations, Operator Credentials; Operational Skills.

**Look for.** The environment parameter of every skill and the values
it accepts; the `local` file beside the cloud ones and what it names;
a branch in a skill that assumes a cloud.

**Violation.** A skill with no environment parameter, or one that
refuses `local`; no owner-only file for the local stack; a skill that
calls the cloud's identity service when the environment is `local`; a
skill that reaches the local dashboards by a hard-coded port instead
of the file. (The `devx` profile and its URLs are DEL-33 and DEL-34.)

**Severity.** medium

## OPS-11 The built-in skill set, one per task that repeats

**Principle.** Every system ships with a built-in set of operational
skills, one per task that repeats, project-local: the scaffold writes
them from a shared template with the product's name in, and they read
the product's own documents. Every skill states its role, the
credential check, what it reads, what it never does, and the shape of
its report.

**Source.** Operations, Operational Skills.

**Look for.** The skill folder of the tree against the nine names the
guideline lists: `ops-investigate`, `ops-watch`, `ops-root-cause`,
`ops-infra-as-code`, `ops-cloud-deployment-create`,
`ops-cloud-deployment-nuke`, `ops-simulate-traffic`,
`stress-test-create-or-update`, `stress-test-run`; the five statements
at the top of each; where each reads what is specific to the product.

**Violation.** A repeating task with no skill, or a listed skill
missing from the tree; a skill that names no role or no credential
check; a skill that says what it does and not what it never does; a
skill with the product's specifics written into the template instead
of read from the product's documents.

**Severity.** medium

**Check.** `arch-check` decides that the nine skills exist; the rest is
judged.

## OPS-12 A watch outlives its conversation and is written for a burst

**Principle.** A watch outlives the conversation that started it, so the
invoking agent spawns another to run it. It batches per interval, caps
what it reports, never reads a window twice, and reads its profile each
interval, stopping when the person's session ends.

**Source.** Operations, Operational Skills.

**Look for.** The `ops-watch` skill's statement that it is a loop and
how it is launched; the interval, the batch, the cap, and the cursor
that moves the window.

**Violation.** A watch run in the conversation that started it; a
watch that reports every line of a burst; a watch that re-reads a
window it already reported, so an alarm repeats; a watch that retries
on an expired session instead of ending and saying so.

**Severity.** low

## OPS-13 The first responder reads whose traffic it was before it escalates

**Principle.** The first responder to an alarm is an agent. Before it
escalates, it reads the platform's size and whose traffic raised the
alarm. Outside production, the team's own traffic may be suppressed,
recorded with its reason; in production nothing is. What it cannot
explain, it escalates with everything it read.

**Source.** Operations, Operational Skills.

**Look for.** The size read in `ops-investigate` before any escalation;
the reason a suppressed alarm carries; what an escalation carries.

**Violation.** An agent that escalates without reading the tenant, user,
and traffic counts; a suppression in production, or one by size alone;
a suppression with no reason or no record; an escalation that names
the alarm and nothing the agent read.

**Severity.** medium

## OPS-14 One operator dashboard per environment, as code in both twins

**Principle.** Every environment has one operator dashboard, declared
with the environment: the cloud one in Terraform, the local one
provisioned into the metrics view of the `devx` profile. Both carry
the same panels, and a test holds the panel titles equal, so what an
operator learns on the local stack is what they see in production.

**Source.** Operations, Dashboards and Alarms as Code.

**Look for.** The dashboard resource in the environment's Terraform
and the provisioned dashboard file under the local stack; the panels
against the list the guideline gives (every process up, requests per
second by route, responses by status, p95 by route, each counted
subsystem's outcomes, one row for the backing services); the test
that reads both and compares titles.

**Violation.** A dashboard built by hand in the cloud console; a local
stack with no dashboard, so an operator learns nothing before
production; a panel present in one twin and not the other; no test
that holds the titles equal.

**Severity.** medium

## OPS-15 A default alarm set goes to one topic per environment

**Principle.** A small default set of alarms goes to one topic per
environment, and a person's address subscribes to it. The set covers
the edge, the processes, the database, and, in a system with a queue,
the queue. The thresholds are numbers,
and the numbers are the system's; the set and the topic are the
shape.

**Source.** Operations, Dashboards and Alarms as Code.

**Look for.** The alarm resources in the environment's Terraform, the
topic they publish to, and the subscription on it; the three areas the
set covers (the error ratio, the latency, and the unhealthy targets at
the load balancer, a service below its desired count, the database's
processor and free storage, the oldest waiting item's age, parked and
failed work, and the outbox's lag); where the thresholds come from.

**Violation.** An environment with no alarm, or alarms that reach no
topic and no person; an edge, a process, or a database with no alarm
on it; a queue with no alarm on its oldest item or its failed work; a
second topic per environment; a threshold copied from another
system with no reading of this one.

**Severity.** medium

## OPS-16 A tenant's view of its organization is a product feature

**Principle.** A tenant admin's view of their own organization is a
product screen: a feature served by the app-specific service from the
activity role. It is never a telemetry query. A metric carries no
tenant id, and a log search is an operator's tool, never a tenant's
screen.

**Source.** Operations, Dashboards and Alarms as Code.

**Look for.** Where the portal's usage or activity screen gets its
numbers: an app-specific service route over the activity role, or a
metrics or log query; any tenant id in a metric label or a log query
built for a tenant.

**Violation.** A tenant-facing screen fed by a query against the
metrics store or the log groups; a tenant id added to telemetry so
such a query could work; an operator dashboard panel exposed to a
tenant as their view. (The bounded labels are DEL-35.)

**Severity.** medium

## OPS-17 Every process declares its autoscaling; one root variable turns it on

**Principle.** Every service and every worker declares its autoscaling
with its deployment: a minimum, a maximum, and a target the runtime
tracks, the minimum being the desired count. One variable per
environment turns it on, off by default, and every lever below it is
declared on. The database's storage grows on its own from the start.

**Source.** Operations, Scale-Out as a Lever.

**Look for.** The autoscaling declaration on each service and worker
module and its minimum against the desired count; the root variable
per environment and its default; the per-process levers and their
defaults; the storage autoscaling on the database.

**Violation.** A service or worker with no autoscaling declaration; a
minimum below the desired count, so turning the lever on changes the
running count; a root variable defaulting to on; a lever below the
root that is off, so the flip scales part of the environment; a flip
made outside a pull request; a database with a fixed disk.

**Severity.** medium

## OPS-18 A budget and an anomaly monitor from the first apply

**Principle.** Every environment's account has a budget from its first
apply. It names a monthly amount and alerts the owner at half of it, at
nine-tenths of it, at all of it, and when the forecast crosses it. An
anomaly monitor watches each service's spend and reports a jump. Every
resource carries the environment tag through the provider's default
tags.

**Source.** Operations, Cost Boundaries.

**Look for.** The budget resource in each bootstrap root, its amount,
its four thresholds, and the address they alert; the anomaly monitor and
its subscription; the provider's default tags block and the environment
tag in it.

**Violation.** An account with no budget, or a budget added after the
first apply; a budget with one threshold and no forecast alert; no
anomaly monitor; a resource with no environment tag, so its cost
lands in no environment's column. (Retention on every log group is
DEL-32.)

**Severity.** medium

## OPS-19 Create and destroy are scripted, narrated, and dry-runnable

**Principle.** Creating and destroying an environment are the
administrator's two runs: scripts the repository holds, run by the
skills that narrate them. Each prints every command before running
it; a dry run prints them without running anything. Production is
destroyed only behind a typed name and a released change that turned
its deletion protection off, and leaves a final snapshot.

**Source.** Operations, Creating and Destroying an Environment.

**Look for.** The create script: one account per run, the bootstrap
root and its state moved into its bucket, the delegation, the
investigator's profile, the repository host's environments with their
branch policies and variables, staging's first deploy and production's
run ending at its bootstrap, and the order across accounts. The
destroy script: the word staging goes on, production's typed name, the
branch and the state it reads deletion protection from, the final
snapshot and the backups it keeps, the exact origin commit it applies
from in a clean worktree, and the report of what remains.

**Violation.** A step of creation done by hand in the console; a run
that executes a command it did not print, or has no dry run; a
production destroy that goes on a word, or one where the same run
turns deletion protection off; a protection check read on `main`
instead of `release` and the applied state; a production destroy that
skips the final snapshot or deletes the automated backups; a destroy
that ends without naming what remains, the snapshot among it; a
destroy applied from the working tree it was started in.

**Severity.** high

## OPS-20 One traffic generator drives the edge with realistic sessions

**Principle.** The repository holds one traffic generator, and
everything that drives the system at load rides it. It drives the
edge, the app-specific services, never a domain service and never a
manager, with realistic sessions. A profile sets how many tenants,
people, and concurrent users, and the think time; four ship: light,
regular, heavy, stress. It runs against any environment.

**Source.** Operations, Traffic and Stress.

**Look for.** The generator under the tree and any second load tool
beside it; the routes a session calls (a sign-in, a list, writes, an
edit, a completion, a reopen, a read of the stream, one socket, a
sign-out) and whether all are edge routes; the profile definitions;
how it reaches tenants (the operator plane) and everything else (the
public routes); the report it prints (requests by route and status,
p50, p95, p99, the error ratio).

**Violation.** A second load script, or a stress tool that calls a
manager or a domain service directly; a session that is one request
repeated; a generator that cannot target `local`; a generator that
creates tenants through the database instead of the operator plane,
or under an identity other than its own;
a report an operator cannot read against the dashboard.

**Severity.** medium

**Check.** `arch-check` decides a second load tool among the
dependencies; the rest is judged.

## OPS-21 The stress test is the generator with a scenario and a target

**Principle.** The stress test is the same generator with a scenario: a
profile, a duration, a ramp, a soak, and a target stated before the
run. A run reads the signals back and passes or fails against the
target. The gate runs thirty seconds at the light profile against the
local stack, and proves the wiring, never the capacity.

**Source.** Operations, Traffic and Stress.

**Look for.** The scenario definition and its target (a p95 and an
error ratio) written before the run; the read-back after the run and
the comparison that decides pass or fail; the gate's invocation of the
generator, its duration, its profile, and its environment.

**Violation.** A stress run with no target stated before it, or one
whose result is a number and no verdict; a run that reads no signal
back; a gate that runs a heavy profile, runs longer than thirty
seconds, or drives a cloud environment; a gate whose pass is read as
proof of capacity.

**Severity.** low

## OPS-22 One test reads every signal back by request id

**Principle.** One integration test closes the loop. It starts the
process with the trace exporter and the error tracker configured,
drives one session through the edge with one call failing on purpose,
and reads back by the request id the log line, the counter, the trace,
and the error event. The readers are one interface with two impls,
local and cloud.

**Source.** Operations, The Telemetry Round Trip.

**Look for.** The round-trip test: the exporter and tracker it
configures, the session it drives, the request id it takes from the
response, and the four reads; the reader interface, the local impl
over the `devx` stores, and the cloud impl over the cloud's; whether
the same test is what runs against a deployed environment, and how
that run differs: no deliberate failure, the investigator's profile for
the reads when a person runs it, a read-only grant of the pipeline's
when a deploy runs it, and an identity and a tenant named for the
smoke test.

**Violation.** A signal that is emitted and never read back by a test;
a test that asserts on the exporter's mock instead of the store; a
reader written once for the local stack with no cloud impl, or two
tests instead of one interface; a deployed smoke test that is a
different test from the local round trip; a deployed run that fails a
call on purpose in production, or signs in as a real tenant's user.
(The deployed run's place
beside the in-process suite is DEL-36.)

**Severity.** medium

## OPS-23 Documents are code, reviewed with the change they describe

**Principle.** The documents in the tree are code. They are reviewed in
the same pull request as the change they describe, versioned with it,
and read by people and by agents alike. An agent that operates the
system on its first day reads them first, so they are written for
that reader too.

**Source.** Documentation as Code.

**Look for.** A change to a route, a role, a skill, or a folder and
whether the document that describes it moved in the same pull
request; documents kept outside the tree; a document that assumes a
reader who already knows the team.

**Violation.** A pull request that changes what a document describes
and leaves the document behind; a runbook or a README held in a wiki
and not the tree; a document an agent on its first day could not act
on without asking a person.

**Severity.** low

## OPS-24 Every abstraction level carries a README in its own language

**Principle.** Every folder that is an abstraction level carries a
`README.md` that speaks that level's language: `om/` in the product's
nouns, `deployment/` in processes and environments, `ops/` in roles,
signals, and skills. Developer concerns live in the developer's
folders and operator concerns in the operator's: a local-stack tip
under `deployment/local/`, a runbook under `docs/runbooks/`.

**Source.** Documentation as Code, A README at Every Level.

**Look for.** A `README.md` in each top-level folder that is a level
(`om/`, `deployment/`, `ops/`, the services, the apps); the nouns each
one uses against the folder's altitude; where a local-stack tip and a
runbook live.

**Violation.** A level with no README; a README that mixes altitudes,
a deployment note in `om/` or a domain rule in `deployment/`; a
runbook or a local-stack tip in a README about nouns.

**Severity.** low

**Check.** `arch-check` decides a README in each level folder; the rest
is judged.

## OPS-25 `om/README.md` is written for a reader with no code

**Principle.** `om/README.md` is the one every reader gets. It names
the nouns of the system and how they relate, and it is written for a
reader with no code. It carries no developer instruction and no
operator instruction, and it points one level down to a README per
namespace.

**Source.** Documentation as Code, A README at Every Level.

**Look for.** `om/README.md`: its nouns and relations, any command,
path, or setup step in it, any role or signal in it; the README under
each namespace folder and whether it says what its nouns are, what
can happen to them, and which rules hold.

**Violation.** An `om/README.md` that tells a developer how to run
tests or an operator how to read a log; one that assumes the reader
has the code open; a namespace with no README, so `om/README.md`
carries every detail itself.

**Severity.** medium

**Check.** `arch-check` decides a README per namespace and a command in
`om/README.md`; the rest is judged.

## OPS-26 `llms.txt` names what each audience is served

**Principle.** Documents are granular, one subject each. What each
reader is served is written down in one map at the repository root,
`llms.txt`: a title, a summary, and one section per audience with one
line per link. Three audiences are named: platform developers,
platform operators, and the tenant's users and admins. Exposure is by
listing, never by location.

**Source.** Documentation as Code, The Knowledge Map.

**Look for.** `llms.txt` at the root: its title, summary, and the
three audience sections; each link's one line; a document in the tree
listed under no audience, or a document an audience is meant to have
that its section lacks; a document that covers two subjects.

**Violation.** No `llms.txt`, or one with a fourth audience or a
missing one; a document served because of the folder it sits in and
not a listing; a link with no line, or a section with prose instead of
links; one document that is a folder's worth of subjects.

**Severity.** medium

**Check.** `arch-check` decides the shape of `llms.txt` and that each
link resolves; the rest is judged.

## OPS-27 Every document speaks product, technology, or service

**Principle.** The language of every document is the language of the
product, the technology, or the service: no deployment trick, no
team-internal note, no credential, and no hostname of a real
environment. What a document explains is what the product explains to
its own customers, so a document that leaks costs nothing.

**Source.** Documentation as Code, The Knowledge Map.

**Look for.** Every document under the tree that `llms.txt` lists:
hostnames, tokens, account ids, internal ticket references, and
workarounds in it; the map for a narrower line the team drew.

**Violation.** A real environment's hostname or an account id in a
document (the environments' file, which the scripts and roots read, is
configuration and not a document); a token in a runbook; a note that
only a team member could act on; a deployment workaround written into a
document served to a tenant.

**Severity.** high

## OPS-28 One repository-host environment per deployer credential

**Principle.** Each deployer credential has its own repository-host
environment, deploying from its branch alone: staging's, production's
plan with no reviewer, production's apply with the reviewer. The trust
names the environment, the ref, and the repository's immutable id. Each
environment holds its own variables under the same names, and no cloud
key is stored.

**Source.** Operations, Operator Roles.

**Look for.** The environments on the repository host, the rules on
each, and their deployment-branch policies; the variables each holds and
their names; the trust of each deployer role, the environment its
subject names, the ref it requires, and whether it matches the
repository by `repository_id` and its owner's id; any cloud key stored
as a secret.

**Violation.** One environment shared by two credentials, or a reviewer
on the plan's environment, so the plan waits on its own approval; a
production value in a repository-wide variable a staging job can read; a
cloud access key stored as a secret; an environment with no
deployment-branch policy, so a pushed branch declaring `staging` gets
staging's role; a deployer trust that names the environment and not the
branch, or the repository by its mutable name alone. (The approval
itself is DEL-38.)

**Severity.** high
