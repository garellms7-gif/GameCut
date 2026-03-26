/**
 * Tauri runtime detection and URL helpers.
 * All functions are safe to call in a browser context — they just return false/null.
 */

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * Convert a local filesystem path to a URL the Tauri WebView can load.
 * Falls back to null in a plain browser context.
 */
export async function localFileUrl(path: string): Promise<string | null> {
  if (!isTauri()) return null;
  // Dynamic import so the module tree stays valid in plain Next.js builds.
  const { convertFileSrc } = await import("@tauri-apps/api/core");
  return convertFileSrc(path);
}

/**
 * Open the OS-native file-picker dialog.
 * Returns the selected path string, or null if the user cancelled.
 */
export async function pickVideoFile(): Promise<string | null> {
  if (!isTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const result = await open({
    multiple: false,
    filters: [
      { name: "Video", extensions: ["mp4", "mov", "mkv", "avi", "webm", "m4v"] },
    ],
  });
  if (typeof result === "string") return result;
  return null;
}
