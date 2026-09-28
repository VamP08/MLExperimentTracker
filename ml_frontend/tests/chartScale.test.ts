import { describe, expect, it } from 'vitest'
import { niceAxis, tickLabel, ticks } from '../src/lib/chartScale'
import { camelCase, groupFlatMetrics, headlineMetric } from '../src/lib/metrics'

describe('chart scale', () => {
  it('snaps the axis outward to round ticks', () => {
    const axis = niceAxis(0.781, 0.851)
    expect(axis.lo).toBeLessThanOrEqual(0.781)
    expect(axis.hi).toBeGreaterThanOrEqual(0.851)
    expect(axis.values[0]).toBe(axis.lo)
    expect(axis.values.at(-1)).toBe(axis.hi)
  })

  it('never gives a non-negative series a negative axis', () => {
    expect(niceAxis(0.01, 5).lo).toBe(0)
  })

  it('handles a flat series', () => {
    const axis = niceAxis(3, 3)
    expect(axis.hi).toBeGreaterThan(axis.lo)
    expect(ticks(2, 2).values).toEqual([2])
  })

  it('labels ticks with only the decimals the spacing needs', () => {
    expect(tickLabel(0.7, 0.1)).toBe('0.7')
    expect(tickLabel(0.75, 0.25)).toBe('0.75')
    expect(tickLabel(2000, 500)).toBe('2000')
  })
})

describe('metrics helpers', () => {
  it('matches the server key transform', () => {
    expect(camelCase('val_loss')).toBe('valLoss')
    expect(camelCase('learning-rate')).toBe('learningRate')
  })

  it('picks validation accuracy first, and knows losses go down', () => {
    expect(headlineMetric(['loss', 'accuracy', 'val_accuracy'])).toEqual({ key: 'val_accuracy', better: 'max' })
    expect(headlineMetric(['loss'])).toEqual({ key: 'loss', better: 'min' })
    expect(headlineMetric([])).toBeNull()
  })

  it('regroups flat stat siblings and drops nulls', () => {
    const groups = groupFlatMetrics({ loss: 0.2, lossMin: 0.1, lossMax: 0.9, accuracy: null })
    expect(groups).toEqual([{ name: 'loss', latest: 0.2, min: 0.1, max: 0.9, mean: undefined, stddev: undefined }])
  })
})
