import type { Attachment } from '../useAttachments'

function badge(att: Attachment): string {
  if (att.kind === 'folder') return 'FOLDER'
  const ext = att.name.includes('.') ? att.name.split('.').pop()! : ''
  return ext ? ext.toUpperCase().slice(0, 6) : 'FILE'
}

/** The files and folders attached to the message being written. */
export function AttachmentCards({ attachments, onRemove }: {
  attachments: Attachment[]
  onRemove: (id: string) => void
}) {
  if (!attachments.length) return null
  return (
    <div className="att-cards">
      {attachments.map((a) => {
        const pct = a.total ? Math.round((a.loaded / a.total) * 100) : 100
        return (
          <div key={a.id} className={`att-card${a.previewUrl ? ' image' : ''}${a.status === 'error' ? ' failed' : ''}`}
            title={a.status === 'error' ? `${a.name}: ${a.error}` : a.path}>
            {a.previewUrl ? (
              <img src={a.previewUrl} alt={a.name} />
            ) : (
              <>
                <div className="att-name">{a.kind === 'folder' ? '📁 ' : ''}{a.name}</div>
                <div className="att-foot">
                  <span className="att-badge">{badge(a)}</span>
                  {a.kind === 'folder' && <span className="att-count">{a.count} files</span>}
                  {a.kind === 'existing' && <span className="att-count">project file</span>}
                </div>
              </>
            )}
            {a.status === 'uploading' && <div className="att-progress"><div style={{ width: `${pct}%` }} /></div>}
            {a.status === 'error' && <div className="att-error">upload failed</div>}
            <button className="att-remove" aria-label={`Remove ${a.name}`} onClick={() => onRemove(a.id)}>×</button>
          </div>
        )
      })}
    </div>
  )
}

/** Attachments of a sent message, small, under its text. */
export function SentAttachments({ paths }: { paths: string[] }) {
  if (!paths.length) return null
  return (
    <div className="att-sent">
      {paths.map((p) => (
        <span key={p} className="att-chip" title={p}>
          <span className="att-badge">{p.includes('.') ? p.split('.').pop()!.toUpperCase().slice(0, 5) : 'DIR'}</span>
          {p.split('/').pop() || p}
        </span>
      ))}
    </div>
  )
}
