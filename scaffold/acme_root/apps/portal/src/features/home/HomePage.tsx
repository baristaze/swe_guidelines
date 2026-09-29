import { AppNav } from "../../app/AppNav";
import { Banner, Card, Muted, Page } from "../../design/kit";
import { tokens } from "../../design/tokens";
import { useHomeVm } from "./useHomeVm";

const label = { color: tokens.color.muted } as const;
const value = { margin: 0 } as const;

export function HomePage() {
  const vm = useHomeVm();
  return (
    <Page title="Home" nav={<AppNav />}>
      {vm.error ? <Banner>{vm.error.message}</Banner> : null}
      {vm.card === null ? (
        <Card>
          <Muted>Loading</Muted>
        </Card>
      ) : (
        <Card title={vm.card.orgName} id="org">
          <dl
            data-home
            style={{
              display: "grid",
              gridTemplateColumns: "max-content 1fr",
              gap: `${tokens.space.sm} ${tokens.space.md}`,
              margin: 0,
            }}
          >
            <dt style={label}>You</dt>
            <dd style={value}>{vm.card.personName}</dd>
            <dt style={label}>Role</dt>
            <dd style={value}>{vm.card.role}</dd>
            <dt style={label}>Members</dt>
            <dd style={value}>{vm.card.members}</dd>
          </dl>
        </Card>
      )}
      <Muted style={{ fontSize: tokens.font.size.sm }}>The product&apos;s own screens go here.</Muted>
    </Page>
  );
}
