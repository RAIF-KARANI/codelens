"use client";

import { PanelLeftCloseIcon, PanelLeftOpenIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { setSidebarCollapsed, useSidebarCollapsed } from "@/lib/use-sidebar";

/** Collapse the sidebar to its icons, or open it again (desktop only). */
export function SidebarToggle() {
  const collapsed = useSidebarCollapsed();
  return (
    <Button
      variant="ghost"
      size="icon"
      className="hidden md:inline-flex"
      aria-label={collapsed ? "Expand the sidebar" : "Collapse the sidebar"}
      onClick={() => setSidebarCollapsed(!collapsed)}
    >
      {/* CSS picks the icon, so the server's HTML is right before the stored
          state is known (the same trick as the theme toggle). */}
      <PanelLeftCloseIcon className="collapsed:hidden" />
      <PanelLeftOpenIcon className="hidden collapsed:block" />
    </Button>
  );
}
