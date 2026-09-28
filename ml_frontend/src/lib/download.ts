import { apiFetch } from "./api";

/** Fetch an API path and save it as a file. Uses apiFetch so the demo works too. Throws on failure. */
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
