import type { ReactNode } from 'react'

/** A small Markdown renderer for the documentation (headings, paragraphs, lists, tables, code,
 *  quotes, images, links): React elements, never injected HTML. */
export function Markdown({ text, onLink, imageUrl }: {
  text: string
  /** A link was clicked: a page of the documentation (relative .md path) or an outside address. */
  onLink: (href: string) => void
  imageUrl: (src: string) => string
}) {
  const lines = text.replace(/\r/g, '').split('\n')
  const out: ReactNode[] = []
  let i = 0
  let key = 0
  const inline = (s: string) => renderInline(s, onLink, imageUrl)
  while (i < lines.length) {
    const line = lines[i]
    if (/^```/.test(line)) {
      const body: string[] = []
      i++
      while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++])
      i++
      out.push(<pre key={key++} className="md-code"><code>{body.join('\n')}</code></pre>)
      continue
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const level = h[1].length
      const content = inline(h[2])
      out.push(level === 1 ? <h1 key={key++}>{content}</h1> : level === 2 ? <h2 key={key++}>{content}</h2>
        : level === 3 ? <h3 key={key++}>{content}</h3> : <h4 key={key++}>{content}</h4>)
      i++
      continue
    }
    if (/^\s*\|/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
      const head = cells(line)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]))
      out.push(
        <div key={key++} className="md-table"><table>
          <thead><tr>{head.map((c, j) => <th key={j}>{inline(c)}</th>)}</tr></thead>
          <tbody>{rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}>{inline(c)}</td>)}</tr>)}</tbody>
        </table></div>)
      continue
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items: string[] = []
      while (i < lines.length && (/^\s*([-*]|\d+\.)\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        if (/^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ''))
        else items[items.length - 1] += ' ' + lines[i].trim()
        i++
      }
      const lis = items.map((t, j) => <li key={j}>{inline(t)}</li>)
      out.push(ordered ? <ol key={key++}>{lis}</ol> : <ul key={key++}>{lis}</ul>)
      continue
    }
    if (/^>\s?/.test(line)) {
      const body: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ''))
      out.push(<blockquote key={key++}>{inline(body.join(' '))}</blockquote>)
      continue
    }
    if (!line.trim()) { i++; continue }
    const para: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|>|\s*\||\s*([-*]|\d+\.)\s)/.test(lines[i])) para.push(lines[i++])
    out.push(<p key={key++}>{inline(para.join(' '))}</p>)
  }
  return <div className="md">{out}</div>
}

const INLINE = /(!\[[^\]]*\]\([^)]+\)|\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|<https?:\/\/[^>]+>)/g

function renderInline(s: string, onLink: (href: string) => void, imageUrl: (src: string) => string): ReactNode[] {
  const parts = s.split(INLINE)
  return parts.map((p, i) => {
    if (!p) return null
    let m = p.match(/^!\[([^\]]*)\]\(([^)]+)\)$/)
    if (m) return <img key={i} src={imageUrl(m[2])} alt={m[1]} className="md-img" />
    m = p.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (m) {
      const href = m[2]
      return <a key={i} href={href.startsWith('http') ? href : '#'} target={href.startsWith('http') ? '_blank' : undefined}
        rel="noreferrer" onClick={(e) => { if (!href.startsWith('http')) { e.preventDefault(); onLink(href) } }}>
        {renderInline(m[1], onLink, imageUrl)}</a>
    }
    m = p.match(/^<(https?:\/\/[^>]+)>$/)
    if (m) return <a key={i} href={m[1]} target="_blank" rel="noreferrer">{m[1]}</a>
    if (/^`[^`]+`$/.test(p)) return <code key={i}>{p.slice(1, -1)}</code>
    if (/^\*\*[^*]+\*\*$/.test(p)) return <b key={i}>{p.slice(2, -2)}</b>
    if (/^\*[^*\s][^*]*\*$/.test(p)) return <i key={i}>{p.slice(1, -1)}</i>
    return p
  })
}
