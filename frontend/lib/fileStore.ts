/**
 * Simple in-memory store for the uploaded video source.
 * Survives client-side (Next.js) navigation within the same browser tab.
 *
 * Browser mode : stores a File object (blob URL for playback).
 * Tauri mode   : stores a filesystem path string (converted via convertFileSrc).
 */

let _uploadedFile: File | null = null;
let _uploadedFilePath: string | null = null;

export function setUploadedFile(file: File): void {
  _uploadedFile = file;
  _uploadedFilePath = null;
}

export function getUploadedFile(): File | null {
  return _uploadedFile;
}

export function setUploadedFilePath(path: string): void {
  _uploadedFilePath = path;
  _uploadedFile = null;
}

export function getUploadedFilePath(): string | null {
  return _uploadedFilePath;
}

/** Returns the display name regardless of mode. */
export function getUploadedName(): string | null {
  if (_uploadedFile) return _uploadedFile.name;
  if (_uploadedFilePath) {
    const parts = _uploadedFilePath.replace(/\\/g, "/").split("/");
    return parts[parts.length - 1] ?? null;
  }
  return null;
}
