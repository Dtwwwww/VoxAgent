export function resolveSessionToken(search: string, fallbackToken: string): string {
  const urlToken = new URLSearchParams(search).get("token")?.trim() ?? "";
  return urlToken || fallbackToken.trim();
}
