# Contracts

Group id: `contracts`. Covers Interfaces, Separation of Layers, The
Business Layer, The Network Layer (Service Interfaces and Impls;
Direction of Calls), and Cross-Cutting Conventions (The App Container)
of `architecture.md`.

This group judges how the pieces of the system are declared, wired, and
allowed to call each other: interfaces and their impls, constructor
injection, the roots that assemble everything at boot, the shape of a
manager operation, and the direction calls may flow across layers. It
leaves the shape of entities and mixins to `om`, everything about
`OpContext`, authorization, and tenancy to `context`, the internals of
tables, translation, and migrations to `storage`, the semantics of each
infra capability to `async`, the gateway, public types, and realtime
channel to `network`, and settings objects and environment reads to
`delivery`.

## CON-01 Every layer is defined by an interface

**Principle.** A manager, a storage, a service: each exposes a
`*Interface` that lists the operations its scope supports. An operation
is an async method whose signature is a contract.

**Source.** Interfaces.

**Look for.** Every manager, storage, and service class; the module a
consumer imports from; the type of every dependency a constructor
accepts.

**Violation.** A manager, storage, or service exists only as a concrete
class with no `*Interface` declared; a caller imports a concrete class
where an interface should stand; an operation is declared as a
synchronous method on an interface that describes I/O.

**Severity.** medium

## CON-02 Interfaces are abstract classes with empty bodies, impls subclass them

**Principle.** An interface is an `ABC` whose methods are
`@abstractmethod` with `...` bodies, and an impl subclasses it. The
interpreter refuses an impl that forgot a method, the type checker
holds every impl to the signature, and the interface still reads as
documentation.

**Source.** Interfaces.

**Look for.** Interface declarations; the base list of every impl
class; method bodies inside interface classes.

**Violation.** An interface that is a plain class, so an incomplete
impl instantiates and returns `None`; an interface method carries
logic, defaults, or side effects; an impl does not subclass the
interface it claims to implement; an impl adds public methods the
interface does not declare and callers use them.

**Severity.** medium

## CON-03 At least two impls, technology named last

**Principle.** An interface has at least two impls, a technology impl
and an in-memory impl, and they are interchangeable at wiring time.
Names put the technology last: `WarehouseStoragePostgresImpl`,
`WarehouseStorageMemoryImpl`.

**Source.** Interfaces, Multiple impls per interface.

**Look for.** Impl class names under `impl/` folders; the set of impls
behind each storage and infra interface; the names that appear in
interface signatures and in callers.

**Violation.** An interface has a single impl; an impl name leads with
the technology or omits `Impl`; a technology-specific name leaks into an
interface or a caller.

**Severity.** medium

## CON-04 The in-memory impl is a full implementation

**Principle.** The in-memory impl is a full second implementation, not a
stub: every read, write, filter, and tenancy rule the relational impl
has, the memory impl has too, and the test suite runs both. Every
unique key the schema declares has a contract case, so the memory impl
refuses what the engine refuses.

**Source.** Interfaces, Multiple impls per interface.

**Look for.** The memory impl of every storage interface; the test
fixtures that select an impl; parametrized tests that run against both;
the unique indexes the table classes declare and the contract case
behind each one.

**Violation.** A memory impl raises `NotImplementedError` or returns
empty results for an operation the relational impl supports; a filter
or ordering rule exists only in one impl; the test suite runs storage
tests against one impl only; a unique key with no contract case, so the
memory impl accepts a duplicate the engine refuses and the pair is two
impls of two contracts.

**Severity.** medium

## CON-05 Decoration composes impls behind one interface

**Principle.** Because an impl depends on an interface, impls compose:
a wrapper holds an inner impl of the same interface and forwards
selectively. Decoration is an infrastructure pattern; a manager that
needs a cache takes one through its constructor rather than wrapping
its storage.

**Source.** Interfaces, Composition by decoration.

**Look for.** Wrapper classes for caching, retry, metrics, and tracing;
how a manager obtains caching.

**Violation.** A wrapper exposes a different interface than the impl it
wraps; a manager wraps its storage in a caching decorator instead of
taking a `CacheInterface`; a caller can tell which layer of a composite
answered.

**Severity.** low

## CON-06 Dependencies are injected through constructors, typed by interface

**Principle.** Dependencies are passed into impls through the
constructor and typed by interface, never by impl. A dependency on a
peer manager or a peer storage is an implementation detail of the impl
that holds it; the interface never gains a parameter for it.

**Source.** Interfaces, Injectability; The Business Layer, Cross-Manager
Dependencies.

**Look for.** Constructor signatures of every impl; type annotations of
stored dependencies; any construction of a dependency inside a method;
interface method signatures that mention another manager or storage.

**Violation.** A constructor parameter is annotated with a concrete impl
class or `Any`; an impl instantiates its own storage, cache, or peer
manager; a dependency is fetched from a module-level global, a
registry, or a service locator at call time; an interface method takes
a peer manager or storage as an argument.

**Severity.** medium

## CON-07 Tunables arrive as a frozen options object

**Principle.** A manager that has tunables (a default page size, a lease
length, a threshold) takes a small frozen options object in its
constructor, built once at boot from settings.

**Source.** Interfaces, Injectability.

**Look for.** Constructor parameters that carry numbers, durations, or
limits; module-level constants inside impls that differ between
deployments; whether the options object is frozen and built once.

**Violation.** A tunable is a hard-coded constant that differs between
deployments; tunables arrive as loose positional numbers instead of one
options object; an options object is mutable or is rebuilt per call.

**Severity.** medium

## CON-08 Cycles are broken above the managers

**Principle.** When two managers need each other, the cycle is broken
above them: extract the shared operation into the lower namespace, or
pass a narrow callable for the one operation the upper manager needs.
Reaching into another impl's private attributes after construction is
not wiring. The context module carries ids and facts, never entities,
and imports nothing above the base module.

**Source.** Interfaces, Injectability; OpContext.

**Look for.** The business root's wiring code; assignments to another
object's underscore-prefixed attributes; constructor parameters with a
`None` default that are filled in later.

**Violation.** The root sets `manager._peer = other` after construction;
a dependency is typed optional only to dodge an import cycle; two
namespaces import each other's impls; the context module imports an
entity type, under `TYPE_CHECKING` or otherwise, to embed a user or an
organization.

**Severity.** medium

## CON-09 Roots wire everything at boot

**Principle.** A root class constructs the concrete impls in the right
order and wires them together. The storage, infra, and services roots
expose one getter per member; the business root returns one frozen
object with a field per manager.

**Source.** The Business Layer; Interfaces, Injectability.

**Look for.** The root modules of storage, infra, business, and
services; where impls are instantiated; whether the returned object is
frozen.

**Violation.** An impl is constructed outside a root; a root exposes a
concrete impl type instead of an interface; the business root returns a
mutable container or a dict; wiring is spread across request handlers.

**Severity.** medium

## CON-10 Upper layers depend on lower layers through interfaces only

**Principle.** Three layers: Network, Business, Storage. Upper layers
depend on interfaces exposed by lower layers, never on their internals.
The OM imports infra interfaces; infra imports nothing from the OM.

**Source.** Separation of Layers; Infrastructure, Infrastructure
Principles.

**Look for.** Import graph across the network, business, and storage
packages; what a router imports from a storage package; what a manager
imports from a storage impl; what the infra distribution imports from
the OM.

**Violation.** A router imports a table class or a storage impl; a
manager imports from `storage/impl/` or `storage/tables/`; a service
reads a session or connection object from a storage impl; an infra
module imports from the OM package, so the two distributions depend on
each other.

**Severity.** medium

## CON-11 Infrastructure never leaks a technology across a boundary

**Principle.** Infrastructure capabilities are injected into
any of the three layers and never leak a technology choice across a
boundary.

**Source.** Separation of Layers.

**Look for.** Interface signatures that mention a client library, a
driver, or a vendor type; return types of infra getters; exceptions
that escape an impl.

**Violation.** An interface method takes or returns a vendor client
object; a manager catches a driver-specific exception; a caller
branches on which backend is configured.

**Severity.** medium

## CON-12 Calls flow downward, never up

**Principle.** Services call services and managers; managers call
managers and storage; storage calls storage. Nothing reaches up.
`services.*` exists only in the network layer.

**Source.** The Network Layer, Direction of Calls.

**Look for.** Imports of `services` from any OM or storage module;
imports of managers from any storage module; callbacks that let a lower
layer invoke an upper one.

**Violation.** A manager impl constructor or method holds or calls a
`*ServiceInterface`; a storage impl calls a manager; a lower layer is
handed a callback that invokes an upper layer.

**Severity.** medium

## CON-13 App-specific services stay bounded to one app

**Principle.** An app-specific service impl can call domain services,
but not other app-specific services. If two apps need the same logic,
it belongs in a domain service or in the OM. A domain service cannot
call an app-specific service.

**Source.** The Network Layer, Direction of Calls.

**Look for.** Constructor dependencies of app-specific service impls;
any domain service that imports from an app-specific package.

**Violation.** One app-specific service holds another as a dependency;
shared logic is copied between two app-specific services; a domain
service depends on an app-specific interface.

**Severity.** medium

## CON-14 Cross-service orchestration lives in the service impl

**Principle.** Cross-service orchestration lives in the service impl,
not in the OM, and it composes; it never decides. A service interface
has two impls: the in-process one calls the manager and exists from the
start, since every router calls through it; the remote one is the typed
client, written at the split.

**Source.** The Network Layer, Direction of Calls.

**Look for.** Service impl methods that call more than one namespace;
the parameter list of the manager method such a service impl calls,
which takes the other service's result as a plain argument; manager
methods that sequence calls to another namespace's service-level
operation; what implements each `*ServiceInterface` and which impl the
container wires, since the swap at wiring time is what a split changes.

**Violation.** A manager method's signature accepts a `*ServiceInterface`
or a service impl's dependency handle; a service impl passes its own
dependency into a manager instead of the result it produced; a sequence
that needs a service-level operation of another namespace is written
inside a manager; a service impl that holds a rule (availability,
concurrency) a manager owns; a service interface with only a remote
impl, so the single-process start goes over the wire, or with no
in-process impl from the start, so a router calls a manager directly
and a split rewrites its callers.

**Severity.** medium

## CON-15 A router translates through its service impl, it does not decide

**Principle.** A router builds the entity or the arguments from the
request, calls one operation of its service impl, which calls one
manager, and projects the result onto a view. The router calls through
the service interface from the first day, so a split is a wiring
change. When a router starts deciding something, the decision moves
into a manager.

**Source.** The Network Layer, Service Interfaces and Impls.

**Look for.** Router function bodies: any `if`, `for`, or arithmetic
other than building request arguments and the view; the callee of every
router, which is one operation of the service impl; direct manager or
storage access from a router; domain exceptions raised inside a router.

**Violation.** A router validates a business rule, computes a value, or
branches on entity state; a router calls a manager directly, so a split
rewrites it; a router composes several service operations to enforce a
rule the OM owns; a router calls storage directly; a router raises a
domain exception on its own.

**Severity.** medium

## CON-16 Every process boots through the same container in the same order

**Principle.** Settings are read; logging, error reporting, the trust
store, and tracing are configured; storage is built, then infra, then
the managers, in that order. The container has `start()` and `close()`,
and `close()` unwinds in reverse. A test constructs the same container
over the in-memory storage root and the local infra root.

**Source.** Cross-Cutting Conventions, The App Container.

**Look for.** The container module of each service and worker; the order
of construction; the lifespan hooks; the test fixture that builds the
app.

**Violation.** Managers are built before storage or infra exists; a
worker assembles its dependencies by hand outside a container; `close()`
tears down in construction order or skips a member; the test suite
boots a different assembly than production.

**Severity.** medium

## CON-17 Every write authorizes, verifies, copies, writes

**Principle.** Every write follows four steps: authorize, verify,
copy, write. The caller constructs the entity whole (`id=new_id()`,
timestamps, principals) and hands it to `create_*`; the manager's copy
sets only what is its to decide and leaves the id and timestamps as
constructed. Updates and soft deletes are stamped by the manager's
copy, and a mutating method returns the entity it wrote.

**Source.** The Business Layer, Shape of an Operation.

**Look for.** Manager `create_*`, `update_*`, and `delete_*` bodies: the
read that confirms existence and tenancy before an update, the copy
that sets the actor, the initial status, or a position on create and
`updated_at` / `updated_by` or `deleted_at` / `deleted_by` after, the
return statement; the call site that constructs the entity handed to
`create_*`.

**Violation.** An update writes without first reading the entity back
through the manager's own `get_*`; a manager fills in `id`, a
timestamp, or a principal that the originating caller left unset, or
resets a timestamp the caller constructed; `updated_at`, `updated_by`,
or `deleted_at` is set by the caller or by storage instead of by the
manager; a mutating method returns `None` or a different snapshot than
the one written.

**Severity.** medium

## CON-18 Constructors take structure, contexts take operation state

**Principle.** A constructor takes what an impl needs for its lifetime:
storage, peer managers, infrastructure capabilities, options. A context
carries what one operation needs: state, authority, evidence. A manager
never arrives on a context, nor a request id, an actor, or a tenant
through a constructor. When an operation depends on what the request
established, its stage is in the signature.

**Source.** Interfaces, Injectability; OpContext, Scopes.

**Look for.** Constructor parameters that name a request, a user, or a
tenant; context or scope members that name a manager, a storage, or
the container; any registry or bundle handed out per request or per
stage.

**Violation.** An impl constructed per request to receive the actor or
the tenant; a context, a stage, or a scope with a manager, a storage,
or the container as a member; a per-stage bundle of managers; a
service locator reached from an operation.

**Severity.** medium

## CON-19 The copy on update starts from the stored row

**Principle.** The manager's copy on update starts from the stored row:
the caller's entity supplies the fields a caller may change, and
`PROVENANCE_FIELDS` (`created_at`, `created_by`, `deleted_at`,
`deleted_by`) stay as stored, so no caller rewrites who made a row or
brings a deleted one back by sending an entity. The copy is
`model_validate` over the two dumps, because it carries one.

**Source.** The Business Layer, Shape of an Operation.

**Look for.** The copy in every `update_*`: what it starts from, what
it excludes, and which call builds it.

**Violation.** An update copied from the caller's entity, so a sent
`created_by` or a cleared `deleted_at` is written; an update that
excludes fewer fields than `PROVENANCE_FIELDS`; a `model_copy` fed the
caller's dump.

**Severity.** medium

## CON-20 Roots are built whole, once per process

**Principle.** The storage root constructs every namespace impl, the
infra root every capability impl, and `build_managers` every manager,
in dependency order, once per process; a constructor holds references
and opens nothing, and nothing is built per request. A root that
builds a member on first use is refused, and a test holds that the
managers build once across requests.

**Source.** Cross-Cutting Conventions, The App Container.

**Look for.** The three roots and `build_managers`: whether every
member is constructed in the root's constructor; properties or
getters that construct on first call; anything constructed inside a
request dependency; the build-once test.

**Violation.** A getter that constructs an impl the first time it is
called and caches it; a manager built inside a router dependency; a
constructor that opens a connection; no test counting constructions
across requests, or a root member it does not cover.

**Severity.** medium

## CON-21 A create whose id is already written returns the row as stored

**Principle.** The only way to present a minted id twice is a retry, so
a create whose id is already written returns the row as stored: the
insert reports the existing id, and no check precedes the write.

**Source.** The Business Layer, Shape of an Operation.

**Look for.** Manager `create_*` bodies: what happens when the insert
reports an existing id; any read that precedes the insert; the return
value of the storage create.

**Violation.** A create that raises `Conflict` on its own id, so a
retried request creates twice or fails; a create that checks for the
id and then writes, leaving a window; a create that returns the
caller's entity instead of the row as stored.

**Severity.** medium

## CON-22 A partial update is the router's translation

**Principle.** A partial update is the router's translation: it reads
the current entity, copies the request's set fields onto it, an absent
field meaning unchanged and an explicit null meaning cleared where the
field is optional, and hands the whole entity to the manager. That
policy is the request type's contract, and no impl decides it.

**Source.** The Business Layer, Shape of an Operation.

**Look for.** The router behind every partial update and how it treats
an absent field and a null; whether the request type states the
policy.

**Violation.** A manager or a storage impl that reads a null as
unchanged or an absent field as cleared, deciding what the request
type should state; a router that hands the manager a partial entity.

**Severity.** medium
