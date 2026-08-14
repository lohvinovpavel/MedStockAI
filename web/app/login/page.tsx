"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Plus, ShieldCheck, Stethoscope, ClipboardList, UserCog, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel, FieldSeparator } from "@/components/ui/field";
import { Input } from "@/components/ui/input";

const DEMO_ROLES = [
  { role: "Chief Pharmacist", email: "pharmacist@medstock.demo", icon: Stethoscope },
  { role: "Procurement Officer", email: "procurement@medstock.demo", icon: ClipboardList },
  { role: "Clinical Director", email: "director@medstock.demo", icon: UserCog },
] as const;

export default function LoginPage() {
  const router = useRouter();
  const [step, setStep] = useState<"credentials" | "otp">("credentials");
  const [pending, setPending] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");

  function submitCredentials(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    window.setTimeout(() => {
      setPending(false);
      setStep("otp");
    }, 500);
  }

  function submitOtp(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    window.setTimeout(() => {
      toast.success("Signed in.");
      router.push("/inventory");
    }, 500);
  }

  function demoLogin(role: string) {
    setPending(true);
    window.setTimeout(() => {
      toast.success(`Signed in as ${role}.`);
      router.push("/inventory");
    }, 400);
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-muted/40 p-4">
      <div className="mb-6 flex items-center gap-2">
        <span className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Plus className="size-4.5" strokeWidth={3} />
        </span>
        <span className="text-base font-semibold tracking-tight">MedStock AI</span>
      </div>

      <Card className="w-full max-w-sm gap-4 py-6">
        <CardHeader className="px-6">
          <CardTitle className="text-base">
            {step === "credentials" ? "Sign in to your facility" : "Verify your identity"}
          </CardTitle>
          <CardDescription className="text-xs">
            {step === "credentials"
              ? "Clinical staff sign-in — 2FA is required for every session."
              : `Enter the 6-digit code sent to ${email || "your device"}.`}
          </CardDescription>
        </CardHeader>
        <CardContent className="px-6">
          {step === "credentials" ? (
            <>
              <form onSubmit={submitCredentials}>
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="login-email">Email</FieldLabel>
                    <Input
                      id="login-email"
                      type="email"
                      autoComplete="username"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@hospital.org"
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="login-password">Password</FieldLabel>
                    <Input
                      id="login-password"
                      type="password"
                      autoComplete="current-password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                    />
                  </Field>
                  <Button type="submit" className="w-full" disabled={pending}>
                    {pending && <Loader2 className="animate-spin" data-icon="inline-start" />}
                    Continue
                  </Button>
                </FieldGroup>
              </form>

              <FieldSeparator className="my-4">or try a demo role</FieldSeparator>

              <div className="flex flex-col gap-2">
                {DEMO_ROLES.map(({ role, icon: Icon }) => (
                  <Button
                    key={role}
                    type="button"
                    variant="outline"
                    className="w-full justify-start text-xs"
                    disabled={pending}
                    onClick={() => demoLogin(role)}
                  >
                    <Icon data-icon="inline-start" />
                    Demo Login as {role}
                  </Button>
                ))}
              </div>
            </>
          ) : (
            <form onSubmit={submitOtp}>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="login-otp">Verification code</FieldLabel>
                  <Input
                    id="login-otp"
                    inputMode="numeric"
                    maxLength={6}
                    required
                    value={otp}
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
                    placeholder="123456"
                    className="tracking-[0.3em]"
                  />
                </Field>
                <Button type="submit" className="w-full" disabled={pending || otp.length < 6}>
                  {pending && <Loader2 className="animate-spin" data-icon="inline-start" />}
                  Verify & Continue
                </Button>
                <Button type="button" variant="ghost" className="w-full text-xs" onClick={() => setStep("credentials")}>
                  Back
                </Button>
              </FieldGroup>
            </form>
          )}
        </CardContent>
      </Card>

      <p className="mt-5 flex max-w-sm items-start gap-1.5 text-center text-[11px] text-muted-foreground">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
        Authorized Clinical Personnel Only. All access is logged per ISO-27001.
      </p>

      <Link href="/" className="mt-3 text-[11px] text-muted-foreground underline-offset-4 hover:underline">
        ← Back to overview
      </Link>
    </div>
  );
}
