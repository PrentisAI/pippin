/** Speaking in a brainstorm instead of typing: a recording becomes words (whisper, on the console's machine),
 *  and an agent's answer can be read aloud (the cloud voice when the console has one, else the browser's). */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'

export type Hearing = 'idle' | 'listening' | 'transcribing'

// hands-free: what counts as speaking, and how long a pause ends what was said
const LOUD = 0.02
const PAUSE_MS = 1400
const MAX_MS = 120_000
// while speaking, what was said so far is recognised again this often (one request at a time), in slices this
// long; per the console's setting (voice.live): a small model can go often, the main one less
const LIVE: Record<'fast' | 'accurate', { every: number; slice: number }> = {
  fast: { every: 250, slice: 250 },
  accurate: { every: 900, slice: 500 },
}

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] ?? '')
    r.onerror = () => reject(r.error)
    r.readAsDataURL(blob)
  })
}

/** Record from the microphone and get the words back. With `auto`, the recording ends on its own after a pause
 *  (hands-free); otherwise it ends when `stop` is called. While it records, `onPartial` gets the words so far
 *  (recognised again as more is said); `onWords` gets the final words once it has ended. */
export function useVoice(onWords: (text: string, auto: boolean) => void, onPartial?: (text: string) => void) {
  const [state, setState] = useState<Hearing>('idle')
  const [error, setError] = useState<string | null>(null)
  const [canHear, setCanHear] = useState<boolean | null>(null)
  const [cloudVoice, setCloudVoice] = useState(false)
  const [live, setLive] = useState<'fast' | 'accurate' | 'off'>('fast')
  const rec = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const ctx = useRef<AudioContext | null>(null)
  const timer = useRef<number | null>(null)
  const discard = useRef(false)
  const words = useRef(onWords)
  const partial = useRef(onPartial)
  useEffect(() => { words.current = onWords; partial.current = onPartial })

  useEffect(() => {
    api.memory.voiceStatus().then((s) => { setCanHear(s.hear); setCloudVoice(s.cloud_voice); setLive(s.live) }).catch(() => setCanHear(false))
  }, [])

  const release = () => {
    if (timer.current) window.clearInterval(timer.current)
    timer.current = null
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
    ctx.current?.close().catch(() => {})
    ctx.current = null
  }
  useEffect(() => () => {
    discard.current = true
    if (rec.current?.state === 'recording') rec.current.stop()
    release()
  }, [])

  const stop = useCallback(() => { if (rec.current?.state === 'recording') rec.current.stop() }, [])
  const cancel = useCallback(() => { discard.current = true; stop() }, [stop])

  const start = useCallback(async (auto = false) => {
    if (rec.current?.state === 'recording') return
    setError(null)
    if (canHear === false) {
      setError('Speech recognition is not set up on this console yet: add a model key, or install whisper.cpp and set AURORA_WHISPER (see Help → Voice).')
      return
    }
    let media: MediaStream
    try {
      // the browser takes out what its own speakers play (an answer read aloud), and room noise
      media = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
    } catch {
      setError('The microphone is not available here: allow it for this page in the browser.')
      return
    }
    stream.current = media
    const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((t) => MediaRecorder.isTypeSupported(t)) ?? ''
    const r = new MediaRecorder(media, type ? { mimeType: type } : undefined)
    const chunks: Blob[] = []
    let heard = !auto
    let ended = false
    const kind = r.mimeType.includes('mp4') ? 'mp4' : 'webm'
    r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data) }
    // the words so far: everything recorded until now, recognised again (the next request when this one is back)
    let asking = false
    let askedAt = 0
    let askedChunks = 0
    const soFar = async () => {
      asking = true
      askedAt = Date.now()
      askedChunks = chunks.length
      try {
        const { text } = await api.memory.voiceTranscribe(await toBase64(new Blob(chunks, { type: r.mimeType })), kind, true)
        if (!ended && text.trim()) partial.current?.(text.trim())
      } catch { /* the final words still come */ }
      asking = false
    }
    r.onstop = async () => {
      ended = true
      release()
      const blob = new Blob(chunks, { type: r.mimeType })
      if (discard.current || !heard || blob.size < 2000) { discard.current = false; setState('idle'); return }
      setState('transcribing')
      try {
        const audio = await toBase64(blob)
        const { text } = await api.memory.voiceTranscribe(audio, kind)
        setState('idle')
        if (text.trim()) words.current(text.trim(), auto)
      } catch (e) {
        setState('idle')
        setError((e as Error).message)
      }
    }
    rec.current = r
    discard.current = false
    const pace = live === 'off' ? null : LIVE[live]
    r.start(pace?.slice ?? 1000)   // in slices, so what was said so far can be recognised while speaking
    setState('listening')
    // how loud it is now: speech, then a pause, ends what was said (and a recording is never endless)
    const began = Date.now()
    const audioCtx = new AudioContext()
    ctx.current = audioCtx
    const analyser = audioCtx.createAnalyser()
    analyser.fftSize = 1024
    audioCtx.createMediaStreamSource(media).connect(analyser)
    const buf = new Float32Array(analyser.fftSize)
    let quietSince = Date.now()
    timer.current = window.setInterval(() => {
      analyser.getFloatTimeDomainData(buf)
      const rms = Math.sqrt(buf.reduce((s, v) => s + v * v, 0) / buf.length)
      if (rms > LOUD) { heard = true; quietSince = Date.now() }
      if ((auto && heard && Date.now() - quietSince > PAUSE_MS) || Date.now() - began > MAX_MS) { stop(); return }
      if (pace && partial.current && heard && !asking && chunks.length > askedChunks && Date.now() - askedAt > pace.every) soFar()
    }, 100)
  }, [canHear, live, stop])

  return { state, error, setError, canHear, cloudVoice, start, stop, cancel }
}

function bigrams(text: string): string[] {
  const t = text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '')
  return Array.from({ length: Math.max(0, t.length - 1) }, (_, i) => t.slice(i, i + 2))
}

/** Whether words heard are (mostly) the answer just read aloud coming back through the microphone. */
export function isEcho(heard: string, read: string): boolean {
  const h = bigrams(heard)
  if (h.length < 3 || !read) return false
  const r = new Set(bigrams(read))
  return h.filter((b) => r.has(b)).length / h.length >= 0.6
}

/** What an answer sounds like: without citation keys, markup and links. */
export function spoken(text: string): string {
  return text
    .replace(/\[[^\]]{1,40}\]/g, '')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/[*_`#>]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

let playing: HTMLAudioElement | null = null
let generation = 0           // a new reading, or stopping, ends the one before

/** A text in pieces of a sentence or two, so the first can be heard while the next is made. */
function pieces(text: string, most = 160): string[] {
  const sentences = text.match(/[^。！？!?.\n]+[。！？!?.]*\s*|\n+/g) ?? [text]
  const out: string[] = []
  for (const sen of sentences.map((x) => x.trim()).filter(Boolean)) {
    if (out.length && out[out.length - 1].length + sen.length < most) out[out.length - 1] += ` ${sen}`
    else out.push(sen)
  }
  return out
}

function play(blob: Blob, gen: number): Promise<void> {
  return new Promise((resolve) => {
    if (gen !== generation) { resolve(); return }
    const url = URL.createObjectURL(blob)
    playing = new Audio(url)
    const done = () => { URL.revokeObjectURL(url); resolve() }
    playing.onended = done
    playing.onpause = done
    playing.onerror = done
    playing.play().catch(done)
  })
}

async function speakBrowser(said: string, gen: number): Promise<void> {
  if (!('speechSynthesis' in window) || gen !== generation) return
  await new Promise<void>((resolve) => {
    const u = new SpeechSynthesisUtterance(said)
    const cjk = (said.match(/[\u4e00-\u9fff]/g) ?? []).length
    u.lang = cjk > said.length / 5 ? 'zh-CN' : 'en-US'
    u.onend = () => resolve()
    u.onerror = () => resolve()
    window.speechSynthesis.speak(u)
  })
}

/** Read a text aloud; `onEnd` runs when it is done (or could not be read), not when it was stopped. With `cloud`,
 *  the console's voice (a model that talks), a sentence or two at a time: the next is made while one is heard. */
export async function speak(text: string, cloud: boolean, onEnd?: () => void): Promise<void> {
  stopSpeaking()
  const gen = ++generation
  const said = spoken(text)
  if (!said) { onEnd?.(); return }
  if (cloud) {
    const parts = pieces(said)
    const make = (t: string) => fetch('/api/memory/voice/speak', { method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }) })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
    let i = 0
    try {
      let next = make(parts[0])
      for (; i < parts.length; i++) {
        const blob = await next
        if (gen !== generation) return
        if (i + 1 < parts.length) { next = make(parts[i + 1]); next.catch(() => {}) }
        await play(blob, gen)
        if (gen !== generation) return
      }
      onEnd?.()
      return
    } catch {
      if (gen !== generation) return
      // the rest in the browser's voice
      await speakBrowser(parts.slice(i).join(' '), gen)
      if (gen === generation) onEnd?.()
      return
    }
  }
  await speakBrowser(said, gen)
  if (gen === generation) onEnd?.()
}

export function stopSpeaking(): void {
  generation++
  if (playing) { const p = playing; playing = null; p.pause() }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}
