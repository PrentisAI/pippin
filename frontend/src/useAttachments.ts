import { useCallback, useEffect, useRef, useState } from 'react'
import { uploadAll, type UploadItem } from './upload'

/** A file or folder attached to a message: uploaded as soon as it is added, sent with the message. */
export interface Attachment {
  id: string
  /** What is sent with the message: the path it was uploaded to (relative to where uploads go). */
  path: string
  name: string
  kind: 'file' | 'folder' | 'existing'
  /** Files in a folder. */
  count: number
  /** For images: a local preview. */
  previewUrl?: string
  status: 'uploading' | 'done' | 'error'
  loaded: number
  total: number
  error?: string
}

/**
 * Attachments of the message being written. `urlFor(path)` is where a file is uploaded;
 * `onDiscard(path)` removes an uploaded one the researcher takes off the message again.
 */
export function useAttachments(urlFor: (path: string) => string, onDiscard?: (path: string) => void) {
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const aborts = useRef(new Map<string, AbortController>())
  const update = (id: string, patch: Partial<Attachment>) =>
    setAttachments((list) => list.map((a) => (a.id === id ? { ...a, ...patch } : a)))

  const add = useCallback((items: UploadItem[]) => {
    // a folder (items sharing a first path segment) is one attachment; a file is one
    const groups = new Map<string, UploadItem[]>()
    for (const it of items) {
      const top = it.path.includes('/') ? it.path.split('/')[0] : it.path
      groups.set(top, [...(groups.get(top) ?? []), it])
    }
    for (const [top, group] of groups) {
      const folder = group.length > 1 || group[0].path.includes('/')
      const id = `${top}-${Math.random().toString(36).slice(2, 8)}`
      const file = group[0].file
      const att: Attachment = {
        id, path: top, name: top, kind: folder ? 'folder' : 'file', count: group.length,
        previewUrl: !folder && file.type.startsWith('image/') ? URL.createObjectURL(file) : undefined,
        status: 'uploading', loaded: 0, total: group.reduce((n, it) => n + it.file.size, 0),
      }
      setAttachments((list) => [...list, att])
      const ctl = new AbortController()
      aborts.current.set(id, ctl)
      uploadAll(group, (it) => urlFor(it.path), (p) => update(id, { loaded: p.bytesDone }), ctl.signal)
        .then((p) => {
          aborts.current.delete(id)
          if (ctl.signal.aborted) return
          update(id, p.errors.length
            ? { status: 'error', error: p.errors[0] }
            : { status: 'done', loaded: att.total })
        })
    }
  }, [urlFor])

  /** Files already in place (e.g. chosen from the project's files). */
  const addExisting = useCallback((paths: string[]) => {
    setAttachments((list) => [...list, ...paths.filter((p) => !list.some((a) => a.path === p)).map((p) => ({
      id: `${p}-${Math.random().toString(36).slice(2, 8)}`, path: p, name: p.split('/').pop() || p,
      kind: 'existing' as const, count: 1, status: 'done' as const, loaded: 0, total: 0,
    }))])
  }, [])

  const remove = useCallback((id: string) => {
    setAttachments((list) => {
      const att = list.find((a) => a.id === id)
      if (att) {
        aborts.current.get(id)?.abort()
        if (att.previewUrl) URL.revokeObjectURL(att.previewUrl)
        if (att.status === 'done' && att.kind !== 'existing') onDiscard?.(att.path)
      }
      return list.filter((a) => a.id !== id)
    })
  }, [onDiscard])

  /** After sending: the attachments belong to the message now. */
  const clear = useCallback(() => setAttachments([]), [])

  useEffect(() => () => {
    for (const ctl of aborts.current.values()) ctl.abort()
  }, [])

  return {
    attachments, add, addExisting, remove, clear,
    uploading: attachments.some((a) => a.status === 'uploading'),
    ready: attachments.filter((a) => a.status === 'done').map((a) => a.path),
  }
}
