"""Third-party providers. Each has one interface, a real client, and a
deterministic twin that tests and the local stack run against. Nothing here
imports the object model; a provider's errors root at infra's exception
family, and the container wires one impl of each at boot, like a backend of
the infra root."""
