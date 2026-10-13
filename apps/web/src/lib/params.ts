/** Reading the URL. Route params and query strings come from the visitor, so
 * anything unexpected is treated as absent (or as not found), never passed on
 * to the API as is. */

/** A database id from a route segment, or null (the page then 404s). */
export function parseId(value: string): number | null {
  if (!/^[1-9]\d{0,15}$/.test(value)) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}

/** `?cursor=` for keyset pagination: an id, or undefined for the first page. */
export function parseCursor(value: string | string[] | undefined): number | undefined {
  const text = first(value);
  return text === undefined ? undefined : (parseId(text) ?? undefined);
}

/** One of `allowed`, or undefined. */
export function parseChoice<T extends string>(
  value: string | string[] | undefined,
  allowed: readonly T[],
): T | undefined {
  const text = first(value);
  return allowed.find((choice) => choice === text);
}

export function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/** A link to `pathname` with these query parameters, leaving out unset ones. */
export function withQuery(
  pathname: string,
  params: Record<string, string | number | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `${pathname}?${text}` : pathname;
}
