import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import type { FileNode } from '../types'

interface WorkspacePanelProps {
  campaignId: string
  expId: string
  /** Open this file when the request object changes (e.g. the "Report" button). */
  openRequest?: { path: string } | null
}

interface OpenTab {
  path: string
  name: string
  content: string | null
  binary: boolean
  size: number
  truncated?: boolean
  error?: string
}

const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp', 'ico']

function isImageFile(path: string): boolean {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return IMAGE_EXTS.includes(ext)
}

function fileIconChar(name: string, type: string): string {
  const ext = type.toLowerCase()
  if (ext === 'dir') return '📁'
  if (ext === 'py') return 'π'
  if (ext === 'json' || ext === 'yaml' || ext === 'toml') return '{}'
  if (ext === 'sh' || ext === 'bash') return '$'
  if (ext === 'csv') return 'csv'
  if (ext === 'md') return '≡'
  if (ext === 'txt' || ext === 'log') return '☰'
  if (ext === 'patch' || ext === 'diff') return '±'
  if (IMAGE_EXTS.includes(ext)) return '▦'
  const n = name.toLowerCase()
  if (n === 'dockerfile') return '🐳'
  if (n.startsWith('requirements')) return '📄'
  return '▦'
}

function fileIconColor(name: string, type: string): string {
  const ext = type.toLowerCase()
  if (ext === 'dir') return 'var(--accent)'
  if (ext === 'py') return '#3572A5'
  if (ext === 'json' || ext === 'yaml' || ext === 'toml') return '#f1e05a'
  if (ext === 'sh' || ext === 'bash') return '#89e051'
  if (ext === 'csv') return 'var(--green)'
  if (ext === 'md') return 'var(--fg-dim)'
  if (ext === 'patch' || ext === 'diff') return '#a78bfa'
  if (IMAGE_EXTS.includes(ext)) return '#a78bfa'
  const n = name.toLowerCase()
  if (n === 'dockerfile') return '#2496ed'
  if (n.startsWith('requirements')) return 'var(--fg-dim)'
  return 'var(--fg-dim)'
}

function fileName(path: string): string {
  return path.split('/').pop() || path
}

function TreeNode({ node, depth, selectedPath, onSelect }: {
  node: FileNode
  depth: number
  selectedPath: string | null
  onSelect: (path: string) => void
}) {
  const [expanded, setExpanded] = useState(depth < 2)
  const isDir = node.type === 'dir'
  const isSelected = node.path === selectedPath
  const icon = fileIconChar(node.name, node.type)
  const color = fileIconColor(node.name, node.type)

  return (
    <div>
      <button
        className={`ws-tree-item${isSelected ? ' selected' : ''}`}
        style={{ paddingLeft: 8 + depth * 16 }}
        onClick={() => {
          if (isDir) setExpanded(!expanded)
          else if (node.path) onSelect(node.path)
        }}
      >
        {isDir && (
          <span className="ws-tree-chevron">{expanded ? '▾' : '▸'}</span>
        )}
        <span className="ws-icon" style={{ color, fontWeight: ['py', 'sh', 'bash'].includes(node.type.toLowerCase()) ? 600 : 400 }}>
          {icon}
        </span>
        <span className="ws-tree-name">{node.name}</span>
      </button>
      {isDir && expanded && node.children?.map((child, i) => (
        <TreeNode
          key={child.name + i}
          node={child}
          depth={depth + 1}
          selectedPath={selectedPath}
          onSelect={onSelect}
        />
      ))}
    </div>
  )
}

type Token = { cls: string; text: string }

const PY_KW = new Set([
  'def','class','import','from','return','if','else','elif','for','while',
  'in','not','and','or','is','None','True','False','try','except','finally',
  'with','as','pass','break','continue','raise','yield','lambda','global',
  'nonlocal','assert','del','async','await',
])

function tokenizePython(line: string): Token[] {
  const tokens: Token[] = []
  let i = 0
  while (i < line.length) {
    if (line[i] === '#') {
      tokens.push({ cls: 'cmt', text: line.slice(i) })
      break
    }
    if (line[i] === '"' || line[i] === "'") {
      const q = line[i]
      const triple = line.slice(i, i + 3) === q + q + q
      const end = triple ? q + q + q : q
      const start = i
      i += triple ? 3 : 1
      while (i < line.length) {
        if (line[i] === '\\') { i += 2; continue }
        if (line.slice(i, i + end.length) === end) { i += end.length; break }
        i++
      }
      tokens.push({ cls: 'str', text: line.slice(start, i) })
      continue
    }
    if (/\d/.test(line[i]) && (i === 0 || /[\s=+\-*/<>(,[\]:{}]/.test(line[i - 1]))) {
      const start = i
      while (i < line.length && /[\d._xXoObBeEa-fA-F]/.test(line[i])) i++
      tokens.push({ cls: 'num', text: line.slice(start, i) })
      continue
    }
    if (/[a-zA-Z_]/.test(line[i])) {
      const start = i
      while (i < line.length && /[a-zA-Z0-9_]/.test(line[i])) i++
      const word = line.slice(start, i)
      if (PY_KW.has(word)) tokens.push({ cls: 'kw', text: word })
      else if (i < line.length && line[i] === '(') tokens.push({ cls: 'fn', text: word })
      else tokens.push({ cls: '', text: word })
      continue
    }
    if (line[i] === '@' && /[a-zA-Z_]/.test(line[i + 1] || '')) {
      const start = i; i++
      while (i < line.length && /[a-zA-Z0-9_.]/.test(line[i])) i++
      tokens.push({ cls: 'fn', text: line.slice(start, i) })
      continue
    }
    tokens.push({ cls: '', text: line[i] })
    i++
  }
  return tokens
}

function tokenizeJSON(line: string): Token[] {
  const tokens: Token[] = []
  let i = 0
  while (i < line.length) {
    if (line[i] === '"') {
      const start = i; i++
      while (i < line.length && line[i] !== '"') {
        if (line[i] === '\\') i++
        i++
      }
      if (i < line.length) i++
      const text = line.slice(start, i)
      const after = line.slice(i).trimStart()
      tokens.push({ cls: after.startsWith(':') ? 'kw' : 'str', text })
      continue
    }
    if (/\d/.test(line[i]) || (line[i] === '-' && /\d/.test(line[i + 1] || ''))) {
      const start = i; if (line[i] === '-') i++
      while (i < line.length && /[\d.eE+-]/.test(line[i])) i++
      tokens.push({ cls: 'num', text: line.slice(start, i) })
      continue
    }
    if (line.slice(i, i + 4) === 'true' || line.slice(i, i + 5) === 'false' || line.slice(i, i + 4) === 'null') {
      const w = line.slice(i, i + 4) === 'null' ? 'null' : line.slice(i, i + 4) === 'true' ? 'true' : 'false'
      tokens.push({ cls: 'kw', text: w })
      i += w.length
      continue
    }
    tokens.push({ cls: '', text: line[i] }); i++
  }
  return tokens
}

function tokenizeShell(line: string): Token[] {
  const trimmed = line.trimStart()
  if (trimmed.startsWith('#')) return [{ cls: '', text: line.slice(0, line.length - trimmed.length) }, { cls: 'cmt', text: trimmed }]
  return [{ cls: '', text: line }]
}

function getTokenizer(ext: string): ((line: string) => Token[]) | null {
  if (ext === 'py') return tokenizePython
  if (ext === 'json') return tokenizeJSON
  if (ext === 'sh' || ext === 'bash') return tokenizeShell
  return null
}

function CodeLines({ content, filePath }: { content: string; filePath?: string }) {
  const ext = (filePath?.split('.').pop() || '').toLowerCase()
  const tokenize = useMemo(() => getTokenizer(ext), [ext])

  const lines = useMemo(() => content.split('\n'), [content])

  const renderLine = useCallback((line: string) => {
    if (!tokenize) return <span>{line || '\n'}</span>
    const tokens = tokenize(line)
    if (tokens.length === 0) return <span>{'\n'}</span>
    return <>
      {tokens.map((t, j) =>
        t.cls ? <span key={j} className={`ec-${t.cls}`}>{t.text}</span> : <span key={j}>{t.text}</span>
      )}
    </>
  }, [tokenize])

  return (
    <div className="editor-content">
      {lines.map((line, i) => (
        <div key={i} className="editor-line">
          <span className="editor-gutter">{i + 1}</span>
          <code className="editor-code">{renderLine(line)}</code>
        </div>
      ))}
    </div>
  )
}

function EditorWithLines({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const lines = value.split('\n')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const linesRef = useRef<HTMLDivElement>(null)

  const handleScroll = () => {
    if (textareaRef.current && linesRef.current) {
      linesRef.current.scrollTop = textareaRef.current.scrollTop
    }
  }

  return (
    <div className="ws-editor-wrap">
      <div className="ws-editor-lines" ref={linesRef}>
        {lines.map((_, i) => (
          <span key={i}>{i + 1}</span>
        ))}
      </div>
      <textarea
        ref={textareaRef}
        className="ws-editor"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={handleScroll}
        spellCheck={false}
      />
    </div>
  )
}

export function WorkspacePanel({ campaignId, expId, openRequest }: WorkspacePanelProps) {
  const [files, setFiles] = useState<FileNode[]>([])
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([])
  const [activeTab, setActiveTab] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [treeLoading, setTreeLoading] = useState(true)
  const [treeWidth, setTreeWidth] = useState(200)
  const treeRef = useRef<HTMLDivElement>(null)

  const fetchTree = useCallback(async () => {
    setTreeLoading(true)
    try {
      const data = await api.workspace.tree(campaignId, expId)
      setFiles(data.files || [])
    } catch {
      setFiles([])
    } finally {
      setTreeLoading(false)
    }
  }, [campaignId, expId])

  useEffect(() => { fetchTree() }, [fetchTree])

  const openFile = useCallback(async (path: string) => {
    const existing = openTabs.find(t => t.path === path)
    if (existing) {
      setActiveTab(path)
      setEditing(false)
      setEditContent(null)
      setSaveStatus(null)
      return
    }

    setActiveTab(path)
    setEditing(false)
    setEditContent(null)
    setSaveStatus(null)

    if (isImageFile(path)) {
      const tab: OpenTab = { path, name: fileName(path), content: null, binary: true, size: 0 }
      setOpenTabs(prev => [...prev, tab])
      return
    }

    setLoading(true)
    try {
      const data = await api.workspace.readFile(campaignId, expId, path)
      const tab: OpenTab = {
        path,
        name: fileName(path),
        content: data.content,
        binary: data.binary,
        size: data.size,
        truncated: data.truncated,
      }
      setOpenTabs(prev => [...prev, tab])
    } catch (err) {
      const tab: OpenTab = { path, name: fileName(path), content: null, binary: false, size: 0, error: describeError(err) }
      setOpenTabs(prev => [...prev, tab])
    } finally {
      setLoading(false)
    }
  }, [campaignId, expId, openTabs])

  const handledRequest = useRef<{ path: string } | null>(null)
  useEffect(() => {
    if (openRequest && openRequest !== handledRequest.current) {
      handledRequest.current = openRequest
      openFile(openRequest.path)
    }
  }, [openRequest, openFile])

  const closeTab = useCallback((path: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    setOpenTabs(prev => {
      const next = prev.filter(t => t.path !== path)
      if (activeTab === path) {
        const idx = prev.findIndex(t => t.path === path)
        const newActive = next[Math.min(idx, next.length - 1)]?.path || null
        setActiveTab(newActive)
      }
      return next
    })
    if (activeTab === path) {
      setEditing(false)
      setEditContent(null)
    }
  }, [activeTab])

  const currentTab = openTabs.find(t => t.path === activeTab) || null

  const handleSave = async () => {
    if (!activeTab || editContent === null) return
    setSaving(true)
    setSaveStatus(null)
    try {
      const result = await api.workspace.writeFile(campaignId, expId, activeTab, editContent)
      setOpenTabs(prev => prev.map(t =>
        t.path === activeTab ? { ...t, content: editContent } : t
      ))
      setEditing(false)
      setSaveStatus(result.synced_to_docker ? 'Saved in the container' : 'Saved')
      setTimeout(() => setSaveStatus(null), 3000)
    } catch (err) {
      setSaveStatus(`Error: ${describeError(err)}`)
    } finally {
      setSaving(false)
    }
  }

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = treeWidth
    const onMove = (ev: MouseEvent) => {
      setTreeWidth(Math.max(140, Math.min(320, startWidth - (ev.clientX - startX))))
    }
    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.classList.remove('resizing')
    }
    document.body.classList.add('resizing')
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [treeWidth])

  const showImage = activeTab && isImageFile(activeTab)
  const breadcrumb = activeTab ? `${expId.split('_').pop() || expId} / ${activeTab}` : null

  return (
    <div className="ws-panel">
      {/* Tab bar */}
      <div className="ws-tabbar">
        {openTabs.map(tab => (
          <div
            key={tab.path}
            className={`ws-tab${tab.path === activeTab ? ' active' : ''}`}
            onClick={() => { setActiveTab(tab.path); setEditing(false); setEditContent(null); setSaveStatus(null) }}
          >
            <span className="ws-tab-icon" style={{ color: fileIconColor(tab.name, tab.name.split('.').pop() || '') }}>
              {fileIconChar(tab.name, tab.name.split('.').pop() || '')}
            </span>
            <span className="ws-tab-name">{tab.name}</span>
            <span className="ws-tab-close" onClick={(e) => closeTab(tab.path, e)}>&times;</span>
          </div>
        ))}
      </div>

      {/* Toolbar */}
      {activeTab && (
        <div className="ws-toolbar">
          <span className="ws-breadcrumb">{breadcrumb}</span>
          <div className="ws-toolbar-actions">
            {saveStatus && (
              <span className={`ws-save-status ${saveStatus.startsWith('Error') ? 'error' : 'ok'}`}>
                {saveStatus}
              </span>
            )}
            {editing ? (
              <>
                <button className="ws-tbtn primary" onClick={handleSave} disabled={saving}>
                  {saving ? 'Saving...' : 'Save'}
                </button>
                <button className="ws-tbtn" onClick={() => { setEditing(false); setEditContent(null) }}>
                  &times; Close
                </button>
              </>
            ) : (
              !currentTab?.binary && !currentTab?.error && (
                <button className="ws-tbtn" onClick={() => { setEditing(true); setEditContent(currentTab?.content ?? '') }}>
                  &#9998; Edit
                </button>
              )
            )}
          </div>
        </div>
      )}

      {/* Body: code viewer + resize handle + explorer tree */}
      <div className="ws-body">
        {/* Code viewer (main area) */}
        <div className="ws-viewer">
          {loading ? (
            <div className="ws-empty">Loading file...</div>
          ) : !currentTab ? (
            <div className="ws-placeholder">Select a file to view</div>
          ) : showImage ? (
            <div className="ws-image-wrap">
              <img src={api.workspace.rawUrl(campaignId, expId, activeTab)} alt={activeTab} />
            </div>
          ) : currentTab.error ? (
            <div className="ws-empty">Could not open the file: {currentTab.error}</div>
          ) : currentTab.binary ? (
            <div className="ws-empty">Binary file ({currentTab.size} bytes)</div>
          ) : editing ? (
            <EditorWithLines
              value={editContent ?? ''}
              onChange={setEditContent}
            />
          ) : (
            <CodeLines content={currentTab.content ?? '(empty)'} filePath={activeTab ?? undefined} />
          )}
        </div>

        {/* Resize handle */}
        <div className="resize-handle" onMouseDown={handleResizeStart} />

        {/* Explorer sidebar (right) */}
        <div className="ws-explorer" ref={treeRef} style={{ width: treeWidth }}>
          <div className="ws-explorer-header">
            <span>EXPLORER</span>
            <span className="ws-branch-badge">{expId.split('_').pop() || expId}</span>
            <button className="ws-refresh" onClick={fetchTree} title="Refresh">&#x21BB;</button>
          </div>

          <div className="ws-explorer-section">
            <div className="ws-section-label">WORKSPACE</div>
            <div className="ws-section-tree">
              {treeLoading ? (
                <div className="ws-empty">Loading...</div>
              ) : files.length === 0 ? (
                <div className="ws-empty">No files</div>
              ) : (
                files.map((node, i) => (
                  <TreeNode
                    key={node.name + i}
                    node={node}
                    depth={0}
                    selectedPath={activeTab}
                    onSelect={openFile}
                  />
                ))
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Info bar */}
      {currentTab && !currentTab.binary && !currentTab.error && (
        <div className="editor-info-bar">
          <span>{(activeTab?.split('.').pop() || 'txt').toUpperCase()}</span>
          <span>{(currentTab.content?.split('\n').length ?? 0)} lines</span>
          <span>{currentTab.size > 0 ? `${(currentTab.size / 1024).toFixed(1)} KB` : ''}</span>
          {currentTab.truncated && <span className="info-badge modified">truncated</span>}
        </div>
      )}
    </div>
  )
}
