import { Card, Muted } from "../../design/kit";
import { useStorageVm } from "./useStorageVm";

export function StorageCard() {
  const vm = useStorageVm();
  return (
    <Card title="Storage used">
      {vm.loading || vm.line === null ? <Muted>Loading</Muted> : <span>{vm.line}</span>}
    </Card>
  );
}
