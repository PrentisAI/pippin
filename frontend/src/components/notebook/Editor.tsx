/** Writing at length: headings, lists, quotes, code, saved as you type (a second after you stop). A clip can be
 *  put in where the cursor is, as a quote with where it comes from. */
import { useEffect, useRef, useState } from 'react'
import { EditorContent, useEditor } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import type { NbClip } from '../../types'

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** A clip as it goes into a note: the words, then where they are from, then yours. */
export function clipHtml(c: NbClip): string {
  return `<blockquote><p>${esc(c.quote)}</p><p>— ${esc(c.paper_title || c.paper_id)}, p. ${c.page_index + 1}</p></blockquote>`
    + (c.words ? `<p>${esc(c.words)}</p>` : '<p></p>')
}

export function Editor({ html, onSave, placeholder, clips = [], autofocus = false }: {
  html: string
  onSave: (html: string, text: string) => Promise<unknown> | void
  placeholder: string
  /** Clips that can be put in (yours, newest first). */
  clips?: NbClip[]
  autofocus?: boolean
}) {
  const [state, setState] = useState<'saved' | 'saving' | 'dirty'>('saved')
  const [picking, setPicking] = useState(false)
  const timer = useRef<number | null>(null)
  const save = useRef(onSave)
  useEffect(() => { save.current = onSave }, [onSave])
  const editor = useEditor({
    extensions: [StarterKit, Placeholder.configure({ placeholder })],
    content: html || '',
    autofocus: autofocus ? 'end' : false,
    onUpdate: ({ editor: ed }) => {
      setState('dirty')
      if (timer.current) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(async () => {
        setState('saving')
        try { await save.current(ed.getHTML(), ed.getText()) } finally { setState('saved') }
      }, 900)
    },
  })
  // what was typed last is saved when the editor goes (another note opened, the page left)
  useEffect(() => () => {
    if (timer.current && editor) { window.clearTimeout(timer.current); save.current(editor.getHTML(), editor.getText()) }
  }, [editor])
  if (!editor) return null
  const b = (label: string, on: boolean, run: () => void, title: string) => (
    <button type="button" className={on ? 'on' : ''} title={title} aria-pressed={on} onMouseDown={(e) => { e.preventDefault(); run() }}>{label}</button>
  )
  return (
    <div className="nbe">
      <div className="nbe-bar" role="toolbar" aria-label="Formatting">
        {b('H', editor.isActive('heading', { level: 2 }), () => editor.chain().focus().toggleHeading({ level: 2 }).run(), 'Heading')}
        {b('B', editor.isActive('bold'), () => editor.chain().focus().toggleBold().run(), 'Bold (⌘B)')}
        {b('I', editor.isActive('italic'), () => editor.chain().focus().toggleItalic().run(), 'Italic (⌘I)')}
        {b('•', editor.isActive('bulletList'), () => editor.chain().focus().toggleBulletList().run(), 'List')}
        {b('1.', editor.isActive('orderedList'), () => editor.chain().focus().toggleOrderedList().run(), 'Numbered list')}
        {b('“', editor.isActive('blockquote'), () => editor.chain().focus().toggleBlockquote().run(), 'Quote')}
        {b('</>', editor.isActive('codeBlock'), () => editor.chain().focus().toggleCodeBlock().run(), 'Code')}
        {clips.length > 0 && (
          <span className="nbe-pick">
            <button type="button" onMouseDown={(e) => { e.preventDefault(); setPicking(!picking) }} aria-expanded={picking}>Insert a clip</button>
            {picking && (
              <div className="nbe-menu" role="menu">
                {clips.slice(0, 30).map((c) => (
                  <button key={c.clip_id} role="menuitem" onMouseDown={(e) => {
                    e.preventDefault(); setPicking(false); editor.chain().focus().insertContent(clipHtml(c)).run()
                  }}>
                    <span>“{c.quote.length > 90 ? `${c.quote.slice(0, 89)}…` : c.quote}”</span>
                    <span className="lit-sub">{c.paper_title || c.paper_id} · p. {c.page_index + 1}</span>
                  </button>
                ))}
              </div>
            )}
          </span>
        )}
        <span className="nbe-state">{state === 'saved' ? 'Saved' : state === 'saving' ? 'Saving…' : ''}</span>
      </div>
      <EditorContent editor={editor} className="nbe-doc" />
    </div>
  )
}
