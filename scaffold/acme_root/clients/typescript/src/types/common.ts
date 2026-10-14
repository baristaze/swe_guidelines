// What every namespace shares: the error envelope's parts and the roles.
// An app names each type as common.<Name>, through the package's entry.
import type { components } from "../schema/common";

type Schemas = components["schemas"];

export type OwnedOrgRef = Schemas["OwnedOrgRef"];
export type LastOwnerDetail = Schemas["LastOwnerDetail"];
export type Role = Schemas["Role"];
export type Permission = Schemas["Permission"];
