"""The one Python client of the Acme API. `types` is the facade over the
generated `schema`, one module per namespace; `transport` the one transport;
`calls` each namespace's operations, which `client` composes into `ApiClient`;
`realtime` the channel."""
