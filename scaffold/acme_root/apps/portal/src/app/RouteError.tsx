// The router catches render errors before any outer boundary sees them, so
// every route reports its own through this element.
import { useEffect } from "react";
import { useRouteError } from "react-router-dom";
import { Banner, Page } from "../design/kit";
import { reportError } from "./errors";

export function RouteError() {
  const error = useRouteError();
  useEffect(() => {
    reportError(error);
  }, [error]);
  return (
    <Page title="Something went wrong">
      <Banner>The page failed to load. Reload to try again.</Banner>
    </Page>
  );
}
