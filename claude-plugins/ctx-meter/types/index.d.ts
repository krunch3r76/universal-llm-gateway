export type Reading = { percent: number; tokens: number; window: number }

declare module 'claude-code' {
  interface PluginState {
    'ctx-meter': { last: Reading | null }
  }
}
