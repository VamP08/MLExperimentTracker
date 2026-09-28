import { apiFetch } from "./api";

/**
 * Fetch an API path and hand it to the browser as a file.
 *
 * Goes through `apiFetch` rather than an `<a href>` so the static demo can answer from its
 * snapshot too. Throws on a failed response so the caller can say what went wrong.
 */
export async function downloadFrom(path: string, filename: string): Promise<void> {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`Download failed with ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
