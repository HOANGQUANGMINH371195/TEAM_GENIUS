import type { Metadata } from "next";
import { AdminAuthProvider } from "../../components/admin/auth-context";
import { AdminRouteFrame } from "../../components/admin/admin-route-frame";
import "./admin.css";

export const metadata: Metadata = {
  title: "BHYT Admin Portal",
  description: "Cổng giám sát vận hành BHYT.",
};

export default function AdminLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <AdminAuthProvider><AdminRouteFrame>{children}</AdminRouteFrame></AdminAuthProvider>;
}
