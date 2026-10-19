/** Uploading files and folders: each file is sent as the raw body of one request, with its path. */

export interface UploadItem {
  file: File
  /** Path relative to the folder it is uploaded into (keeps a folder's structure). */
  path: string
}

export interface UploadProgress {
  done: number
  total: number
  bytesDone: number
  bytesTotal: number
  current: string
  errors: string[]
}

/** Items from an <input type="file"> (with `webkitdirectory`, files keep their folder paths). */
export function fromInput(files: FileList | null): UploadItem[] {
  return Array.from(files ?? []).map((file) => ({ file, path: file.webkitRelativePath || file.name }))
}

/** Items dropped on the page, folders included (read recursively). */
export async function fromDrop(dt: DataTransfer): Promise<UploadItem[]> {
  const entries = Array.from(dt.items)
    .map((item) => (item.kind === 'file' ? item.webkitGetAsEntry?.() : null))
    .filter((e): e is FileSystemEntry => !!e)
  if (!entries.length) return fromInput(dt.files)
  const out: UploadItem[] = []
  const walk = async (entry: FileSystemEntry, prefix: string): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject))
      out.push({ file, path: prefix + file.name })
      return
    }
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    for (;;) {   // readEntries returns the folder in batches
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
      if (!batch.length) break
      for (const child of batch) await walk(child, `${prefix}${entry.name}/`)
    }
  }
  for (const entry of entries) await walk(entry, '')
  return out
}

function putFile(url: string, file: File, onBytes: (n: number) => void, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)
    xhr.upload.onprogress = (e) => onBytes(e.loaded)
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else {
        let detail = xhr.statusText
        try { detail = JSON.parse(xhr.responseText).detail ?? detail } catch { /* keep the status text */ }
        reject(new Error(String(detail)))
      }
    }
    xhr.onerror = () => reject(new Error('network error'))
    xhr.onabort = () => reject(new Error('cancelled'))
    signal.addEventListener('abort', () => xhr.abort())
    xhr.send(file)
  })
}

/**
 * Upload *items*, a few at a time. `urlFor` gives each item's upload URL. Errors are collected,
 * not thrown; aborting the signal cancels what is left.
 */
export async function uploadAll(
  items: UploadItem[],
  urlFor: (item: UploadItem) => string,
  onProgress: (p: UploadProgress) => void,
  signal: AbortSignal,
  parallel = 3,
): Promise<UploadProgress> {
  const progress: UploadProgress = {
    done: 0, total: items.length, bytesDone: 0, current: '', errors: [],
    bytesTotal: items.reduce((n, it) => n + it.file.size, 0),
  }
  const inFlight = new Map<UploadItem, number>()
  const report = () => onProgress({
    ...progress, bytesDone: progress.bytesDone + [...inFlight.values()].reduce((a, b) => a + b, 0),
  })
  let next = 0
  const worker = async () => {
    while (next < items.length && !signal.aborted) {
      const item = items[next++]
      progress.current = item.path
      inFlight.set(item, 0)
      try {
        await putFile(urlFor(item), item.file, (n) => { inFlight.set(item, n); report() }, signal)
      } catch (err) {
        progress.errors.push(`${item.path}: ${err instanceof Error ? err.message : String(err)}`)
      }
      inFlight.delete(item)
      progress.done += 1
      progress.bytesDone += item.file.size
      report()
    }
  }
  await Promise.all(Array.from({ length: Math.min(parallel, items.length) }, worker))
  return progress
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}
