import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";
import { UpgradeDialog } from "../features/billing/UpgradeDialog";
import { Notices } from "./Notices";
import { queryClient } from "./queryClient";
import { router } from "./routes";

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Notices />
      <UpgradeDialog />
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
