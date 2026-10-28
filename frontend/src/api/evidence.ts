import type { TraceFact } from '../types/evidence'

export function basicHeader(username: string, password: string) {
  const bytes = new TextEncoder().encode(`${username}:${password}`)
  return `Basic ${btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(''))}`
}

export async function evidenceGet<T>(path: string, authorization: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    headers: { Authorization: authorization }, cache: 'no-store', signal: signal ?? AbortSignal.timeout(15000),
  })
  if (!response.ok) throw new Error(`读取失败（HTTP ${response.status}），请核对账号、运行归属和游标。`)
  const body = await response.json()
  if (!body || !('data' in body)) throw new Error('证据响应格式不正确。')
  return body.data as T
}

export interface SSEFrame { event: string; id: string; data: string }

// Frame boundaries may cross UTF-8 chunks. Only a completed blank-line frame is applied.
export class SSEParser {
  private buffer = ''
  private data: string[] = []
  private kind = 'message'
  private id = ''
  push(text: string): SSEFrame[] {
    this.buffer += text
    if (this.buffer.length > 262144) throw new Error('事件帧超过接收上限。')
    const frames: SSEFrame[] = []
    let end: number
    while ((end = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, end).replace(/\r$/, '')
      this.buffer = this.buffer.slice(end + 1)
      if (!line) {
        if (this.data.length) frames.push({ event: this.kind, id: this.id, data: this.data.join('\n') })
        this.data = []; this.kind = 'message'; this.id = ''
      } else if (!line.startsWith(':')) {
        const separator = line.indexOf(':')
        const field = separator < 0 ? line : line.slice(0, separator)
        const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
        if (field === 'data') this.data.push(value)
        if (field === 'event') this.kind = value
        if (field === 'id' && !value.includes('\0')) this.id = value
        if (this.data.join('\n').length > 262144) throw new Error('事件帧超过接收上限。')
      }
    }
    return frames
  }
}

export function checkedFact(value: unknown, run: string): TraceFact {
  const fact = value as TraceFact | undefined
  const e = fact?.event
  if (!fact || typeof fact.cursor !== 'string' || !e || e.run_id !== run ||
    typeof e.event_id !== 'string' || !Number.isSafeInteger(e.run_seq) || e.run_seq < 1 ||
    !['1.0', '1.1', '1.2', '1.3'].includes(e.schema_version) || typeof e.type !== 'string' ||
    fact.cursor !== `v1:${run}:${e.run_seq}:${e.event_id}`) throw new Error('事件身份或游标不一致。')
  return fact
}

export async function streamEvidence(run: string, cursor: string, authorization: string,
  controller: AbortController, apply: (frame: SSEFrame) => void) {
  let watchdog: ReturnType<typeof setTimeout> | undefined
  const resetWatchdog = () => {
    clearTimeout(watchdog)
    watchdog = setTimeout(() => controller.abort('heartbeat_timeout'), 35000)
  }
  resetWatchdog()
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    const response = await fetch(`/api/v1/runs/${run}/events/stream`, {
      headers: { Authorization: authorization, 'Last-Event-ID': cursor, Accept: 'text/event-stream' },
      signal: controller.signal, cache: 'no-store',
    })
    if (!response.ok) throw new Error(`实时连接失败（HTTP ${response.status}）。`)
    if (!response.headers.get('content-type')?.startsWith('text/event-stream') || !response.body)
      throw new Error('服务端未返回事件流。')
    reader = response.body.getReader()
    const parser = new SSEParser(), decoder = new TextDecoder()
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      resetWatchdog()
      for (const frame of parser.push(decoder.decode(chunk.value, { stream: true }))) apply(frame)
    }
    // A truncated last frame intentionally does not advance the cursor.
  } finally {
    clearTimeout(watchdog)
    await reader?.cancel().catch(() => {})
  }
}
