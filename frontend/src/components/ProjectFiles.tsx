import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import type { ProjectFileEntry, ProjectFileListing } from '../types'
import { formatBytes, fromDrop, uploadAll, type UploadItem, type UploadProgress } from '../upload'
import { useFilePickers } from '../useFilePickers'

/** Progress of an upload, with a button to cancel it. */
export function UploadStatus({ progress, onCancel }: { progress: UploadProgress; onCancel?: () => void }) {
  const pct = progress.bytesTotal ? Math.round((progress.bytesDone / progress.bytesTotal) * 100) : 100
  const finished = progress.done >= progress.total
  return (
    <div className="upload-status">
      <div className="upload-status-line">
        <span>{finished ? `Uploaded ${progress.total - progress.errors.length} of ${progress.total}`
          : `Uploading ${progress.done + 1} of ${progress.total}: ${progress.current}`}</span>
        <span className="upload-status-bytes">{formatBytes(progress.bytesDone)} / {formatBytes(progress.bytesTotal)}</span>
        {!finished && onCancel && <button className="link-btn" onClick={onCancel}>cancel</button>}
      </div>
      <div className="upload-bar"><div className="upload-bar-fill" style={{ width: `${pct}%` }} /></div>
      {progress.errors.length > 0 && (
        <div className="tl-detail tone-error">{progress.errors.slice(0, 5).join('\n')}{progress.errors.length > 5 ? `\n… ${progress.errors.length - 5} more` : ''}</div>
      )}
    </div>
  )
}

/**
 * The files uploaded to a project (shared by its runs). `mode="pick"` lets the researcher tick
 * files and folders to give an experiment (returned as `project:<path>`).
 */
export function ProjectFilesDialog({ campaignId, mode = 'manage', initial = [], onPick, onClose }: {
  campaignId: string
  mode?: 'manage' | 'pick'
  /** Already chosen (pick mode), as project:<path>. */
  initial?: string[]
  onPick?: (inputs: string[]) => void
  onClose: () => void
}) {
  const [listing, setListing] = useState<ProjectFileListing | null>(null)
  const [folder, setFolder] = useState('')
  const [picked, setPicked] = useState<Set<string>>(() => new Set(initial.filter((x) => x.startsWith('project:')).map((x) => x.slice(8))))
  const [extract, setExtract] = useState(false)
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const [preview, setPreview] = useState<{ path: string; text: string } | null>(null)
  const [newFolder, setNewFolder] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)

  const load = useCallback(async (path: string) => {
    try {
      setListing(await api.projectFiles.list(campaignId, path))
      setError(null)
    } catch (err) {
      setError(describeError(err))
    }
  }, [campaignId])
  useEffect(() => { load(folder) }, [load, folder])
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape' && !progress) onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose, progress])

  const upload = async (items: UploadItem[]) => {
    if (!items.length) return
    const ctl = new AbortController()
    abort.current = ctl
    const prefix = folder ? `${folder}/` : ''
    const result = await uploadAll(items, (it) => api.projectFiles.uploadUrl(campaignId, prefix + it.path, extract),
      setProgress, ctl.signal)
    abort.current = null
    const tops = [...new Set(items.map((it) => prefix + it.path.split('/')[0]))]
    if (result.done > result.errors.length) api.projectFiles.noteUploaded(campaignId, tops).catch(() => {})
    load(folder)
  }
  const { inputs, pick } = useFilePickers(upload)

  const open = async (e: ProjectFileEntry) => {
    if (e.type === 'dir') {
      setFolder(e.path)
      setPreview(null)
      return
    }
    try {
      const r = await api.projectFiles.preview(campaignId, e.path)
      setPreview({ path: e.path, text: r.binary ? `Binary file, ${formatBytes(r.size)}` : `${r.content}${r.truncated ? '\n…' : ''}` })
    } catch (err) {
      setError(describeError(err))
    }
  }
  const remove = async (e: ProjectFileEntry) => {
    try {
      await api.projectFiles.remove(campaignId, e.path)
      setPicked((s) => { const n = new Set(s); n.delete(e.path); return n })
      load(folder)
    } catch (err) {
      setError(describeError(err))
    }
  }
  const makeFolder = async () => {
    const name = (newFolder ?? '').trim()
    setNewFolder(null)
    if (!name) return
    try {
      await api.projectFiles.mkdir(campaignId, folder ? `${folder}/${name}` : name)
      load(folder)
    } catch (err) {
      setError(describeError(err))
    }
  }
  const toggle = (path: string) => setPicked((s) => {
    const n = new Set(s)
    if (n.has(path)) n.delete(path)
    else n.add(path)
    return n
  })

  const crumbs = folder ? folder.split('/') : []
  const usagePct = listing ? Math.min(100, (listing.usage_bytes / listing.max_project_bytes) * 100) : 0
  const busy = !!progress && progress.done < progress.total

  return (
    <div className="modal-backdrop" onMouseDown={() => { if (!busy) onClose() }}>
      <div className={`modal files-modal${dragging ? ' dragging' : ''}`} role="dialog" aria-modal="true"
        onMouseDown={(e) => e.stopPropagation()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false) }}
        onDrop={async (e) => { e.preventDefault(); setDragging(false); upload(await fromDrop(e.dataTransfer)) }}>
        {inputs}
        <div className="modal-title">{mode === 'pick' ? 'Choose files for the experiment' : 'Project files'}</div>
        <div className="files-toolbar">
          <button className="btn-sm" disabled={busy} onClick={() => pick('files')}>Upload files</button>
          <button className="btn-sm" disabled={busy} onClick={() => pick('folder')}>Upload folder</button>
          <button className="btn-sm" disabled={busy} onClick={() => setNewFolder('')}>New folder</button>
          <label className="files-extract" title="Unpack .zip and .tar.gz files into a folder after uploading them">
            <input type="checkbox" checked={extract} onChange={(e) => setExtract(e.target.checked)} /> unpack archives
          </label>
        </div>
        <div className="files-crumbs">
          <button className="link-btn" onClick={() => setFolder('')}>files</button>
          {crumbs.map((part, i) => (
            <span key={i}> / <button className="link-btn" onClick={() => setFolder(crumbs.slice(0, i + 1).join('/'))}>{part}</button></span>
          ))}
        </div>
        {newFolder !== null && (
          <div className="files-newfolder">
            <input autoFocus value={newFolder} placeholder="folder name" onChange={(e) => setNewFolder(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) makeFolder(); if (e.key === 'Escape') setNewFolder(null) }} />
            <button className="btn-sm" onClick={makeFolder}>Create</button>
          </div>
        )}
        {error && <div className="modal-error" style={{ fontSize: 11 }}>{error}</div>}
        <div className="files-body">
          <div className="files-list">
            {listing && listing.entries.length === 0 && (
              <div className="panel-empty">No files here yet. Upload them, or drop files and folders on this window.</div>
            )}
            {listing?.entries.map((e) => (
              <div key={e.path} className={`files-row${preview?.path === e.path ? ' selected' : ''}`}>
                {mode === 'pick' && (
                  <input type="checkbox" checked={picked.has(e.path)} onChange={() => toggle(e.path)} title="Give it to the experiment" />
                )}
                <button className="files-name" onClick={() => open(e)}>
                  <span className="files-icon">{e.type === 'dir' ? '📁' : '📄'}</span>{e.name}
                </button>
                <span className="files-size">{e.type === 'dir' ? '' : formatBytes(e.size ?? 0)}</span>
                {mode === 'manage' && (
                  <button className="link-btn danger" title="Delete" onClick={() => remove(e)}>delete</button>
                )}
              </div>
            ))}
          </div>
          {preview && <pre className="files-preview">{preview.text}</pre>}
        </div>
        {progress && <UploadStatus progress={progress} onCancel={() => abort.current?.abort()} />}
        <div className="files-footer">
          {listing && (
            <div className="files-usage" title={`Largest file: ${formatBytes(listing.max_file_bytes)}`}>
              <div className="upload-bar"><div className="upload-bar-fill" style={{ width: `${usagePct}%` }} /></div>
              {formatBytes(listing.usage_bytes)} of {formatBytes(listing.max_project_bytes)}
            </div>
          )}
          <div className="modal-actions" style={{ marginTop: 0 }}>
            {mode === 'pick' ? (
              <>
                <button className="btn-sm" disabled={busy} onClick={onClose}>Cancel</button>
                <button className="btn-sm btn-approve" disabled={busy}
                  onClick={() => { onPick?.([...picked].sort().map((p) => `project:${p}`)); onClose() }}>
                  Use {picked.size} item{picked.size === 1 ? '' : 's'}
                </button>
              </>
            ) : (
              <button className="btn-sm" disabled={busy} onClick={onClose}>Close</button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
