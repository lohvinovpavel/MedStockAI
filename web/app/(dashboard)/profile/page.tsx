"use client";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useSession } from "@/lib/session";
import { ROLE_LABEL, type Role } from "@/lib/rbac";

function initials(name: string) {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts[parts.length - 1]?.[0] ?? "")).toUpperCase();
}

export default function ProfilePage() {
  const { user } = useSession();
  if (!user) return null;

  const rows: [string, string][] = [
    ["Email", user.email],
    ["Role", ROLE_LABEL[user.role as Role] ?? user.role],
    ["Hospital", user.hospital_name],
    ["User ID", user.user_id],
  ];

  return (
    <div className="flex flex-col gap-4 p-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Profile</h1>
        <p className="text-xs text-muted-foreground">Your account details.</p>
      </div>

      <Card className="max-w-md gap-3 py-4">
        <CardHeader className="flex items-center gap-3 px-4">
          <Avatar className="size-12">
            <AvatarFallback className="text-base">{user.full_name ? initials(user.full_name) : "?"}</AvatarFallback>
          </Avatar>
          <CardTitle className="text-base">{user.full_name ?? "—"}</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 px-4 text-sm">
          {rows.map(([label, value]) => (
            <div key={label} className="flex justify-between border-b pb-2 last:border-0 last:pb-0">
              <span className="text-muted-foreground">{label}</span>
              <span className="font-medium">{value}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
