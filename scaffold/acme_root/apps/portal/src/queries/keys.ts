// One key factory per domain. The first element is the entity name the
// server uses in its `entity_changed` pushes, so the realtime router can
// invalidate by convention; an entity the convention does not reach on its
// own (a membership, read through `me`; a user, read through `me` as well as
// its own list) is named in the router's table instead.
export const keys = {
  me: ["me"] as const,
  // The person behind the session: their address and their time zone.
  identity: ["identity"] as const,
  // The person's places across orgs, read with the session under the
  // identity stage. A switch drops it with every other key.
  myMemberships: {
    all: ["my_membership"] as const,
    list: (limit: number) => ["my_membership", "list", limit] as const,
  },
  // The org's memberships: the role beside each member in Settings. A
  // `tenancy.membership.*` push reaches it through the router's table.
  memberships: {
    all: ["membership"] as const,
    list: (limit: number) => ["membership", "list", limit] as const,
  },
  users: {
    all: ["user"] as const,
    list: (limit: number) => ["user", "list", limit] as const,
  },
  apiKeys: {
    all: ["api_key"] as const,
    list: (limit: number) => ["api_key", "list", limit] as const,
  },
  // The org's files. A `media.file.*` push refreshes the usage, since the
  // entity is `file`.
  files: {
    all: ["file"] as const,
    usage: ["file", "usage"] as const,
  },
  // The session's flags, one snapshot. No push names them, so they are read
  // again on focus and on an interval (flags.ts).
  flags: ["flags"] as const,
  // A `tenancy.invitation.*` push invalidates these by convention.
  invitations: {
    all: ["invitation"] as const,
    list: (limit: number) => ["invitation", "list", limit] as const,
  },
};
