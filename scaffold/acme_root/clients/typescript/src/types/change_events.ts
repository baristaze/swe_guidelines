// The change_events namespace's types: the stream and the live channel's
// ticket. An app names each as change_events.<Name>, through the package's entry.
import type { components } from "../schema/change_events";

type Schemas = components["schemas"];

export type ChangeEventView = Schemas["ChangeEventView"];
export type IssuedTicketView = Schemas["IssuedTicketView"];
