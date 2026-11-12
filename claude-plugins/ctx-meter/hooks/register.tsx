import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Reading } from '../types'

// Hop line from runbook:houseless-coordination §4 (contextUsagePercent >= 69).
const HOP_PERCENT = 69

const k = (n: number) => `${Math.round(n / 1000)}k`

const last = atom({ plugin: 'ctx-meter', key: 'last' } as const, null)

async function reading($: EngineInterface): Promise<Reading | null> {
  const { context } = await $.session.usage()
  if (context.percent === undefined || context.tokens === undefined) return null
  return { percent: context.percent, tokens: context.tokens, window: context.window }
}

let warned = false

async function refresh($: EngineInterface): Promise<void> {
  const r = await reading($)
  await update($, last, () => r)
  if (r === null) {
    $.ui.status('ctx —')
    return
  }
  const flag = r.percent >= HOP_PERCENT ? ' HOP' : ''
  $.ui.status(`ctx ${r.percent}% (${k(r.tokens)}/${k(r.window)})${flag}`)
  if (r.percent >= HOP_PERCENT && !warned) {
    warned = true
    $.ui.toast(`Context at ${r.percent}%: past the ${HOP_PERCENT}% hop line`)
  }
  if (r.percent < HOP_PERCENT) warned = false
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const out = await next(e)
    await refresh($)
    return out
  })

  on('turn.complete', async ($, e, next) => {
    const out = await next(e)
    await refresh($)
    return out
  })

  on('tool.call', async ($, e, next) => {
    const out = await next(e)
    await refresh($)
    return out
  }).catch(($, e, next) => next(e)) // fail open: a meter error never blocks a tool

  // Give the seat its own figure, beside the prompt (not in the system prompt,
  // so the cached prefix is untouched). Slash commands pass through untouched.
  on('prompt.submit', async ($, e, next) => {
    if (e.command) return next(e)
    const r = await reading($)
    const footer =
      r === null
        ? 'ctx — (no reading yet)'
        : `ctx ${r.percent}% · ${k(r.tokens)} of ${k(r.window)} · hop at ${HOP_PERCENT}%${r.percent >= HOP_PERCENT ? ' — HOP NOW' : ''}`
    const figure =
      r === null
        ? 'unknown (no response yet in this window)'
        : `${r.percent}% (${r.tokens} of ${r.window} tokens, as of the last response); hop line ${HOP_PERCENT}%`
    // The person cannot see mod UI on claude.ai web, so the reply carries it.
    const line = `[ctx-meter] context used: ${figure}. End your reply with this exact line, alone, as its last line: ${footer}`
    return next({ ...e, context: [...(e.context ?? []), line] })
  }).catch(($, e, next) => next(e)) // fail open: the prompt goes in unchanged

  // Band above the prompt, for the person; redraws when `last` changes.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const r = await read($, last)
    if (e.props.hasSurvey || r === null) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const isOver = r.percent >= HOP_PERCENT
    return (
      <Box>
        <Text color={isOver ? 'red' : undefined} dimColor={!isOver} bold={isOver}>
          ctx {r.percent}% · {k(r.tokens)} of {k(r.window)} · hop at {HOP_PERCENT}%{isOver ? ' — HOP NOW' : ''}
        </Text>
      </Box>
    )
  })
}
