// The media namespace's types: files and the org's storage. An app names
// each as media.<Name>, through the package's entry.
import type { components } from "../schema/media";

type Schemas = components["schemas"];

export type FileView = Schemas["FileView"];
export type IssuedUploadView = Schemas["IssuedUploadView"];
export type IssuedDownloadView = Schemas["IssuedDownloadView"];
export type StorageUsageView = Schemas["StorageUsageView"];
