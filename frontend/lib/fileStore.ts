/**
 * Simple in-memory store for the uploaded video File object.
 * Survives client-side (Next.js) navigation within the same browser tab.
 */
let _uploadedFile: File | null = null;

export function setUploadedFile(file: File): void {
  _uploadedFile = file;
}

export function getUploadedFile(): File | null {
  return _uploadedFile;
}
