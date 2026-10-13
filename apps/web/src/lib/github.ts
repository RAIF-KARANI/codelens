import "server-only";

/** GitHub login with the App's OAuth client (D113): the user approves on
 * github.com, GitHub sends them back with a one-time code, and we trade the
 * code for a user token. The token is used for exactly two calls, who the user
 * is and which App installations they can access, and then dropped. */

const GITHUB_WEB = "https://github.com";
const GITHUB_API = "https://api.github.com";
const PER_PAGE = 100;

export class GitHubError extends Error {}

type Fetch = typeof fetch;

/** Where the browser goes to sign in. GitHub Apps have no OAuth scopes: what
 * the token can reach is the App's permissions, limited to what the user can
 * access. */
export function authorizeUrl(clientId: string, redirectUri: string, state: string): string {
  const url = new URL("/login/oauth/authorize", GITHUB_WEB);
  url.search = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    state,
    allow_signup: "false",
  }).toString();
  return url.toString();
}

/** The user token for a callback's `code`. */
export async function exchangeCode(
  args: { clientId: string; clientSecret: string; code: string; redirectUri: string },
  fetchFn: Fetch = fetch,
): Promise<string> {
  const response = await fetchFn(`${GITHUB_WEB}/login/oauth/access_token`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      client_id: args.clientId,
      client_secret: args.clientSecret,
      code: args.code,
      redirect_uri: args.redirectUri,
    }),
    cache: "no-store",
  });
  if (!response.ok) {
    throw new GitHubError(`code exchange failed: HTTP ${response.status}`);
  }
  // A bad or expired code is a 200 with an `error` field.
  const body = (await response.json()) as { access_token?: string; error?: string };
  if (!body.access_token) {
    throw new GitHubError(`code exchange failed: ${body.error ?? "no access_token"}`);
  }
  return body.access_token;
}

export type GitHubUser = { id: number; login: string; name: string | null; avatar_url: string };

export async function fetchUser(token: string, fetchFn: Fetch = fetch): Promise<GitHubUser> {
  const body = await getJson(`${GITHUB_API}/user`, token, fetchFn);
  const { id, login, name, avatar_url } = body as GitHubUser;
  if (!Number.isSafeInteger(id) || typeof login !== "string") {
    throw new GitHubError("unexpected /user response");
  }
  return { id, login, name: name ?? null, avatar_url: avatar_url ?? "" };
}

/** The ids of this App's installations the user can access: the accounts
 * (theirs, their orgs') where the App is installed and the user can see at
 * least one of its repos. */
export async function fetchInstallationIds(
  token: string,
  fetchFn: Fetch = fetch,
): Promise<number[]> {
  const ids: number[] = [];
  for (let page = 1; ; page++) {
    const body = (await getJson(
      `${GITHUB_API}/user/installations?per_page=${PER_PAGE}&page=${page}`,
      token,
      fetchFn,
    )) as { total_count?: number; installations?: { id: number }[] };
    const installations = body.installations ?? [];
    ids.push(...installations.map((i) => i.id));
    if (installations.length < PER_PAGE || ids.length >= (body.total_count ?? 0)) {
      return ids;
    }
  }
}

async function getJson(url: string, token: string, fetchFn: Fetch): Promise<unknown> {
  const response = await fetchFn(url, {
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "User-Agent": "CodeLens",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new GitHubError(`GET ${new URL(url).pathname} failed: HTTP ${response.status}`);
  }
  return response.json();
}
