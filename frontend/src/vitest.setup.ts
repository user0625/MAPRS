import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(cleanup)

Object.defineProperty(HTMLElement.prototype, 'scrollTo', {
  configurable: true,
  value: vi.fn(function scrollTo(this: HTMLElement, options?: ScrollToOptions) {
    if (typeof options?.top === 'number') this.scrollTop = options.top
  }),
})

globalThis.requestAnimationFrame = callback => window.setTimeout(() => callback(performance.now()), 0)
globalThis.cancelAnimationFrame = handle => window.clearTimeout(handle)
