export function bottomNavigationPercent(tabCount: number): number {
  if (tabCount <= 0) return 100
  return Math.floor(100 / tabCount)
}

export function batchGridColumns(viewportWidth: number): number {
  return viewportWidth <= 320 ? 2 : 3
}

export function editingTouchTarget(): number {
  return 40
}
