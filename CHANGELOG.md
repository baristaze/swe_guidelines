# Changelog

All notable changes to this repository are listed here. Releases are
tagged `vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps
which number.

## Unreleased

## 0.17.0 (2026-09-20)

Three outside readings of the operational half of the guideline: that
telemetry is treated as a convention rather than as structure, that
resilience under degradation is thin, and that the tenant fence claims
more than convention and a signature test establish. Minor: rules are
added and sharpened, none is removed or reversed. Telemetry becomes a
section of its own and a handoff carries the request that caused it,
so a reader holding a request id follows the work past the queue it
crossed. Every call gains a bound and a named behaviour at the bound,
each stated where the call is made and gathered by "Resilience by
Design", with the numbers left to the system that runs it. The tenant
fence keeps its decision and states what actually holds it: the
predicate is the fence, the case that presents another tenant's
identifier is its evidence, and the suite is verified against a
deliberate breach.

### Added

- `architecture.md`, "Composition by decoration": an open breaker
  answers the way the dependency's own failure answers. It decorates
  an interface, and a caller cannot tell what is behind one, so where
  the interface says a failure is an answer, as "Cache" says an
  unreachable backend is a miss, the breaker gives that answer at once
  instead of raising. It declines to pay the timeout, never to keep
  the contract. Where the failure is an exception the refusal is still
  the unavailable shape. Lens `CON-23`.

- `architecture.md`, "Namespace Shape" and "Tests": a signature is not
  a guarantee, and the case that tries the breach is what says the
  tenant is used. The test that enumerates the exceptions to the
  `org_id`-first rule reads signatures, and the fence itself lives in
  one place, the `WHERE` clause of the query, so a method that takes
  the tenant and leaves the predicate out of its body passes every
  check made on signatures. "Tests" names the coverage the
  cross-tenant cases owe: reads and writes, the list and the page, the
  bulk write, and the paths that return early or raise, over memory
  and over the engine, with a new storage method arriving with its
  case. Lens `CTX-30`; `CTX-12` now says the enumerating test reads
  signatures and nothing more. `arch-scaffold-entity` generates the
  cross-tenant case per method of an entity's storage interface,
  `arch-scaffold-worker` the one its tenant-carrying method owes,
  `arch-scaffold-new` names the enumerating test as a signature check,
  and the shared scaffold conventions carry the rule.
- `architecture.md`, "Tests": the isolation suite is verified against a
  deliberate breach. A tenant predicate is taken out of one query, the
  suite is run and fails, the predicate is put back, and what that run
  showed is recorded, the query it was run against and what the suite
  reported, because a negative control nobody ran is a claim and not
  evidence. Which mechanism takes the predicate out is the project's
  choice; that the control is run and recorded is not. Lens `CTX-31`.
- `architecture.md`, "Intra-Service Communication": a key per issuer is
  attribution, never containment. One signing key stays one trust
  domain, and the system that gives each issuing process a key of its
  own learns which process signed; what bounds an issuer is a
  declaration of what it may assert, the tenants it may name, the
  roles it may carry, and the principals it may speak for, which the
  callee reads before the gateway rebuilds `OpContext` and against
  which it refuses a credential that reaches past. The declaration is
  configuration of the callee, so an issuer cannot widen its own reach
  by minting a wider token. Lens `NET-34`; `NET-27` keeps the token's
  shape.
- `architecture.md`, "Correlation Across a Handoff": a handoff carries
  the request that caused it, so one id joins the request, the row it
  wrote, the item it queued, and the run that followed. The stage a
  worker mints for a claim is a new request that names the causing one
  in a field of its own, both are logged, and a span raised on the far
  side links to the causing trace rather than becoming its child,
  because a durable queue holds an item past the end of the request
  that filled it. `WorkItem` gains `request_id` and `traceparent`, and
  `OutboxRow` gains `traceparent` beside the request id it already
  carried: the trace context as the header spells it and not as a
  `trace_id`, since an id names a trace and only the header carries
  what a later span links to. A traceparent is empty when the causing
  request ran with no tracer configured, and the far side then starts
  a trace of its own. The relay takes both fields off the outbox row
  and a direct create off its caller's context, and `RequestContext`
  gains `caused_by_request_id`, which the claim's transition fills
  from the item. "Logs" says what every line carries: the service and
  the environment, which is what lets one query read across processes,
  the request id, and the causing request where a handoff supplied
  one. Lenses `CTX-29`, `ASY-29`, `DEL-39`; `CTX-02`
  names the field the request stage gained, `OM-03` the field the
  outbox row gained; `arch-scaffold-new`, `arch-scaffold-worker`, and
  the shared scaffold conventions carry the shape.
- `architecture.md`, "Resilience by Design": a bound on every call and
  a named behaviour at the bound, each rule stated beside the
  mechanism it bounds and gathered into a section that is the sibling
  of "Scalability by Design". "Database Roles": a role's pool declares
  its size and the bound on waiting for a connection, both from
  settings, and a checkout past the bound fails rather than queueing
  without end, with the size chosen against the process's own
  concurrency. "A Storage Impl": a statement carries a deadline, so
  the one dependency the timeout doctrine never named is bounded like
  every other call, and a hung query costs one call and not a held
  connection. "The Gateway": `/readyz` answers under a deadline of its
  own, shorter than the interval it is polled on, and a timeout is a
  negative answer and never a missing one; a process bounds what it
  has in flight and refuses past the bound at once, which is the
  process defending itself and fails closed, where the rate limit
  beside it is per-subject fairness and fails open. "Composition by
  decoration": a breaker counts consecutive failures, refuses for a
  cool-down past a bound from settings, and lets one call through to
  decide whether to close, because a dependency that is down turns
  every call into a full timeout and the timeouts exhaust a pool.
  "Direction of Calls": the retry we send is classified, bounded in
  count, spaced by a delay that grows and carries jitter, and never
  stacked. "Cache": a degraded answer is declared where it is chosen,
  so nothing silently substitutes a stale answer for a fresh one.
  "Exceptions" gains the `Unavailable` shape with the status it
  carries, so an open breaker, a refused admission, and a backend that
  is down present alike on both exception roots. Every rule states the
  shape of a bound and never a number: "What This Document Does Not
  Cover" keeps excluding the tuning of deadlines and retry budgets and
  now excludes the numbers an admission bound is set to. Lenses
  `STO-27`, `NET-32`, `NET-33`, `CON-23`, `ASY-30`; `NET-26` carries
  the statement deadline, `NET-10` the bounded readiness probe, and
  `DEL-18` the new shape. The scaffolds carry the shape:
  `arch-scaffold-new` writes the shape exception and a storage root
  whose pools declare a size and a checkout bound and whose sessions
  carry a statement deadline; `arch-scaffold-service` writes the
  admission middleware, the bounded readiness probe, the breaker the
  container wires in front of a remote service impl, and the settings
  behind all three; `arch-scaffold-app` puts the one retry in the
  transport client, in both languages, with nothing retrying above it;
  and `arch-scaffold-worker` sets a worker's capacity against the pool
  behind it.

### Changed

- `architecture.md`, "Storage Principles": the row-level security
  bullet is argued on strength and not on cost alone. The predicate in
  the query is the fence and the cross-tenant cases are its evidence;
  a database policy is the second fence, and independence is what a
  second fence buys, since a policy and a predicate fail in different
  ways and a policy still constrains a query whose predicate was left
  out. The decision stays application enforcement, and both costs stay
  stated: the tenant set per statement on a pooled connection, which
  is the same discipline in a second place, and a policy that misfires
  returning nothing instead of failing loudly. The bullet names the
  trigger at which a system takes the second fence, a role held by a
  process the team does not write or a commitment requiring
  enforcement the application cannot vouch for, and a project that
  wants the database to hold it still records the decision.
  "Namespace Shape" no longer reads the `org_id` parameter as
  enforcement in itself. Lens `CTX-09`.
- `architecture.md`, "Telemetry": "Logs", "Traces and Metrics", and
  "Error Tracking" leave "Cross-Cutting Conventions" for a top-level
  section of their own, placed in front of it, so that what every
  process emits reads as structure and not as a convention added at
  the edge. The three subsections keep their text, and
  "Cross-Cutting Conventions" keeps "Exceptions", "Configuration",
  "The App Container", "Records of Decisions", and "Tests". Lenses
  `CTX-07`, `DEL-19`, `DEL-20`, `DEL-27`, and `DEL-35` cite the new
  section, the `delivery` group's home sections name it, and
  `arch-review-delivery` is regenerated.

## 0.16.0 (2026-09-20)

A second reading of 0.14.0, triaged against 0.15.0, for the places
where the guideline states a rule in one section and states it
differently in another, and for the lenses and scaffolds that carried
the older spelling forward. Minor: two absolutes are softened to what
the guideline already does elsewhere, one interface loses a return
value, and the derivations are brought back onto the sentences they
restate.

### Changed

- `architecture.md`, "Queues": `send()` returns `None`. It returned a
  broker id two hundred lines under the paragraph that refuses one
  from `publish()`, for a reason that holds for both: the observable
  id is the producer-set `idempotency_key` the body carries, and a
  broker id carries no durable meaning across retries and replays. The
  `receipt` on a `QueueMessage` is named as what it is, the handle of
  one delivery that `delete` and `change_visibility` take.
- `architecture.md`, "The Work Queue": a duplicate `idempotency_key`
  on an enqueue is reported the way an existing id is, never raised,
  and the relayed enqueue presents the outbox row's id as the item's
  key. "Database Roles" has said since 0.1.0 that relaying twice is
  harmless and "A Storage Impl" that a key collision surfaces as a
  report, while this section called the same collision a conflict; and
  with a fresh item id per relay run, nothing said what the second run
  met. Lenses `ASY-25`, `CON-17`; `arch-scaffold-worker` carries it.
- `architecture.md`, "Namespace Shape": a cross-tenant read returns
  `tuple[UUID, Entity]` unless the entity carries `org_id` itself, as
  the outbox row and the `Event` do for exactly this reader. The
  tuple was stated flatly two hundred lines before the two rows that
  do not use it. Lens `CTX-12`.
- `architecture.md`, "Naming Entities": an append-only record's birth
  time is the one in its id. The audit swimlane is defined as who did
  what, when, from which app, while the rule beside it gives an audit
  entry no `created_at`; every id is a `uuid_v7` with the millisecond
  in front, so the "when" costs no column and the two read together.
- `architecture.md`, "Shutdown": a draining worker returns its work
  item to the queue. "Long-Running Orchestrations" uses "record" for
  the row a work item advances, and the queue holds the item.
- `architecture.md`, "Local: Docker Compose": the steps the four
  shortcuts wrap are named, `make infra-up`, `make infra-down`, `make
  infra-reset`, `make migrate`, `make seed`, and the start script,
  because a gate wants the dependencies and not a running
  application. The scaffolds have run `make infra-up` since 0.1.0
  against a section that defined only `up`, `down`, `reset`, and
  `urls`. `arch-scaffold-new` and `arch-upgrade-deps` carry it, the
  latter reaching for `make reset`, which also seeds and starts the
  application, where it wants the volumes recreated and nothing else.
- `lenses/context.md`: `CTX-16` names the relay's two handoffs, the
  event append and the work enqueue, which "Operations Without a
  Principal" has called three operations of that kind since 0.14.0,
  and `CTX-01` defers the whole set to it; `CTX-12` allows the row
  that carries its own tenant; `CTX-05` lists the worker's claim once,
  as an operation that asks a transition, and names the service
  context a sweep asks for.
- `lenses/contracts.md`: `CON-03`'s title stops stating the absolute
  its body dropped in 0.14.0; `CON-17` names the work item, the one
  row "Shape of an Operation" exempts, so the lens no longer flags the
  signing `OM-13` requires; `CON-02` flags a fallback body, not the
  keyword-parameter default "Parameters" requires.
- `lenses/om.md`: `OM-12` judges the layer the rule names, an id
  minted inside a storage impl or assigned by the database, and says
  that the id a creating `POST` mints before its marker is the
  gateway's protocol.
- `lenses/storage.md`: `STO-11` names `FeedIdentifiableMixin` beside
  the other two identity mixins, which `STO-14` and "Defining ORM
  Classes" both require of a feed table; `STO-03` stops listing a
  single-row compare-and-set among the primitives that need a named
  atomic method, which "Storage Principles" reserves for an invariant
  two rows hold together.
- `lenses/network.md`: `NET-02`'s Principle carries the qualifier its
  own Violation carries, that a call stays in-process wherever the
  process holds the callee's code and roles; `NET-09` scopes the key
  to the tenant and the principal, as "The Gateway" does, and points
  at `NET-31` for what a create that issued a secret stores; `NET-07`
  points at infra's exception root by that name; `NET-04` and `NET-08`
  name the lens that rates the same breach `high`.
- `lenses/async.md`: `ASY-25` carries the reported duplicate key and
  the key a relayed enqueue presents; `ASY-26` covers the hand-back of
  an item a worker finds is not its to run, which `ASY-25` deferred to
  it and it did not name; `ASY-18` says the draining worker returns its
  work item, the word "Shutdown" now uses.
- `lenses/delivery.md`: `DEL-23` asks for an ADR where the guideline
  does, and names the docstring and the enumerating test as the
  mechanism for an enumerated exception; `DEL-21` excepts the local
  secrets impl, whose backend is the environment, and points at
  `DEL-31`; `DEL-09` flags a service or worker distribution with no
  console entry point, not every distribution in the tree; `DEL-30`
  puts the object store in the policy when the app moves bytes
  through presigned URLs, in either direction, as "API Access" says.
- `lenses/README.md`: the reserved `high` set names a credential or a
  secret reaching somewhere it is not held, which is what `DEL-06`,
  `DEL-30`, `CTX-18`, `CTX-19`, `NET-25`, and `NET-31` rate; `Source`
  allows the section alone, which `AGENTS.md` and the checker have
  allowed since 0.14.0; the deferral rule reads the way its own
  example is written; and `make lenses` is credited with the limits it
  checks.
- `skills/arch-scaffold-worker`: the outbox relay gains its
  `work.<kind>` branch here, with the test that a row relayed twice
  leaves one item, so `enqueue_relayed` has the caller
  `arch-scaffold-new` says arrives with this skill; the claim asks the
  tenancy manager for the `OpContext` it returns, since "Stages" says
  only a transition produces a stage.
- `skills/arch-scaffold-service`: `settings.py` carries the
  `namespaces` setting the first form of a split rests on, and
  `all_routers()` reads it; the second service moves the first's
  gateway package into a root `gateway/` distribution with its own
  `pyproject.toml` and workspace membership, and writes no gateway
  package of its own.
- `skills/arch-scaffold-new`: the initial migrations cover the
  `activity` role as well as `core`, since the skill declares the
  events and audit tables and its own step 6 runs `make migrate` and
  `make migrate-check` over every role; the Makefile gains
  `infra-reset`.
- `skills/_shared/scaffold-conventions.md`: a scaffold pins the
  release a patch release already sits behind, as "Versions" states,
  so a scaffold run on a `.0` day pins what `arch-upgrade-deps` would
  keep.

## 0.15.0 (2026-09-20)

A reading of 0.14.0 by a second reviewer, for the places where a rule
holds only until two attempts, two releases, or two rows meet. Minor:
each rule is sharpened where its own safety argument stopped short.

### Changed

- `architecture.md`, "Shape of an Operation" and "The Gateway": the
  re-mint of a secret on a rerun is conditional on the attempt the
  marker holds, like `finish` and the release. It is the one write of
  a rerun that changes what is stored, so without the guard an
  attempt whose lease a retry took over could overwrite the digest of
  the secret that retry had already returned, and refusing its
  `finish` afterwards restores nothing. Lenses `NET-24`, `NET-25`,
  `CON-21`; `arch-scaffold-service`, `arch-scaffold-new`, and
  `skills/_shared/scaffold-conventions.md` carry it.
- `architecture.md`, "The Gateway": the pending lease runs from the
  attempt and never from the marker. The attempt token is a
  `uuid_v7`, so it carries the moment it was minted; measured from
  the marker's age, a marker handed from one attempt to the next was
  stale the instant it changed hands, and a third attempt could take
  it over at once. A released marker holds no attempt and is taken
  over at once. Lens `NET-24`; `arch-scaffold-service` carries it.
- `architecture.md`, "Namespace Shape", "A Storage Impl", "Database
  Roles", and "The Work Queue": a `core`-role write takes
  `outbox_rows: tuple[OutboxRow, ...]`, not one row. "The Work Queue"
  has said since 0.14.0 that work following a core write rides a
  second outbox row of that write, of kind `work.<kind>`, which a
  one-row signature had nowhere to put. Lenses `STO-16`, `STO-20`,
  `ASY-25`, `NET-22`; `arch-scaffold-entity`, `arch-scaffold-new`,
  and `skills/_shared/scaffold-conventions.md` carry it.
- `architecture.md`, "Translation" and "Migrations": a field added to
  a stored JSON shape is staged across two releases, read in one and
  written by the next. `extra="forbid"` makes an unknown key a read
  error, and a rollout runs two releases at once, so a field written
  before its readers are out is an unreadable row in the process
  still serving beside them. Lens `STO-25`.
- `architecture.md`, "Immutability": the `FrozenMapping` validator
  descends, wrapping a nested mapping the same way and turning a
  nested list into a tuple. A `MappingProxyType` freezes only the
  mapping it wraps, and a payload of dumped JSON, which is what the
  outbox row and the work item carry, is nested. The field-shape
  rules move out of the Python tip into the prose beside it, which
  keeps the tip under the paragraph limit. Lens `OM-17`;
  `skills/_shared/scaffold-conventions.md` carries it.
- `architecture.md`, "Stateless vs Stateful Services": a lightly
  stateful service holds the open socket, the `OpContext` its ticket
  produced with the expiry that bounds it, the subscriptions, and a
  bounded buffer; what it never holds is session data or accumulated
  business state. "Stages" has said since 0.1.0 that a socket holds
  the context its ticket produced, while the principle here read
  "never session data or user context". Lens `NET-05`.
- `architecture.md`, "The Gateway": a creating `POST` is one that
  writes a durable row, whether it answers `201` with the row or
  `202` with the id of work now running. Selecting the routes by
  their status left the `202` submissions of "Push-First Apps" with
  no idempotency key, though a client that never saw the answer
  retries them the same way. Lens `NET-09`;
  `skills/_shared/scaffold-conventions.md`, `arch-scaffold-service`,
  `arch-scaffold-entity`, and `arch-scaffold-new` carry it.

## 0.14.0 (2026-09-19)

A reading of 0.13.0 by a second reviewer, for the places where the
guideline states one rule and a lens or a scaffold states another.
Minor: two absolutes lose the exceptions the guideline had already
granted them, and the work queue's enqueue is settled the way
"Database Roles" had always described it.

### Changed

- `architecture.md`, "The Work Queue" and "Database Roles": a work
  item that follows a core write rides that write's outbox row and
  the relay enqueues it, as "Database Roles" said and nothing else
  did. The relay dispatches on the row's `kind`: an entity change
  appends the `Event` and publishes `ENTITY_CHANGED`, a row of kind
  `work.<kind>` enqueues the item and publishes `WORK_AVAILABLE`.
  Both take `(org_id, row)` and read the actor off the row, so
  "Operations Without a Principal" now names three operations of that
  kind, not two. A work item that follows no core write stays a
  direct manager create under a context, and the two callers meet one
  insert under one idempotency key. `arch-scaffold-new`,
  `arch-scaffold-namespace`, `arch-scaffold-entity`, and
  `arch-scaffold-worker`, which gains `enqueue_relayed(org_id, row)`,
  carry it; lenses `STO-20`, `ASY-25`.
- `architecture.md`, "The Work Queue": every write to a work item
  after its enqueue signs `updated_by` with `EMPTY_UUID` and never
  from the context, the one named exception to the copy of "Shape of
  an Operation". "Naming Entities" and "Identifiers" have said the
  platform is the actor of a work item's claims and completions since
  0.1.0, while the path that performs them stamped the enqueuer.
  `created_by` is who asked for the work, `updated_by` is the
  machinery that ran it. `arch-scaffold-worker` and lens `OM-13`
  carry it.
- `architecture.md`, "Multiple impls per interface": the rule is that
  every interface can be satisfied without the technology behind it,
  and two impls are the usual shape of that rule, not the rule
  itself. Stating "at least two impls" flatly and then granting a
  manager and a service interface one each left `CON-03` scoping its
  Violation to storage, infra, and integration interfaces to stay
  true. `CON-03` now judges the satisfiability.
- `architecture.md`, "The Operator Context": a manager operation that
  acts for a principal takes exactly one of `OpContext` and
  `OperatorContext`. The old absolute, "tenant managers take
  `OpContext` and nothing else", flagged the guideline's own
  `TenancyManagerInterface` and every operation of "Operations
  Without a Principal". A stage below is a parameter only where the
  stage is what the operation establishes or what it has none of,
  which is `CTX-21`. Lens `CTX-20`.
- `architecture.md`, "Composition by decoration": the cache impls are
  `CacheLocalImpl`, `CacheCloudImpl`, and `CacheMixedImpl`. The
  snippet led with the technology, which is what `CON-03` flags two
  hundred lines above it.
- `architecture.md`, "Realtime at the Edge": the default shape is one
  stream per tenant and a socket is subscribed to its tenant's stream
  when it opens, so the `subscribe` frame is what a client sends when
  the product keeps more than one stream. The section spoke of "the
  streams its client subscribed" and of one stream per tenant without
  saying how the two met.
- `architecture.md`, "Monorepo Folder Structure": the gateway package
  appears once. The tree showed it both at `gateway/` and inside
  `services/api/`, two sections after "The Gateway" says it is moved,
  not copied.
- `lenses/storage.md`: `STO-04` defers the cross-role join to
  `STO-17`, which rates the same breach `high`, and drops the
  "Database Roles" citation it no longer needs; the group intro says
  the outbox row is relayed at once or by the sweep, the cheaper
  first step `STO-20` itself allows.
- `lenses/async.md`: `ASY-24` flags repeated heartbeat failures that
  leave a worker claiming, which is what "Shape of a Worker" says,
  not the first failure.
- `lenses/network.md`: `NET-04` flags a warm cache or rollup that is
  not rebuilt at boot; "Stateless vs Stateful Services" allows a
  per-process rollup that is.
- `lenses/om.md`: `OM-02` grants `org_id` to an entity any reader
  without a tenant takes, the rule "Defining ORM Classes" states, not
  only one an operator reads; the guideline's own `OutboxRow` and
  `Event` carry it for the context-less relay.
- `lenses/contracts.md`: `CON-21` names the recovery "Shape of an
  Operation" gives, a replay that answers with the row and no secret
  and a client that revokes and reissues, in place of a client left
  with nothing.
- `lenses/context.md`: `CTX-06` cites the "OpContext" section, where
  the immutability rule is written, rather than "Stages", where only
  its last sentence lives.
- `lenses/delivery.md`: `DEL-07` names `specs/`, which the folder
  tree and `docs/adopting.md` both require; `DEL-18` looks for
  `om/src/<root>/om/exceptions.py`, where the tree puts it.
- `skills/arch-scaffold-namespace`: `<Ns>` is the namespace in the
  singular, in CamelCase, which is what "Namespaces as Swimlanes" and
  `skills/_shared/scaffold-conventions.md` require of every interface
  and getter built from it. No skill said how it was singularized.
- `docs/adopting.md`: the headings carry no numbers and the one
  positional cross-reference names its section by title, as
  `AGENTS.md` requires of the docs; the review-and-fix pass is
  `arch-scaffold-new`'s, and the scaffolds that add to an existing
  tree run the repository's gate and leave the review to
  `arch-review-full`.
- `AGENTS.md`: a lens cites a section alone or a section and a
  subsection, which is what `lenses/README.md` and
  `scripts/check_lenses.py` allow and what 24 lenses do.
- `SECURITY.md`: the three tools the gate fetches at pinned versions
  are named, `pytest`, `markdownlint-cli2`, and
  `@anthropic-ai/claude-code`, in place of a claim of one.

## 0.13.0 (2026-09-19)

A reading of 0.12.0 by a second reviewer, plus two changes asked for
on top of it. Minor: the manager exception 0.12.0 added is reversed,
and the changelog names the reversal; a queue parameter is removed;
the rest is where a rule was stated twice, in two places that had
drifted, or read as a forward reference a first reader cannot follow.

### Changed

- `architecture.md`, "Multiple impls per interface": the manager
  exception 0.12.0 introduced is reversed. Every interface can be
  satisfied without the technology behind it. A manager over nothing
  but its own storage is satisfied already, by wiring it over the
  memory roots; a manager that fronts something a caller cannot
  conjure, a payment processor, a carrier, a model provider, gets a
  memory impl of its own, so every caller above that namespace runs
  with no account, no network, and no sandbox. The same paragraph now
  says how a service interface pairs, since both of its impls are
  real and its twin is the manager's, which is what "Direction of
  Calls" has said all along. Lenses `CON-03`, `CON-14`.
- `architecture.md`, "Naming Entities": the system-row passage is
  three short paragraphs instead of one dense one, and the two class
  declarations move to where they do their work, `OutboxRow` to "The
  Storage Layer" ("Namespace Shape") and `IdempotencyMarker` to "The
  Network Layer" ("The Gateway"). Both used `AppContext` and
  `FrozenMapping` hundreds of lines before either was defined. The
  rule stays where it belongs, on the mixins: a platform row composes
  `Created` and carries no `created_by`. Lens `OM-03`.
- `architecture.md`, "Queues": `send` loses its `dedup_id` parameter.
  The section said in the same breath that a queue does not
  deduplicate and that the interface takes a deduplication id; a
  best-effort window is not a guarantee, and the durable answer is
  the consumer's.
- `architecture.md`, "The Gateway": every service has a gateway,
  because a callee rebuilds `OpContext` from the internal credential
  its caller minted, so at the second service the package moves out
  of the API process into a `gateway/` distribution every service
  imports. `arch-scaffold-service` already moved it there; now the
  guideline says so, and the folder tree and `DEL-07` carry it.
- `architecture.md`, "Records of Decisions": naming the near miss a
  rule rules out ("X, never Y") is part of the rule and stays; what
  the document does not carry is a survey of the alternatives it
  weighed. `AGENTS.md` and `CONTRIBUTING.md` carry the same qualified
  form, and `CONTRIBUTING.md` no longer says the guideline carries no
  product vocabulary flatly: it names its technologies on purpose,
  and `make leaks` is a regression guard for one origin's words.
- `architecture.md`, "Web Services as Scalability Units": a call into
  another namespace is made by the service impl through the callee's
  `ServiceInterface`, not by a router, which imports no managers at
  all. Lens `NET-02`.
- `architecture.md`, "Composition by decoration": the cache example is
  the `CacheInterface` of "Cache", `org_id` and TTL included, instead
  of a second `KeyValueInterface` that broke the infrastructure rule
  it sat under.
- `architecture.md`, "Migrations": the per-role metadata-versus-schema
  check is `make migrate-check`, run against the local stack after
  migrating and in CI's integration job; the folder tree names the
  target and gives infra its own `exceptions.py`, which "Exceptions"
  has required all along.
- `lenses/README.md`: a rule belongs to one group and one lens; the
  line between two groups is the paragraph at the top of each file,
  not a table that was never there; `Source` is where a rule is
  written down and the group is who judges it; and where two lenses
  sit next to one breach, the narrower one names the other instead of
  flagging it twice.
- Eleven pairs of lenses judged one breach twice, sometimes at two
  severities, and each pair now has a line between it: `CTX-01` and
  `CTX-16`, `CTX-10` and `CTX-11`, `CTX-12` and `CTX-17`, `STO-06` and
  `OM-12`, `STO-20` and `NET-22`, `STO-18` against `NET-29` and
  `DEL-07`, `CON-12` and `CON-14`, `ASY-17` and `ASY-23`, `ASY-09` and
  `NET-23`, `NET-15` and `DEL-15`, `CON-13` and `DEL-01`.
- Lenses held back to what the guideline states: `STO-03` carries "a
  unique membership" again and stops calling a `version` field a lock
  primitive, `ASY-19` names the sweep's full duty list, `STO-11`
  names `updated_by`, `DEL-05` says "at least two", `CON-21` carries
  the secret-issuing create's exception, `OM-14` grants an
  aggregate's name to a storage interface only, and `ASY-27` no
  longer asks every synchronous chain for an expiry on its first
  step.
- `skills/_shared/scaffold-conventions.md`: the contract cases live
  under `tests/contracts/`, as the folder tree, "Tests", and three
  scaffolds already said; the shared "After writing" step names
  `make infra-up`, `make migrate`, `make migrate-check`, and
  `make openapi`, and the manager's memory twin is named where a
  namespace fronts an external dependency.
- `allowed-tools` names what a body runs, through the procedures a
  skill delegates to: `arch-scaffold-namespace` gains the four make
  targets `arch-scaffold-entity` runs, `arch-scaffold-worker` gains
  the three its migration needs, and `arch-scaffold-new` gains
  `pnpm --filter`.
- `skills/arch-scaffold-new`: `## Changed` is a table like every
  other scaffold's, and the append operations are named after their
  entities, `append_event` and `append_audit_entry`.
- `README.md`: the skill table carries "pure rules" and
  "substitutions", which the two generated skills' own descriptions
  already named. `CHANGELOG.md`: 0.9.0 is dated the day it shipped.

## 0.12.0 (2026-09-19)

Two readings of 0.11.0 against itself, one in this repository and one
by a second reviewer, for the places where one file names a shape
another file cannot hold. Minor: one rule is narrowed where it never
applied and one is widened, and the changelog names both; the rest is
where a rule was stated twice and the two statements had drifted.

### Changed

- `architecture.md`, "Multiple impls per interface": a manager
  interface has one impl, named as the exception to the two-impl
  rule, because a manager carries no technology of its own and the
  pair it runs over is the storage and the infrastructure under it.
  This narrows "an interface has at least two impls", which every
  manager in the document already stood outside. Lens `CON-03`.
- `architecture.md`, "Client Rendering": the bundle also talks to the
  error tracker when one is configured, so the list that section
  closes holds the origins "API Access" and "Error Tracking" already
  require of it; a browser app reports errors like every other
  process. This widens "and nothing else". Lens `DEL-12`.
- `architecture.md`, "Queues": the capability interface is
  `QueuesInterface`, like the `TopicsInterface` and `BucketsInterface`
  beside it and the `get_queues()` getter that returns it.
- `architecture.md`, "Monorepo Folder Structure": the OM's `tests/`
  tree shows `contracts/`, the storage cases the unit and the
  integration suites both run, which "Tests" describes and every
  scaffold writes.
- `lenses/README.md`: the group table names subsections by their
  titles, "Service Interfaces and Impls", "The App Container",
  "Idempotency on the Consumer Side", and "Long-Running
  Orchestrations", so the two review skills generated from those rows
  carry the titles too. `DEL-12` drops the operator-console sentence
  `DEL-16` owns.
- Skills: `arch-scaffold-new` gives the infra distribution its own
  `InfraException` root and has its cloud impls raise it, since infra
  imports nothing from the OM; its `opcontext.py` row no longer names
  a `build_context` helper, because nothing but a transition builds a
  stage above the request stage. `arch-scaffold-entity` fills the
  outbox payload with the entity's own dump, as "Shape of an
  Operation" writes it.
- `docs/adopting.md`: the scaffold's review pass is described by what
  it does, closing every high finding before it hands the tree over,
  not by a count of findings.
- `architecture.md`, "Migrations": the check that the ORM metadata and
  the migrated schema agree needs a migrated database, so it runs in
  CI's integration job beside the downgrade-then-upgrade, not in the
  fast gate "Tests" defines as memory-only. Lens `STO-24`.
- `architecture.md`, "The Gateway": the error envelope carries an
  `InfraException` too, with the status and the code the exception
  carries, as "Exceptions" already asks of the gateway and the worker
  loop alike. Lenses `NET-07`, `DEL-18`; the service scaffold
  registers both handlers.
- `architecture.md`, "Identifiers": the example of a required
  reference no tenant and no person owns is `updated_by` on an item
  the platform claimed, since "Naming Entities" gives a bookkeeping
  row no `created_by` at all. Lens `OM-13`.
- `architecture.md`, "Local: Docker Compose": the `devx` profile is
  started by the developer commands below it, `make up` among them,
  and never by CI. Lens `DEL-33`.
- Lenses read back against the sections they restate: `CTX-01` names
  the identity and the operator stage a first argument takes; `CTX-22`
  admits the scope another scope is built on, as `TenantScope` is;
  `NET-12` says intra-service calls need no TLS rather than that the
  traffic is plain; `CON-17` says which stamp belongs to a create and
  which to an update; `ASY-06` leaves the rate-limit counter, the
  generation, and the liveness beat cache-only by design; `STO-21`
  purges a soft-deleted row after its retention period; `STO-03`
  states the criterion the guideline states, an invariant two rows
  must hold together; `DEL-33` asks for a dashboard where the local
  image ships a console.
- Lens sources and owners: `CON-09` cites Storage Root for the getters,
  `NET-01` the section that shapes an app-specific service, `STO-04`
  Database Roles for its cross-role clause. One owner per rule:
  `ASY-25` leaves the hand-back that spends no attempt to `ASY-26`,
  `DEL-31` the approval on the plan to `DEL-38`, and `CON-14` the
  router's own call to `CON-15`.
- Skills: `arch-scaffold-new` writes five mixins, not four, and
  re-exports `StorageInterface` from `storage/__init__.py`, since the
  guideline puts the root at `<root>.om.storage`; `arch-scaffold-service`
  and `arch-scaffold-worker` say what `--container` does, the second
  compose file, with `scripts/dev.sh` starting the process on the host
  either way; the worker's `/healthz` answers from the loop's own
  state with no I/O, its `AppContext` version is a version, and its
  `WorkItem` row no longer adds the `claim_token` The Work Queue
  declares; the shared conventions name `create_<entity>` and
  `append_<entity>` beside the reads and the write.
- `allowed-tools` names only what a body runs: `Bash(git diff:*)`
  leaves the six scaffolds and the upgrader, `uv init` and `uv add`
  leave `arch-scaffold-new`, which now names `git rev-parse` where it
  refuses to run inside a repository.
- `README.md` and `docs/adopting.md`: a scaffold reads the version it
  shipped with and names it in its output; a review skill does not,
  and the docs no longer promise it.

## 0.11.0 (2026-09-19)

An outside review of 0.10.0 read the guideline, the lenses, the skills,
and the repository's own docs against each other and found the places
where they say different things. Every check passed and the text still
contradicted itself, so this release is the pass that makes the four
agree. Minor: rules are sharpened and shapes gain the fields the prose
already assumed; the sentence that had the router translate is
reversed, the service impl translates and the router binds, and the
changelog names it.

### Changed

- `architecture.md`, "Naming Entities" and "Scopes": `OutboxRow`
  carries the provenance of the write it announces, `actor_id`,
  `request_id`, and `app`, and still no `created_by`; the provenance
  helper builds it, the flagship snippet calls the helper, and the
  relay's `(org_id, row)` signature is explained by fields the row
  now has. Lens `OM-03`.
- `architecture.md`, "Shape of a Worker": `WorkItem` declares the
  `claim_token` the fences condition on; `claimed_by` is the worker's
  name for an operator and never a fence. On enqueue the manager's
  copy stamps the actor, the status, and the attempts, clears every
  claim field, and leaves the id and the timestamps as constructed,
  as every create does. Lenses `ASY-16`, `ASY-25`, `ASY-26`.
- `architecture.md`, "Realtime at the Edge": the `Event` carries
  `actor_id`, as the hint always did; an audit entry is the same
  shape plus the request id and the app; a socket handler filters by
  tenant and by the streams its client subscribed, never by kind
  within a stream, so a subscribed stream arrives whole. Lenses
  `NET-16`, `NET-22`, `NET-30`.
- `architecture.md`, "Service Interfaces and Impls": a router declares
  the route and its dependencies, calls one operation of its service
  impl, and returns what it returns; the service impl translates, it
  builds the entity from the request, calls one manager, and projects
  the result onto a view; a partial update is the impl's translation.
  This reverses "a router translates". An app-specific service in the
  single-process start is a router module and its service impl. Lenses
  `CON-15`, `CON-22`, `CTX-08`, `NET-01`; the entity and service
  scaffolds wire every route through the service impl.
- `architecture.md`, "Namespaces as Swimlanes": the naming rule stated
  once: interfaces and getters after the namespace in the singular,
  operations after the entity, work handlers `<Kind>HandlerImpl`, the
  manager impl in `impl/manager.py`; every example follows it. Lenses
  `OM-14`, `STO-10`, `CON-03`; the scaffold conventions restate it.
- `architecture.md`, "Local: Docker Compose": `make up` starts every
  dependency and the `devx` profile in containers and the application
  on the host through the one start script; the second compose file
  is never the default. Lens `DEL-04`.
- `architecture.md`, "Monorepo Folder Structure": the tree shows the
  `outbox`, `idempotency`, and `work` namespaces and the `services/`
  and `impl/` folders of the API process; every path in the document
  is spelled the way the tree spells it; the Makefile comment lists
  the four shortcuts and `seed`.
- `architecture.md`, smaller: four index rules, not three (`STO-14`
  is the first three, `STO-26` the fourth); two write primitives on
  `PgStorageBase`; observability and a feature flag SDK are the two
  vendor APIs used directly; the bundle also talks to the object store
  through a presigned URL and the CSP names that origin and the error
  tracker's (`DEL-12`, `DEL-30`); the identity provider is an
  integration with a twin, in the `integrations/` distribution
  (`CTX-28`); a worker serves `/healthz` beside `/metrics` so every
  image's healthcheck holds (`DEL-35`); the pure-rules principle names
  the statement-level spelling it allows (`OM-15`); the sweep's list
  names the outbox relay and the purge (`ASY-19`); the liveness key
  lives under `CacheScope.WORKER_LIVENESS`; the closing section no
  longer disclaims what the text covers.
- Lenses: `NET-11` drops the constant-time comparison 0.8.0 removed
  from the text; `CTX-05` admits a transition that asks the tenancy
  manager, as a claim does; `CON-17` no longer flags the actor the
  manager sets from the context; `CTX-01` gives a scope to helpers
  below the managers, never to a manager operation; `NET-27`,
  `NET-20`, `ASY-19`, `CTX-27` cite the subsections that state their
  rules. One owner per rule: `CON-08`, `CON-18`, `NET-06`, `ASY-03`,
  `NET-26`, `NET-03`, `OM-02`, `OM-11`, `NET-13` are narrowed to the
  side their group's header claims and point at the owner; the async
  group owns the whole work queue, table and statements included.
- Skills: the service scaffold writes `deployment/realtime-timeouts.json`
  when absent, so the test it adds has a file to read before the
  portal exists; `arch-scaffold-new` writes the one-head-per-role
  migration test and its marker manager has the release and the
  take-over; the worker scaffold adds `CacheScope.WORKER_LIVENESS`
  when absent; every scaffold cites its sections in guideline order;
  `allowed-tools` names only what a skill runs, and the entity
  scaffold runs `make infra-up`, `make migrate`, `make migrate-check`,
  and `make openapi` where the guideline needs them.
- `scripts/check_skills.py`: every `Bash(make <target>)` in
  `allowed-tools` is a target the body runs; a bare `Bash` is refused.
  `scripts/check_leaks.py` refuses an em-dash in `scripts/` and
  `tests/` too. `README.md` and the CI job name list what `make check`
  runs; `AGENTS.md` says what the skills checker accepts and refuses;
  `docs/adopting.md` says the scaffold writes every test it lists.

## 0.10.0 (2026-09-19)

The text closes the gaps an outside review of 0.8.0 found between what
it promised and what its reference implementation could follow: the
update copy said two things, the system rows had no declared shape,
the sequence had a requirement and no mechanism, the split implied a
wire hop it never argued for, and four rules were missing. Minor:
rules are added and sharpened; the sentence that made a wire hop the
shape of a split is reversed, and the changelog names it.

### Added

- `architecture.md`, "Naming Entities": a `Created` mixin
  (`created_at` alone) for a row the platform writes for itself; the
  outbox row, the idempotency marker, and the socket ticket compose
  it, and what the platform stamps on such a row later is a field
  named for what happened. `Trackable` builds on `Created`.
  `OutboxRow(Identifiable, Created)` and
  `IdempotencyMarker(Identifiable, Created)` are declared with their
  fields, each in its namespace. Lenses `OM-03`, `OM-05`.
- `architecture.md`, "Realtime at the Edge": the mechanism behind the
  gapless `seq`: a cursor row per tenant in the `activity` role,
  updated and returned inside the append's transaction, which is also
  where the head `seq` the pong carries is read; never `MAX(seq) + 1`
  under a unique index with a retry. Gapless is named as a decision
  with its reason. Lens `NET-22`.
- `architecture.md`, "Database Roles": the relay's price, four round
  trips per write and six for a creating request, and its cheaper
  first step, the relay from the sweep alone on a short interval.
  Lens `STO-20`.
- `architecture.md`, "Translation": a value object stored as JSON only
  gains optional, defaulted fields; a rename or a removal is a
  migration that rewrites the column before the class changes. Lens
  `STO-25`.
- `architecture.md`, "Defining ORM Classes": a unique key on a
  `SoftDeletable` table is a partial unique index among the living,
  the memory impl refuses only among the living, and a contract case
  creates, deletes, and creates again. Lens `STO-26`.
- `architecture.md`, "Auth: the Gateway Verifies, the Tenancy Domain
  Owns": an external identity provider is one more credential kind,
  verified at the gateway, keyed on issuer and subject in the tenancy
  manager, and twinned locally. Lens `CTX-28`.
- `architecture.md`, "The Gateway" and "Shape of a Worker": the
  marker's four states and the worker's two fences as tables.
- `architecture.md`, "Records of Decisions": every test the guideline
  asks a build to hold is listed, the scaffold writes them, and an
  existing tree copies them; a decision is named where it is made,
  with its reason, and the alternatives are not listed.
- `scripts/check_prose.py`, `make prose`: no paragraph of the
  guideline over 200 words, a list item counted on its own.
- `scripts/check_leaks.py`: the one spelling the guideline forbids in
  a snippet, `model_copy(update={**`, is refused in the guideline,
  the lenses, the skills, the docs, and the agents.

### Changed

- `architecture.md`, "Immutability" and "Shape of an Operation": the
  update copy is one vocabulary. A copy that carries a dump, the
  caller's fields above all, is `model_validate` over a dict, because
  `model_copy` does not validate and leaves a dumped value object a
  dict; `model_copy` is for values constructed of the field's own
  type. The flagship snippet does what the tip says. Lenses `OM-10`,
  `CON-19`; `arch-scaffold-entity` and the scaffold conventions
  follow.
- `architecture.md`, "Web Services as Scalability Units" and
  "Direction of Calls": the scalability unit is a process, and the
  first form of a split is the API image with a `namespaces` setting.
  A call across namespaces stays a manager call across a split,
  because the process holds the code and the roles; the remote impl
  is for the process that does not, and a wire hop between two
  processes sharing the OM and the database is a recorded decision.
  This reverses the sentence that had the remote impl written at the
  split. Lenses `NET-02`, `CON-14`; `arch-scaffold-service` follows.
- `architecture.md`, "Identifiers": `new_id()` is `uuid.uuid7()` from
  the standard library, which ships it since Python 3.14; the scaffold
  installs no package for it.
- `architecture.md`: the placeholder root package in every snippet is
  `acme`, a name that shadows no standard-library module, as the tip
  under "Layout Conventions" asks; `platform` remains the name of the
  OM root class and the exception root.
- `architecture.md`: ten paragraphs over 200 words are split at their
  seams, and the closing section says the reference implementation
  records each place it does not yet follow the text.
- `skills/arch-scaffold-new`: the events namespace assigns `seq` from
  the cursor row; step 8 sweeps the four most common misses of a
  fresh scaffold before the review and names a high finding on a
  fresh tree as a defect of the skill.
- `docs/adopting.md`: how the checkable rules travel, as tests a
  scaffold writes and an existing tree copies.

## 0.9.0 (2026-09-19)

The branches carry the environments. This release names the smaller
environment staging and makes it the default branch, makes production
a branch that only a fast-forward moves, and closes two windows the
text had left open: a socket's context outliving its evidence, and a
released marker losing the id its row already carries. Minor: a rule
is added and two are sharpened; none is reversed.

### Added

- `architecture.md`, "Cloud: AWS": staging is `main`, and every merge
  to `main` deploys it with no approval, so a merge is the deployment.
  Production is the `release` branch, moved only by a fast-forward
  from `main` and never by a commit of its own, so a release is a
  `main` commit that has run on staging; a push to `release` plans
  production, waits for a person's approval on that plan, and applies
  it; nobody pushes to `release` but the fast-forward, and a deploy of
  production checks that `release` is an ancestor of `main` before it
  plans. Lens `DEL-38`. Minor.

### Changed

- `architecture.md`, "Stages": the session's expiry bounds a socket,
  which the process closes at that instant whatever the client does,
  and a revocation or a membership's end travels on the topic bus, so
  every process holding a socket for that session or user closes it on
  the frame; the expiry covers a frame that was missed. A revocation
  no longer waits for the next reconnect. Lens `CTX-27`.
- `architecture.md`, "The Gateway": a release keeps the marker with
  its digest and its id and clears only the attempt, so the retry that
  follows a failure after the row landed finds the row by the same id
  instead of creating a second one. Lens `NET-24`.
- `architecture.md`, "Cloud: AWS": production promotes what staging
  already ran, images by the digest staging built for that commit, and
  a release commit staging never built is refused. Lens `DEL-31`.
- `architecture.md`, "Cloud: AWS" and "Monorepo Folder Structure": the
  smaller environment is staging, its Terraform tree
  `environments/staging/` and its base domain `staging.<domain>`;
  `arch-scaffold-new` writes the same. Local and `devx` keep their
  names.
- `skills/`: `arch-scaffold-new` writes `deploy-staging.yml` (every
  push to `main`, no approval, the digests and the build id recorded
  by commit), `deploy-production.yml` (a push to `release`: the
  ancestor check, the digest lookup by the release commit, a refused
  commit staging never built, the plan as a workflow artifact, the
  apply behind the approval), and `release.yml` (the fast-forward of
  `release` to `main` on dispatch); `arch-scaffold-app` syncs the
  bundle staging built for the release commit; `arch-scaffold-service`
  closes a socket at its session's expiry and on the revocation or
  membership-end frame, tests both, keeps the marker's digest and
  `target_id` on a release, and adds its image to the two workflows.

## 0.8.0 (2026-09-19)

An outside review of 0.7.0, read against the reference implementation.
This release closes the protocols the text prescribed in detail and
left open at one step each: the idempotency marker gains an attempt
token, a refused lease renewal cancels at once, a create that issues a
secret has a rerun, the update copy starts from the stored row, and
"above" is defined for roles. It resolves the places where two rules
could not both be followed, names the tests that make two claims of the
type system true, and repairs the lens catalog where it had added,
duplicated, or outgrown the text it restates. Minor: rules are added
and sharpened; none is reversed.

### Changed

- `architecture.md`, opening: the audience names the agent that writes
  most of the code and the person who reads it, since the second impl
  of every interface and the reference implementation's size rest on
  that assumption.
- `architecture.md`, "OpContext": above means the permission set; a
  role is at most another when its permissions are a subset, a rank is
  derived from the permission table or held to it by a test, and the
  role reserved for services is not a rung: every operation that
  issues a credential refuses it by name. Lens `CTX-03`.
- `architecture.md`, "Stages" and "The Operator Context": the type is
  the fence at call sites; at construction sites a unit test enumerates
  every site that constructs a stage above the request stage, since a
  stage is an ordinary class and anything can call its constructor.
  Lens `CTX-05`.
- `architecture.md`, "Shape of an Operation": the copy on update
  starts from the stored row and `PROVENANCE_FIELDS` (`created_at`,
  `created_by`, `deleted_at`, `deleted_by`, a constant beside the
  mixins) stay as stored; a partial update is the router's translation
  with an absent field unchanged and an explicit null cleared, the
  request type's contract and no impl's decision; a create that issues
  a secret re-mints it on the row its rerun finds and returns a fresh
  `Issued...View` with the same id. Lenses `CON-19` and `NET-25`.
- `architecture.md`, "The Gateway": the idempotency marker carries an
  attempt token; a take-over stamps one of its own, and `finish` and
  the release are conditional on it in the statement, so an attempt
  that ran past the pending lease can neither finish nor release the
  marker a retry holds. Lens `NET-24`.
- `architecture.md`, "Shape of a Worker": a renewal refused with
  `Conflict` cancels the task at once; the half-lease rule is for
  failures that are not answers. Lens `ASY-23`.
- `architecture.md`, "Operations Without a Principal": a sweep has two
  shapes and who acts decides which; a service context is minted for
  the tenant on the system user, never bound to a member, so a tenant
  whose members have all left is still swept. Lens `CTX-12`.
- `architecture.md`, "Exceptions": infra has a root of its own,
  `InfraException`, with the same two fields, presented alike by the
  gateway and the worker loop; the import direction and the one root
  no longer contradict. Lens `DEL-29`.
- `architecture.md`, "Pure Rules": a rule the engine must evaluate
  inside a statement is spelled once more there, named as such, and
  held to the function by the contract case. Lens `OM-15`.
- `architecture.md`, "Clients Live in One Place": every outbound call
  carries a timeout from settings, one per client. Lens `NET-26`.
- `architecture.md`, "API Access": the bearer lives in memory and the
  tab's session storage, never local storage, and the distribution
  sends a `Content-Security-Policy` declared in Terraform. Lens `DEL-30`.
- `architecture.md`, "Tests": the named atomic methods are raced in a
  contract case, two callers at once and exactly one wins. Lens
  `DEL-28`.
- `architecture.md`, "Storage Root": the two roots are
  `StoragePostgresImpl` and `StorageMemoryImpl`, named like every impl.
  "Database Roles": a role's move to its own engine is a copy with a
  window and a cut-over of one URL, not a URL change. "Auth": a lookup
  reads the row by the digest; the constant-time claim is gone.
- `lenses/`: `CON-17` matches "Shape of an Operation" (the caller
  constructs whole, the manager's copy sets the actor); `DEL-18` lists
  `NotAuthenticated`; `CON-15` is medium, since a router that decides
  is a shape and not a breach of the classes `high` is reserved for;
  the database-backed bus lives in `ASY-10` alone and the file secrets
  refusal in `DEL-06` alone; `STO-17`, `NET-06`, and `ASY-17` are
  split so each principle is one or two sentences; `CON-20` covers
  the roots built whole, which no lens checked; `STO-19` to `STO-21`,
  `NET-27`, `NET-28`, and `ASY-24` hold the split halves; `OM-03` and
  `STO-10` name `PROVENANCE_FIELDS` and the two roots.
- `skills/`: the worker scaffold's claim is one method, its tenant-less
  storage methods join the exceptions test, the dead letter's audit
  entry has a namespace `arch-scaffold-new` creates; the service
  scaffold's router calls one manager and reads the current entity for
  a partial update; the app scaffold keeps the bearer in session
  storage and declares the policy; `arch-upgrade-deps` adopts a release
  once a patch release sits behind it.
- `scripts/`: the leak check reads `agents/`; `check_agents.py` holds
  `agents/arch-reviewer.md` to the review template, and found the agent
  one procedure step short. `README.md` lists
  what `make check` runs; the pull request template no longer asks for
  a rule that is gone.

## 0.7.1 (2026-09-19)

An outside review of 0.7.0. This release takes the findings where the
text claimed more than its mechanisms held (a retry, a rollout order,
a restore), where a rule was stated for a request and left open for a
socket and a worker, and where a scaffold or a lens had drifted from
the guideline.

### Changed

- `architecture.md`, "Direction of Calls": the order example carries
  the id the gateway minted into the reservation as its idempotency
  key, so the retry after a lost response or a crash before the order
  row exists finds the reservation instead of making a second one;
  the retry is owned at the edge by the marker, and the key in the
  signature is what makes the two impls of a service interface
  interchangeable in behavior and not only in signature.
- `architecture.md`, "Stages": a stage lives as long as the request
  that minted it; a socket holds its `OpContext` for the life of the
  connection and a revocation reaches it at the next reconnect, a
  decision bounded by the stream carrying hints only. "The Work
  Queue": authority and attribution are two fields; the person
  authorizes the work once at enqueue, the work runs on the service
  role's authority, and a kind of work that must stop with the
  person's permission re-reads the membership in its handler as a
  recorded decision. Lens `CTX-21` names that one exception.
- `architecture.md`, "Intra-Service Communication": one signing key
  is one trust domain, every holder can name any principal in any
  tenant, and that is a decision made for a platform whose processes
  are all its own; a system with a process it trusts less gives each
  issuer a key of its own, and TLS does not narrow who may sign.
- `architecture.md`, "Public Types": tolerance runs one way. A
  payload, a work item, and an envelope only gain optional, defaulted
  fields, so a row written before the deploy and an old producer's
  message still parse; a request forbids unknown fields, so a service
  rolls out before its apps. Lens `NET-23` says the same.
- `architecture.md`, "Multiple impls per interface": the shared suite
  proves what it exercises; the named atomic methods, uniqueness, the
  compare-and-set, and visibility after a write each have a case.
- `architecture.md`, "Database Roles": a role restored to an earlier
  point than its siblings is reconciled from the outbox, by relaying
  the rows since that point again; a done outbox row is kept for a
  retention period that outlives the backup schedule, never deleted
  on done. "What This Document Does Not Cover" no longer lists that
  reconciliation and names the pieces the shape already holds.
- `architecture.md`, "Tests": a run against a deployed environment is
  a smoke test of the deployment, in addition to the in-process suite
  and never in its place. Lens `DEL-28` flags a suite that runs only
  against a deployed environment, not one that also does.
- `lenses/async.md`: `ASY-15` judges side jobs alone, at high;
  `ASY-22` judges the always-on container, at low, since an execution
  model is a shape and not lost work. 148 lenses.
- `skills/arch-scaffold-entity`: the storage interface row names
  `create_<entity>`, and the manager's copy on create sets the actor,
  the initial status, and a position, as the guideline says.
- `skills/_template/review.SKILL.md`, `agents/arch-reviewer.md`,
  `skills/arch-review-full`: a fourth outcome, **unverified**, for a
  lens whose evidence lies outside the scope, and a `high` pass names
  the file that proved it, so a report carries its evidence.
- `architecture.md`, "Namespace Shape": a doubled decorator removed
  from the storage interface snippet.
- `architecture.md`, "Shape of a Worker": every claim mints a claim
  token the claim returns, and completion, release, deferral, and
  renewal condition on it in the statement, not on the worker's name,
  since one worker can hold one item twice across a requeue.
  Lens `ASY-26`.
- `architecture.md`, "The Work Queue": enqueue is a create, the insert
  that reports an existing id, so a retried enqueue never resets a
  claim; the manager's copy stamps the actor, the timestamps, the
  status, and the attempts, and clears every claim field, whatever the
  caller sent. Lens `ASY-25`.
- `architecture.md`, "Shape of an Operation": the outcome the marker
  stores for a create that issued a secret is the view with the secret
  absent; a replay answers with the row and no secret and says so in
  its header. Lens `NET-31`.
- `architecture.md`, "The Gateway" and "Direction of Calls": a router
  calls one operation of its service impl, which calls one manager,
  from the first day, so the split is a wiring change and not a
  rewrite of the routers; the in-process impl exists from the start
  and the remote one is written at the split. Lenses `CON-14` and
  `CON-15`; the service scaffold follows.
- `architecture.md`, "Multiple impls per interface": every unique key
  the schema declares has a contract case, so the memory impl refuses
  what the engine refuses. Lens `DEL-28`.
- `architecture.md`, "Clients Live in One Place": the gateway bounds a
  request with a deadline from settings and a work handler is bounded
  by its lease; nothing runs unbounded. Lenses `NET-26` and `ASY-17`.
- `architecture.md`, "Naming Entities": a mixin is composed only where
  a manager operation exercises it, which `OM-05` restated before the
  text said it. "Migrations": two migrations of one role in the same
  minute collide on the stamp; the later waits a minute or takes a
  suffix. "Identifiers": the ids in a log line sort by creation time;
  nobody eyeballs a timestamp out of hex.
- `lenses/`: every principle is at most sixty words and every Look for
  and Violation at most three sentences, as `lenses/README.md` now
  states and `check_lenses.py` holds; a lens that held two rules is
  split (`ASY-16`, `ASY-20`, `CTX-03`, `CTX-05`, `CON-17`, `CON-19`,
  `OM-10`, `OM-15`, `NET-17`, `STO-16`, `STO-18`, `DEL-02`, `DEL-04`,
  `DEL-20`, `DEL-28`), the second rule taking the next id of its
  group. Rules with no lens gain one: the data tier splits by role
  (`NET-29`), operator writes through the operator plane's helper
  (`CTX-24`), optional filters as keyword parameters (`CTX-10`), the
  owner-only secrets file (`ASY-28`). `NET-12` is medium. `OM-14`
  exempts value objects and read models. `DEL-26`, `STO-21`, `STO-24`,
  and `ASY-03` ask for what a diff or a runbook shows, not for live
  data. Ownership between groups is stated in each file's header:
  async owns the sweep's duties, context owns operator gating. 186
  lenses.
- `scripts/`: `slug` keeps underscores, as GitHub anchors do;
  `check_lenses.py` holds the word, sentence, and column limits and
  the lens count the README states; `check_skills.py` holds the five
  scaffold sections in order; the leak check reads `AGENTS.md`, which
  names the product list as the regression guard it is. `README.md`
  says `make check` needs pytest and npx.
- `skills/`: `arch-review-full` defines the merge on a tie, what
  "applied" counts, the fallback's path substitution, and what `all`
  costs; `arch-upgrade-deps` raises caps one library at a time and
  says `make reset` after a database major; `arch-new-aspect` agrees
  with `CONTRIBUTING.md` on versioning, is not model-invoked, and has
  a short description; `arch-deviate` dates the record today; the
  worker scaffold carries the claim token and the create on enqueue;
  the service scaffold the request deadline and the stripped outcome;
  `arch-scaffold-new` may run the migration check. `AGENTS.md` says
  what `uv run` and `pnpm run` grant and that the tools form is house
  style.

## 0.7.0 (2026-09-19)

The context is no longer one type. This release names what a request
has established as a chain of typed stages, and what a consumer sees
as a set of composable scopes. It reverses one rule of 0.6.0: the
principal-less operations now take the request stage instead of no
context. Before 1.0.0 a reversal bumps the minor number, as
`CONTRIBUTING.md` now says. The rest sharpens: a consumer that needs
less than `OpContext` declares less.

### Changed

- `architecture.md`, "OpContext": the section is rewritten around two
  ideas kept apart. "Stages": `RequestContext`, `IdentityContext`,
  `OpContext`, and `OperatorContext` are concrete frozen types, each a
  subclass of the stage it refines, each produced by exactly one
  transition on the tenancy manager (`authenticate_login`,
  `authenticate`, `admit_operator`, the claim, the service contexts
  of a sweep); a function that takes a stage relies on its invariant
  instead of checking it again, and the stage in a
  signature is what fences which operations a holder can call, with
  no bundle of managers per stage. "Scopes": `RequestScope`,
  `TenantScope`, `ActorScope`, `CredentialScope`, and
  `ProvenanceScope` are `Protocol`s of read-only properties that a
  stage satisfies structurally; the scope set is derived from
  consumers, a combination is named only when it is a concept of the
  domain, and there is no authorization scope because a manager
  operation takes `OpContext`, which is its scope. Structural
  injection and the context never cross: a manager does not arrive on
  a context, and a request id does not arrive in a constructor.
  Lenses `CTX-01`, `CTX-02`, `CTX-05`, `CTX-06`, `CTX-16`, and
  `CTX-20` are rewritten; `CTX-21` (the weakest stage, relied on),
  `CTX-22` (the narrowest scope, a `Protocol`), `CTX-23` (no name for
  a combination without a concept), and `CON-18` (constructor and
  context never cross) are added; `arch-scaffold-new`,
  `arch-scaffold-service`, `arch-scaffold-worker`, and the shared
  scaffold conventions follow. Minor.
- `architecture.md`, "Operations Without a Principal": the operations
  that exist before a principal does take `RequestContext` first and
  produce a stronger stage; a test names each of them. The outbox
  handoff keeps `(org_id, row)`. Minor, a reversal before 1.0.0.
- `architecture.md`, "The Operator Context": the operator's context is
  `OperatorContext`, named for the plane it serves, since "admin" is a
  tenant role in the reference implementation; `OperatorContext` refines
  `IdentityContext` through `admit_operator`; "The Gateway" mints the
  request stage and runs the transitions; "The Work Queue": the loop
  mints a `RequestContext` per claim and per sweep pass;
  "Injectability" points at the boundary between what a constructor
  takes and what a context carries.
- `CONTRIBUTING.md`, "Versioning": before 1.0.0 a removed or reversed
  rule bumps the minor number, as semver reads 0.x; 1.0.0 is for the
  text that has stopped moving. Patch.

## 0.6.0 (2026-09-19)

Two outside reviews of 0.5.1. The cheap drift is running out; this
release takes the one design flaw both repositories shared, the
contracts the reviews showed to be looser than the prose, and the
decisions the prose had made without naming them.

### Added

- `architecture.md`, "The Gateway": only an outcome a retry cannot
  change is stored on the idempotency marker; a refusal is replayed
  and a failure releases the marker, so the retry runs again on the
  same id instead of replaying the failure for good, which would have
  sent the client to a new key and a second row. Lenses `NET-06` and
  `NET-09` and `arch-scaffold-service` say the same. Minor.
- `architecture.md`, "Namespace Shape" and "A Storage Impl": a create
  and an update are two primitives. The insert does nothing on an
  existing id and reports it, the outbox row landing only when the
  insert won, so a retried create neither overwrites the row nor
  announces it twice and no check precedes the write. Lens `STO-16`
  is "Two write primitives"; `CON-17`, `arch-scaffold-entity`, and
  `arch-scaffold-new` follow. Minor.
- `architecture.md`, "Storage Principles": row-level security is not
  a second fence here, and the sentence says why; a project that
  wants one records the decision. Lens `CTX-09` says the same.
- `architecture.md`, "Realtime at the Edge": the hint is metadata
  every member of the tenant may see, named as a decision; a product
  where existence itself is restricted keeps one stream per
  visibility scope. Lens `NET-17` says the same.
- `architecture.md`, "Cache": the TTL is the bound on staleness, and
  the two ways an invalidation misses are named.

### Changed

- `architecture.md`, "Public Types": a list that can outgrow its clamp
  returns a page envelope and pages by an opaque cursor over its own
  order, of which `after_id` is the case where that order is the id
  order. The reference implementation had the better shape. Lens
  `NET-13` follows. Minor.
- `architecture.md`, "Shape of an Operation": the manager's copy on
  create sets what is the manager's to decide (the actor from the
  context, the initial status, a position), which is what the
  reference implementation does; lens `CON-17` and the scaffold
  conventions no longer say "a create copies nothing".
- `architecture.md`, "Immutability" and "The Work Queue": pydantic
  does not validate a default, so a `FrozenMapping` field's empty
  default is `Field(default_factory=dict, validate_default=True)`;
  the `WorkItem` snippet had shipped a plain dict on the default
  path. Lens `OM-10` and the scaffold conventions say the same.
- `architecture.md`: every interface snippet subclasses `ABC` and
  marks its methods `@abstractmethod`, as `CON-02` requires; the
  snippets had shown plain classes.
- `architecture.md`, "The Gateway": the take-over keys on the pending
  lease, an option of the idempotency manager, where it said "the
  request deadline", which the document does not define.
- Lens `ASY-17`: the violation text no longer demands the record
  fence the 0.5.0 principle gave up; it names a completion, release,
  or renewal that does not check the claim in its own statement, and
  a record write that treats the lease alone as exclusive. Lens
  `NET-09`: the response is stored per tenant and principal, as
  `NET-06` and the text say.
- `arch-scaffold-entity`: the router builds a created entity on the
  id the gateway minted before the idempotency marker, not on a fresh
  `new_id()`, or a retry defeats the crash recovery of 0.5.1.

## 0.5.1 (2026-09-19)

Two outside reviews of 0.5.0. This release takes the findings that
name a real conflict between two rules, a crash window a scaffold left
open, or a drift between the guideline and its scaffolds.

### Changed

- `architecture.md`, "Realtime at the Edge": the sequenced stream is
  a stream of hints. A frame and a replayed record carry the identity
  of the change (`seq`, `kind`, `target_id`, the actor) and no field
  of the entity; the client reads the entity through the authorized
  read, which applies team visibility, so the whole stream and CTX-04
  hold together. `seq` orders events, not core writes. Lens `NET-17`
  says the same. Patch: the reference implementation already sends
  the hint alone.
- `architecture.md`, "The Gateway" and "Shape of an Operation": the
  edge idempotency marker carries the request digest and the id the
  create will use, minted before `begin`; a pending marker past the
  request deadline is taken over and the request rerun with that id,
  and a create that finds its own id already written returns the row
  as stored, so the rerun cannot duplicate what a crash between commit
  and `finish` left behind. `arch-scaffold-service`,
  `arch-scaffold-entity`, and lenses `NET-06` and `CON-17` follow.
- `architecture.md`, "Scalability by Design": a dedicated engine
  isolates a hot tenant's neighbours and does not lift the tenant's
  own ceiling, the one sequence.
- `architecture.md`, "Defining ORM Classes": `FeedIdentifiableMixin`
  is in the guideline, where index rule 2 requires it; the scaffolds
  had it alone. Lens `STO-14` names it.
- `architecture.md`, "OpContext" and lens `CTX-05`: the tenancy
  manager's service context per live tenant, which "Operations
  Without a Principal" already named, is the fourth entry point.
- `architecture.md`, "The Work Queue": the `WorkItem` payload is
  `FrozenMapping`, as "Immutability" requires. "Monorepo Folder
  Structure" shows `specs/`; `arch-scaffold-new` writes
  `environments/prod/` like the tree.

### Added

- `architecture.md`, "What This Document Does Not Cover": the
  concerns a team commits to per system once the shape holds, named
  so their absence is a boundary and not an omission.

## 0.5.0 (2026-09-19)

Two outside reviews of 0.4.4 read the guideline end to end and ran its
gates. This release takes the findings that sharpen a rule or repair a
claim the mechanism did not earn, each verified against the reference
implementation.

### Added

- `architecture.md`, "Public Types": a list that can outgrow its clamp
  pages by `after_id` over the id order, and nothing pages by an
  offset. Lens `NET-13` says the same. Minor.
- `architecture.md`, "Realtime at the Edge": the sequenced stream
  travels whole and a client filters by kind after ordering, so
  contiguity means what it says; the first frame and every pong carry
  the tenant's head `seq`, so a dropped last frame is found on the
  next keepalive and not on the next event. Lens `NET-17` looks for
  both. Minor.
- `architecture.md`, "Auth: the Gateway Verifies, the Tenancy Domain
  Owns": a password is a memory-hard hash under its own salt; an API
  key, a session token, and a socket ticket are stored as a SHA-256
  digest, shown once, and compared in constant time. Lens `NET-11`
  says the same. Minor.
- `architecture.md`, "Intra-Service Communication": the internal
  credential is a signed token with an expiry, its key read from the
  secret store and verified by the callee. Lens `NET-06` says the
  same. Minor.
- `architecture.md`, "Scalability by Design": the assumptions the
  rules rest on and the three triggers that end one (a hot tenant, a
  starved lane, an exhausted pool), each with the change it asks for.
  The `org_id`-first rule makes every query tenant-scoped; it does not
  partition data. Minor.

### Changed

- `architecture.md`, "Shape of an Operation", "Namespace Shape", and
  "A Storage Impl": the canonical write takes the outbox row and lands
  the two in one statement, then relays, as "Database Roles", lens
  `STO-17`, and the scaffold already prescribed; the snippets had
  drifted behind the rule. Lens `STO-16` names the outbox row in the
  upsert.
- `architecture.md`, "Immutability": a bare `Mapping` field on a
  frozen model still holds a mutable dict, so a mapping field is
  `FrozenMapping`; `model_validate` hands an instance back untouched,
  so a copy that carries caller input is rebuilt from a dict. Lens
  `OM-10`, the scaffold conventions, and `arch-scaffold-new` say the
  same.
- `architecture.md`, "Shape of a Worker": the two fences guarantee one
  completion per item and nothing about the record, which lives in
  another role; the handler's idempotency, the record's `version`, and
  reconciliation of external effects carry the rest. The
  compare-and-set is named for what it refuses and when. Lens `ASY-17`
  says the same.
- `architecture.md`, "Storage Principles": the rule reads "no
  transaction outlives a storage call", which is what it always meant;
  lens `STO-02` carries the new title. A storage swap is complete when
  the shared suite passes over the new impl, not when the interface
  compiles.
- `architecture.md`, "Web Services as Scalability Units": the
  independence of a split-out service is of the process, not of the
  data; the data tier splits by role, never by service.
- `architecture.md`, the opening: the document says who it is for, a
  small team that starts with one process and one database, and that
  the next step is a deployment change up to the limits "Scalability
  by Design" names, where it said the system scales out never by
  changing code. `README.md` says the same in its first bullet.
- `README.md`: the Python pin follows the latest-stable rule on
  purpose. `.github/workflows/ci.yml`: the runner is pinned by release
  like the actions are.

## 0.4.4 (2026-09-19)

### Changed

- `README.md` and `docs/adopting.md`: the three lines that update the
  installed plugin (`/plugin marketplace update`, `/plugin update`,
  `/reload-plugins`) as a copy-paste block under the install block,
  with the reason: a skill reads the version it shipped with. Patch.

## 0.4.3 (2026-09-19)

### Changed

- `scripts/check_leaks.py`: the assistant-tooling term group is gone;
  "agent", "AI", "LLM", "prompt" are ordinary words in the guideline
  and the lenses. "Multiple impls per interface", "Technology Choices
  and How to Override Them", and "Next: An End-to-End Reference
  Implementation" say "agent" where they said "program" or "automated
  author", and "The Gateway" and lens `CTX-18` say an agent presents
  an API key; `arch-new-aspect`, `AGENTS.md`, and the Makefile no
  longer ask for the rephrasing. Patch.
- `architecture.md`: "Operations Without a Principal" names the one
  kind that takes a tenant id in place of a context, the outbox relay
  and the event append it performs (`(org_id, row)`), which "Database
  Roles" and the scaffold already prescribe; lens `CTX-16` says the
  same. Lens `OM-03` lists `updated_by` among the `Trackable` fields,
  as "Naming Entities" does. Both from the second review of the
  reference implementation's sibling. Patch.

## 0.4.2 (2026-09-19)

### Changed

- `skills/arch-scaffold-service/SKILL.md`: the `gateway/auth.py`,
  `realtime/`, and `gateway/observability.py` rows say the websocket
  route resolves its context through a `socket_context` gateway
  dependency (redeem the ticket, build `OpContext` by the same rules
  as `current_context`, carry the request id and the span) and never
  parses the query itself, and the request-id middleware covers
  websocket scopes. From The Network Layer (The Gateway). Patch.
- `skills/arch-scaffold-service/SKILL.md`,
  `skills/_shared/scaffold-conventions.md`,
  `skills/arch-scaffold-entity/SKILL.md`: every creating route (every
  `POST` that answers 201), in every namespace, declares the
  `Idempotency-Key` dependency, so a retried create returns the stored
  response. From The Network Layer (The Gateway, Edge idempotency).
  Patch.
- `skills/arch-scaffold-new/SKILL.md`: the tenancy row says an API key
  is issued with a required `expires_in`, capped by a settings option
  and never `None`, and the wire request defaults inside the cap. From
  The Network Layer (The Gateway, Credentials). Patch.
- `skills/arch-scaffold-app/SKILL.md`: the CLI reads its settings (API
  URL, token, home) into one settings object at the start of `main`
  under the product prefix and the client takes them through its
  constructor, nothing below `main` reading `os.environ`; a `listen`
  command keeps the last contiguous `seq` from the hello frame and
  each push and replays from the events route on a gap or a
  reconnect. From Cross-Cutting Conventions (Configuration) and The
  Network Layer (Realtime at the Edge). Patch.
- `skills/arch-scaffold-app/SKILL.md`: a view never re-implements a
  domain rule to enable or disable an action; the service exposes the
  decision as a flag on the view or the client acts on the error
  envelope. From Apps (Apps Are Dumb). Patch.
- `skills/_shared/scaffold-conventions.md`: `ctx.require(<permission>)`
  is the first line of every mutating manager operation, before any
  read, the ones a worker calls included (complete, fail, defer,
  release, extend the lease). From The Business Layer (Shape of an
  Operation). Patch.
- `skills/arch-scaffold-worker/SKILL.md`: the "half the lease" cutoff
  is wall-clock time since the last successful renewal, never a count
  of failed attempts times the interval. From Worker Roles (Shape of a
  Worker). Patch.
- `skills/_template/review.SKILL.md`, `agents/arch-reviewer.md`, and
  the seven generated review skills: a pass on a `high` lens is
  verified like a finding, by opening the file that would breach it
  and naming it when deciding, because the same tree reviewed twice
  moves high lenses between finding and pass. Patch.
- `docs/adopting.md`: the first review of a fresh scaffold lands about
  two thirds of the lenses and a dozen high findings; the
  review-and-fix pass is part of scaffolding. Patch.

## 0.4.1 (2026-09-18)

### Changed

- Scaffold skills: `scaffold-conventions.md` finds the guideline
  version before writing (`plugin.json` against the changelog's first
  release heading; a snapshot between releases is named, not pinned)
  and prints it in the output; tests are counted in cases, not files
  (one contract case per storage method, one refusal per authorization
  rule, one test per rate-limited route, one per exit code, one per
  infra capability); `arch-scaffold-new` pins only a release the two
  agree on, adds the settings check to the infra tests (every
  `InfraSettings` field in `.env.example`), passes every prefix the
  settings read and the database URL as its own secret through
  Terraform, adds the interface check to the OM unit tests (every
  `*Interface` an `ABC` with abstract methods), and ends with
  `arch-review-full` over the tree with every high finding closed; `arch-scaffold-service` adds
  `tests/test_settings.py` (every settings field in `.env.example`,
  every field without a local default set or wired in each Terraform
  environment); `docs/adopting.md` says to update the plugin before a
  scaffold or a review. From the second one-shot run of
  `arch-scaffold-new`. Patch.

### Added

- `architecture.md`: "Multiple impls per interface" says why the
  technology impl and the memory impl pair is a lever rather than a
  cost: an agent writes and keeps the memory impl cheaply, the shape
  generalizes (a dict keyed by tenant and id plus the relational
  filters), and the pair is what lets an application run in-process in
  a test, a backend swap at the root, and impls compose; linked to
  "The App Container", "Storage Root", "Composition by decoration",
  and the reference implementation. No lens: a rationale. Patch.
- `architecture.md`: "Scalability by Design", a closing rationale that
  says horizontal scalability is what most of the rules add up to and
  names them by anchor (stateless services, services per namespace,
  `org_id`-first storage, database roles, the work queue and workers
  per lane, the outbox and the idempotent consumer, cache scopes,
  topics and one realtime channel per app, immutability and pure
  rules, the app container): scale out by adding processes, never by
  changing code; linked from the introduction and from "Web Services
  as Scalability Units"; `README.md` summary. No lens: a rationale.
  Patch.
- `architecture.md`: "The App Container" says why the roots are built
  whole, once per process, and never per request or on first use: a
  constructor holds references and opens nothing, so every root builds
  in microseconds, the imports are paid once at module load, and a
  wiring error surfaces at boot, where the process exits and readiness
  never reports ready, rather than at the first request that needs the
  missing piece; linked to "Storage Root", "InfraInterface Root", and
  the reference implementation. No lens: a rationale that `CON-09` and
  `CON-16` already check. Patch.

## 0.4.0 (2026-09-18)

### Added

- `architecture.md`: "Database Roles" lands a core row and its handoff
  (an event row, a work item) in one named atomic method with an outbox
  row that is relayed at once and, after a crash, by the maintenance
  sweep (the transactional outbox); "Storage Principles" derives the
  no-transactions rule and names the invariant test for a named atomic
  method, the outbox row among its cases; "Realtime at the Edge"
  states the `Event` row (`activity` role, `Identifiable` plus
  `org_id`, `seq`, `kind`, `target_id`, a typed payload, `seq`
  assigned per tenant and gapless by the atomic append) and has the
  client keep the last contiguous sequence so a gap is a replay, never
  a skip; the tree gains `events/`; lenses `STO-17`, `ASY-19`, and
  `NET-17` sharpened; lens `NET-22`; `arch-scaffold-new` scaffolds
  `events/` and `outbox/`, and `arch-scaffold-entity` and
  `arch-scaffold-namespace` write through them. Minor.
- `architecture.md`: "Shape of a Worker" makes the lease the first
  fence and completion and every record write conditional on the claim
  (a fencing token), refused with `Conflict` and handed back without
  spending an attempt, and states the liveness beat as a keyed TTL
  written and read back; "Idempotency on the Consumer Side" keeps the
  dedupe marker with its effect (the idempotent consumer); "The Work
  Queue" makes a failed item a dead letter with an audit entry and a
  metric, names competing consumers, and fixes payload shapes per kind
  with `WORK_PAYLOADS`; lenses `ASY-14`, `ASY-16`, and `ASY-17`
  sharpened; `arch-scaffold-worker` fences completion and record
  writes and dead-letters a failed item. Minor.
- `architecture.md`: "Direction of Calls" gives a reservation an expiry
  and a chain that must survive a crash a compensating step per
  reversible step with the irreversible one last (a saga); "Shape of an
  Operation" names last-writer-wins as the default and a `version`
  compare-and-set raising `Conflict` as the opt-in (optimistic
  concurrency); lenses `ASY-20` and `STO-16` sharpened. Minor.
- `architecture.md`: "Migrations" makes every migration compatible
  with the release before it (expand and contract); "Public Types"
  makes a change inside a version additive, with topic payloads and
  realtime envelopes read tolerantly so producers and consumers roll
  out in either order; lenses `STO-18` and `NET-13` sharpened; lens
  `NET-23`. Minor.
- `architecture.md`: "Intra-Service Communication" names the trust
  boundary plain traffic rests on (private subnets, security groups,
  only the gateway public, in Terraform; mutual TLS when the runtime
  gives it away) and has a service-to-service call carry a short-lived
  internal credential the callee's gateway rebuilds `OpContext` from;
  "The Gateway" stores an idempotent response per tenant and principal;
  "Exceptions" adds `NotAuthenticated` (401); lenses `NET-06` and
  `NET-12` sharpened; `arch-scaffold-service` accepts the internal
  credential and raises `NotAuthenticated`. Minor.
- `architecture.md`: "Database Roles" backs up every role on its own
  schedule with a rehearsed restore, purges a soft-deleted row after
  its retention period as the one hard delete, and keeps personal data
  in named fields; lens `STO-17` sharpened; `arch-scaffold-worker`
  purges in the sweep. Minor.
- `architecture.md`: "Tests", a subsection of "Cross-Cutting
  Conventions": unit tests over the memory roots and the pure rules,
  the storage contract cases parameterized by a fixture and run over
  memory in the fast gate and over Postgres in the integration job,
  end-to-end tests over the in-process container with every backend a
  twin, and the `integration`, `e2e`, and `slow` markers; lens
  `DEL-28`. Minor.
- `architecture.md`: "Technology Choices and How to Override Them"
  names FastAPI on uvicorn, httpx, Typer, SQS, and ECS Fargate;
  "Versions" adopts a release at the next scheduled bump, once a patch
  sits behind it; lens `DEL-26` sharpened. Minor.
- `architecture.md`: pattern names where a guarantee rides on one, so
  a reader can look it up: transactional outbox, idempotent consumer,
  fencing token, saga, expand and contract, claim check, competing
  consumers. Patch.

### Changed

- `architecture.md`: "OpContext" carries `user_id` and `org_id`, never
  `User` and `Org` entities, and gains `credential_id`; a manager that
  needs the user loads it, and `opcontext.py` declares `Role`,
  `Permission`, `CredentialKind`, and `AppType` and imports nothing
  above `base.py`, which removes the import cycle the entity fields
  hid. Adopters change the two fields and drop the `TYPE_CHECKING`
  import. Lenses `CTX-02` and `CON-08` sharpened; `arch-scaffold-new`
  follows. Minor.
- `architecture.md`: "Infrastructure Principles" states that the OM
  imports infra interfaces and infra imports nothing from the OM;
  "Topics" makes `TopicPayload` a frozen base infra declares with
  `extra="ignore"`, and names `ENTITY_CHANGED`, the realtime producer;
  the system scope is the zero UUID by value. Adopters drop the
  infra-to-OM dependency. Lenses `ASY-09` and `CON-10` sharpened;
  `arch-scaffold-new` follows. Minor.
- `architecture.md`: "Topics" is best effort (at most once to the
  processes subscribed at the time); queues stay at-least-once;
  "Overriding a Choice" follows; lenses `ASY-10`, `ASY-14`, and
  `DEL-25` sharpened. Minor.
- `architecture.md`: "Direction of Calls" has cross-service
  orchestration compose and never decide, reserving stock an operation
  of the inventory namespace, and gives every service interface an
  in-process impl and a remote impl swapped at wiring time; lenses
  `CON-14` and `CON-15` sharpened; `arch-scaffold-service` names both
  impls. Minor.
- `architecture.md`: "The Work Queue" renames the row's `queue` field
  to `lane` (the inbound `QueueInterface`, the work table, and the
  `queue` role keep their names); adopters rename the column and the
  claim argument; lens `ASY-16` sharpened; `arch-scaffold-worker`
  takes `--lane`. Minor.
- `architecture.md`: "Naming Entities" adds `updated_by` to `Trackable`
  and its mixin, set by every update; "Immutability" states the deep
  freeze (tuples, frozen models, `Mapping`) and re-validation of a copy
  that carries caller input; "Interfaces" makes an interface an `ABC`
  with abstract methods; "Identifiers" gives `EMPTY_UUID` one reading
  (the platform owns the reference) and makes an optional reference
  `None`; "Entities, Value Objects, and Read Models" makes a persisted
  projection derived and rebuildable; lenses `OM-08`, `OM-10`, `OM-13`,
  and `CON-02` sharpened; the scaffold conventions follow. Minor.
- `architecture.md`: "Realtime at the Edge" and "Push-First Apps" call
  the socket buffer a send buffer, so outbox names one thing;
  `OrderBoardView` is `OrderBoard`; a layer is not a swimlane; a
  database role is not a Postgres role; `OpContext` is the operation
  context and `AdminContext` the operator's; `platform` is named as a
  placeholder before its first use; the Push-First principle is stated
  once; "Separation of Layers" precedes "Interfaces" and "Client App
  Architecture" follows "Apps". Patch.
- `lenses/`: severity recalibrated. `high` is reserved for tenancy,
  authorization, lost or duplicated work, and a cross-role breach (30
  of 143); a business decision in a router rises to `high`; the id
  factory, the shape rules, and the boundaries that bend without
  losing work drop to `medium`; `arch-review-full` closes on three or
  more `high` findings, with the count. Patch.
- Scaffolds: an append-only entity gets no update, delete, or
  `Update...Request`; the browser client lives at `apps/<app>/src/api/`
  and the Python client at `clients/python/`, which the CLI and a
  remote service impl import; `AGENTS.md` adds the scaffold audit step.
  Patch.
- `README.md` lens count.

## 0.3.0 (2026-09-17)

### Added

- `architecture.md`: "Error Tracking", a subsection of "Cross-Cutting
  Conventions": every web service, worker, and browser app reports
  errors through the Sentry SDK, tagged with service, release, and
  request id, off until a DSN is set, the browser app reporting from
  each route's error element and the React root; "Traces and Metrics"
  bounds label values and has every process serve `/metrics`, a worker
  on its own port; "Cloud: AWS" collects logs through the log driver
  with retention and metrics and traces through a non-essential
  collector; "The Gateway" answers `/metrics` with a 404 at the load
  balancer; "Local: Docker Compose" adds GlitchTip to `devx`; lens
  `DEL-27`; lenses `DEL-02`, `DEL-04`, `DEL-20`, `NET-10`, and `CON-16`
  sharpened; `arch-scaffold-new`, `arch-scaffold-service`,
  `arch-scaffold-worker`, and `arch-scaffold-app` wire error reporting,
  the worker metrics port, and the collector; `README.md` lens count.
  Minor.
- `architecture.md`: "Cloud: AWS" ships each browser app from a private
  S3 bucket served through CloudFront, declared in Terraform in every
  environment, while the app's calls to the platform stay behind the
  gateway; production promotes browser bundles by build id as it
  promotes images by digest, each bundle reading a `config.json` its
  environment writes; every environment has a base domain with `api.`,
  `app.`, and `admin.` under it; "Stack" separates serving files from
  serving the platform; "The Gateway" accepts cross-origin requests
  only from the browser apps' origins; "Configuration" and "The
  Operator Console" follow; "Technology Choices and How to Override
  Them" names the hosting; lenses `DEL-02`, `DEL-21`, and `NET-06`
  sharpened; `arch-scaffold-app` creates the `static-site` module, the
  config loader, and a build-once deploy, and refuses to finish without
  them; `arch-scaffold-service` adds the allowed origins;
  `arch-scaffold-new` wires the base domain and `api.` and requires the
  portal's deployment. Minor.

### Changed

- `architecture.md`: "Local: Docker Compose" names the developer
  dashboard profile `devx` (pgweb, Valkey Admin, the local images'
  consoles, Jaeger for traces, the metrics view, ports from `.env`) and
  has the repository's `README.md` list the local URL of each
  dashboard, each service's API docs, and each browser app; lens
  `DEL-04` sharpened; `arch-scaffold-new` writes the profile, a
  `devx-up` target, the ports in `.env.example`, and the `Local URLs`
  table in `README.md`; `arch-scaffold-service` and `arch-scaffold-app`
  add their rows to it. Minor.
- `architecture.md`: "Technology Choices and How to Override Them" and
  "Local: Docker Compose" name Valkey as the cache; the `devx` profile
  browses it with Valkey Admin; `arch-scaffold-new` runs the Valkey
  image locally and depends on the `valkey-glide` client. Minor.
- `architecture.md`: "Local: Docker Compose" adds `make seed`, which
  bootstraps a development org and owner from `.env` (an `.example`
  address and a development password), changes nothing on a second
  run, and is listed with the seeded sign-in in `README.md`; "What a
  Process Refuses" refuses the seed against a non-local database;
  lenses `DEL-04` and `DEL-06` sharpened; `arch-scaffold-new` writes
  the target, the seed settings, and the README quick start and runs
  the seed twice; `arch-scaffold-service` adds `bootstrap --seed`.
  Minor.
- `architecture.md`: "Local: Docker Compose" adds the `make up`,
  `make down`, `make reset`, and `make urls` shortcuts; lens `DEL-04`
  looks for them; `arch-scaffold-new` writes them and leads the README
  quick start with `make up`. Minor.

## 0.2.0 (2026-09-17)

### Added

- `architecture.md`: the Software Design and Architecture Guidelines.
- `architecture.md`: "Technology Choices and How to Override Them",
  a section that says why the guideline names technologies and how a
  project records substitutions in one ADR; lens `DEL-25`;
  `docs/adopting.md` step 3; `arch-scaffold-new` writes
  `docs/adr/0002-technology-choices.md`. Minor.
- `architecture.md`: "Next: An End-to-End Reference Implementation",
  a closing pointer to Tadas (<https://github.com/baristaze/tadas>), a
  to-do app for teams that applies the guideline end to end; linked
  from the introduction. Patch.
- `architecture.md`: "Versions", a subsection of "Technology Choices
  and How to Override Them": every dependency runs on its latest
  stable release, the current active LTS line where one exists; linked
  from "Local: Docker Compose" and "Layout Conventions"; lens `DEL-26`;
  `arch-scaffold-new`, `arch-scaffold-service`, and the scaffold
  conventions pin new runtimes, images, and libraries at those
  releases; this repository's CI moves to Python 3.14, Node 24.21.0,
  and the latest action and markdownlint releases (MD060, new in that
  release, is off like MD013). Minor.
- `skills/arch-upgrade-deps`: moves every dependency of a project to
  its latest stable or LTS release from the maintainers' release data,
  runs the fast and integration gates, and holds back an upgrade that
  breaks them.
- `scripts/check_links.py` checks a link whose text wraps across
  lines; before, a line break hid the anchor from the checker.
- `architecture.md`: a generated table of contents (`make gen-toc`,
  checked by `make toc`) and named anchor links at every
  cross-reference and at the first mention of a concept defined later.
- `skills/arch-new-aspect`: incorporates a new aspect into the
  guideline and cascades it through lenses, skills, docs, README, and
  this changelog.
- `lenses/`: 139 review lenses in seven groups, each citing its
  section by title.
- Skills: `arch-review-<group>` for each group, `arch-review-full`,
  `arch-scaffold-new`, `arch-scaffold-namespace`,
  `arch-scaffold-entity`, `arch-scaffold-service`,
  `arch-scaffold-worker`, `arch-scaffold-app`, `arch-explain`,
  `arch-deviate`, `arch-upgrade-deps`.
- `agents/arch-reviewer.md`: the subagent the full review fans out to.
- Plugin and marketplace manifests under `.claude-plugin/`.
- Checkers: markdownlint, lens format and citations, vocabulary leaks,
  links, skill shape, generated-skill freshness; `make check` runs them
  and CI runs `make check`.
- `docs/adopting.md`: how a project adopts the guideline and the
  skills.

### Changed

- Scaffolded CI runs every gate the guideline's "Layout Conventions"
  and "Migrations" name: `arch-scaffold-new` writes `lint`,
  `format-check`, `typecheck`, and `migrate-roundtrip` targets, states
  that `check` runs lint, format, types, and unit tests, and adds the
  round trip to the integration job; `arch-scaffold-app` adds the
  browser app's lint, typecheck, and test scripts to `check`. Patch.
- Scaffold skills sharpened from their first end-to-end run and the
  full review of what they produced: `arch-scaffold-new` accepts a
  fresh repository as its target, writes `specs/architecture.md`, the
  `events` and `idempotency` namespaces, the migration module, the
  contract-test layout, the Terraform modules and the deploy workflow,
  and states the om/infra workspace dependency and where the role table
  lives; `arch-scaffold-service` declares service interfaces from the
  single-process start, a durable edge idempotency, a server span, a
  boot helper, rate limits from settings, and the `after_seq` replay
  route; `arch-scaffold-worker` gains a container, a `health`
  subcommand, `rules.py`, bounded lease renewal, conditional writes,
  and a per-tenant sweep; `arch-scaffold-app` moves the client package
  to `clients/`, adds the sign-in screen, the lint config, and the
  sequence-gap replay; `arch-scaffold-entity` composes the feed mixin
  variant instead of redeclaring `org_id` and reads the current entity
  before an update. Patch.
- Sections are unnumbered. Every reference in the repository names a
  section by title; `scripts/check_lenses.py` refuses a number. The
  two `Principles` subsections are `Storage Principles` and
  `Infrastructure Principles` so every anchor is unique.
- Every tree diagram in the guideline is a fenced `text` block.
- Root snippets (`StorageInterface`, `ServicesInterface`,
  `InfraInterface`, `Queues`) show two entries and a `# ...` line.
