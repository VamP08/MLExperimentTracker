import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import App from '../src/App'

function open(path: string) {
  window.history.pushState({}, '', path)
  render(<App />)
}

describe('pages render against real API responses', () => {
  it('dashboard shows the archive charts and every experiment', async () => {
    open('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeTruthy()
    expect(await screen.findByText('Runs over time')).toBeTruthy()
    for (const name of ['agnews-distilbert', 'cifar10-cnn', 'quickstart']) {
      expect((await screen.findAllByText(name)).length).toBeGreaterThan(0)
    }
  })

  it('experiment overview ranks the runs on the headline metric', async () => {
    open('/experiment/cifar10-cnn')
    expect(await screen.findByRole('heading', { level: 1, name: 'cifar10-cnn' })).toBeTruthy()
    const board = await screen.findByRole('region', { name: /Leaderboard/ })
    const rows = await within(board).findAllByRole('listitem')
    expect(rows).toHaveLength(7)
    expect(rows[0].textContent).toContain('resnet18-bs64-lr0.004')
    expect(screen.getByText(/final val_accuracy, highest first/)).toBeTruthy()
  })

  it('run page opens on a run from the store', async () => {
    open('/runs/cifar10-cnn_20260801T214403Z_5b6f')
    expect((await screen.findAllByText('resnet18-bs64-lr0.004')).length).toBeGreaterThan(0)
    expect(await screen.findByRole('tab', { name: /Overview/ })).toBeTruthy()
  })

  it('unknown paths get the not-found page', async () => {
    open('/nowhere')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeTruthy()
  })
})
