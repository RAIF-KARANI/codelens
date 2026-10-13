import "server-only";

/** The dashboard's server-side settings, read from the environment
 * (apps/web/.env.local in development; see .env.example). None of them is
 * NEXT_PUBLIC_, so none reaches the browser. */
export type Config = {
  /** Where the dashboard is served, e.g. http://localhost:3000. GitHub sends
   * the user back to `${appUrl}/api/auth/callback`. */
  appUrl: string;
  /** The CodeLens API, e.g. http://localhost:8000. */
  apiUrl: string;
  /** Shared with the API: signs the 5-minute tokens it accepts. */
  apiJwtSecret: string;
  /** Signs the session cookie. Its own secret, so a session cookie can never
   * pass as an API token, or the other way round. */
  sessionSecret: string;
  /** The GitHub App's OAuth client. */
  githubClientId: string;
  githubClientSecret: string;
};

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set (apps/web/.env.local, see .env.example)`);
  }
  return value;
}

function secret(name: string): string {
  const value = required(name);
  // The API refuses shorter secrets too (HS256 wants at least 256 bits).
  if (new TextEncoder().encode(value).length < 32) {
    throw new Error(`${name} must be at least 32 bytes`);
  }
  return value;
}

export function config(): Config {
  return {
    appUrl: required("APP_URL").replace(/\/$/, ""),
    apiUrl: required("API_URL").replace(/\/$/, ""),
    apiJwtSecret: secret("API_JWT_SECRET"),
    sessionSecret: secret("SESSION_SECRET"),
    githubClientId: required("GITHUB_CLIENT_ID"),
    githubClientSecret: required("GITHUB_CLIENT_SECRET"),
  };
}
