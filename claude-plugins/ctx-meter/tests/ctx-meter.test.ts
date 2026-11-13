import { expect, test } from 'claude-code/testing'

const usage = (percent?: number, tokens?: number) => ({
  value: { startedAt: 0, context: { tokens, window: 1_000_000, percent }, rateLimits: [] },
})

test('status shows the figure; toast fires once past the hop line', async ($, on) => {
  const statuses: (string | undefined)[] = []
  const toasts: string[] = []
  let pct = 42
  on('session.usage', () => usage(pct, pct * 10_000))
  on('ui.status', (_, e) => { statuses.push(e.text); return { value: undefined } })
  on('ui.toast', (_, e) => { toasts.push(e.text); return { value: undefined } })
  on('tool.call', () => ({ result: {} }))

  await $.tool.call({ tool: 'Bash', command: 'ls' })
  expect(statuses.at(-1)).toBe('ctx 42% (420k/1000k)')
  expect(toasts.length).toBe(0)

  pct = 70
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  await $.tool.call({ tool: 'Bash', command: 'ls' })
  expect(statuses.at(-1)).toBe('ctx 70% (700k/1000k) HOP')
  expect(toasts.length).toBe(1)
})

test('prompt gets one context line', async ($, on) => {
  on('session.usage', () => usage(12, 120_000))
  let seen: readonly string[] | undefined
  on('prompt.submit', (_, e) => { seen = e.context; return e })

  await $.prompt.submit({ text: 'hi', wait: false, origin: { kind: 'sdk' } })
  expect(seen?.length).toBe(1)
  expect(seen?.[0]).toBe('[ctx-meter] context used: 12% (120000 of 1000000 tokens, as of the last response); hop line 69%. End your reply with this exact line, alone, as its last line: ctx 12% · 120k of 1000k · hop at 69%')
})

test('no reading yet: status dash, prompt says unknown', async ($, on) => {
  const statuses: (string | undefined)[] = []
  on('session.usage', () => usage(undefined, undefined))
  on('ui.status', (_, e) => { statuses.push(e.text); return { value: undefined } })
  on('tool.call', () => ({ result: {} }))
  let seen: readonly string[] | undefined
  on('prompt.submit', (_, e) => { seen = e.context; return e })

  await $.tool.call({ tool: 'Bash', command: 'ls' })
  await $.prompt.submit({ text: 'hi', wait: false, origin: { kind: 'sdk' } })
  expect(statuses.at(-1)).toBe('ctx —')
  expect(seen?.[0]).toBe('[ctx-meter] context used: unknown (no response yet in this window). End your reply with this exact line, alone, as its last line: ctx — (no reading yet)')
})

test('band draws the figure on every surface', async ($, on) => {
  on('session.usage', () => usage(16, 158_000))
  on('ui.status', () => ({ value: undefined }))
  on('tool.call', () => ({ result: {} }))
  await $.tool.call({ tool: 'Bash', command: 'ls' })

  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ plugin: 'ctx-meter', component: 'AbovePrompt', props: { hasSurvey: false }, surface } as never)
    expect((await ui.find({ type: 'Text', text: /ctx 16%/ }))?.text).toBe('ctx 16% · 158k of 1000k · hop at 69%')
    await ui.unmount()
  }
})
