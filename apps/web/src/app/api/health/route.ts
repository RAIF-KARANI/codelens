/** Is the dashboard's server up? For Docker's health check and the desktop
 * app's start-up wait (planning-v2.md D149). It says nothing else, and in
 * local mode src/proxy.ts still asks for the app's key first. */
export function GET() {
  return Response.json({ status: "ok" }, { headers: { "Cache-Control": "no-store" } });
}
