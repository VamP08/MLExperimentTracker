import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import fixture from './fixture.json'

// Real responses captured from `mlexp ui` over the `mlexp demo` data, keyed by path without
// the leading slash. Anything not captured gets the server's 404 shape.
const routes = fixture as Record<string, unknown>

globalThis.fetch = async (input: RequestInfo | URL) => {
  const key = new URL(String(input), 'http://localhost').pathname.slice(1)
  const found = key in routes
  return new Response(JSON.stringify(found ? routes[key] : { message: 'Not Found' }), {
    status: found ? 200 : 404,
    headers: { 'Content-Type': 'application/json' },
  })
}

// jsdom has no layout, so these are missing.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Element.prototype.scrollTo = () => {}
Element.prototype.scrollIntoView = () => {}

afterEach(() => cleanup())
