import { NextRequest, NextResponse } from "next/server";

// Lightweight lead-capture endpoint for the landing page's email forms.
// No CRM is wired up yet - this just validates and logs server-side so the
// form is honest about what it does (no fake "we'll be in touch" promise
// backed by nothing). Swap this for a real CRM/webhook call when ready.
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const email = body?.email;

  if (!email || typeof email !== "string" || !email.includes("@")) {
    return NextResponse.json({ error: "A valid email is required." }, { status: 400 });
  }

  console.log(`[Kinato Landing] New lead captured: ${email}`);

  return NextResponse.json({ status: "ok" });
}
