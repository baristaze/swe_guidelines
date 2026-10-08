import { Card, Muted } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { useStorageVm } from "./useStorageVm";

export function StorageCard() {
  const vm = useStorageVm();
  return (
    <Card title="Storage used">
      {vm.loading || vm.line === null ? <Muted>Loading</Muted> : <span>{vm.line}</span>}
      {vm.notice ? <Muted style={{ display: "block", marginTop: tokens.space.sm }}>{vm.notice}</Muted> : null}
    </Card>
  );
}
