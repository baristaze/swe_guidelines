# Signals: api

The API process: every route, behind the gateway that bounds the
requests in flight and limits each caller's rate.

## Outcomes

- `admission_reads` and `admission_writes`: `admitted`, or `refused`
  past the lane's bound of requests in flight, answered as unavailable
  with a `Retry-After`: load the process could not take.
- `rate_limit`: `allowed`, or `rejected`, a caller past its budget;
  `authentication_failed`, a credential refused, counted against its
  address; `address_refused`, an address past its budget of failed
  authentications, refused before the lookup.
