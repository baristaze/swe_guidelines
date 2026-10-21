// The router catches render errors before any outer boundary sees them, so
// every route reports its own through this element. An address no route
// matches comes here too, as a 404: the not-found page, which reports
// nothing, since a stale link is no fault of the portal's.
import { useEffect } from "react";
import { isRouteErrorResponse, useRouteError } from "react-router-dom";
import { Banner, Page } from "../design/kit";
import { reportError } from "./errors";

export function RouteError() {
  const error = useRouteError();
  const notFound = isRouteErrorResponse(error) && error.status === 404;
  useEffect(() => {
    if (!notFound) reportError(error);
  }, [error, notFound]);
  if (notFound) {
    return (
      <Page title="Page not found">
        <Banner>There is no page at this address.</Banner>
      </Page>
    );
  }
  return (
    <Page title="Something went wrong">
      <Banner>The page failed to load. Reload to try again.</Banner>
    </Page>
  );
}
