import { useMe, useUsers } from "../../queries/tenancy";
import { homeCard } from "./homeModel";

/** The signed-in person's place: the org, who they are in it, and how many
 * are in it with them. A push about a member or a membership refreshes it. */
export function useHomeVm() {
  const me = useMe();
  const users = useUsers();
  const loading = me.isPending || users.isPending;
  return {
    loading,
    error: me.error ?? users.error,
    card: me.data && !loading ? homeCard(me.data, users.data?.length ?? 0) : null,
  };
}
