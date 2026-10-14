import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuthSessionStore } from "../store/authSession";

export function RequireAuth({ children }: { children: ReactNode }) {
  const token = useAuthSessionStore((s) => s.token);
  const location = useLocation();
  if (!token) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}
