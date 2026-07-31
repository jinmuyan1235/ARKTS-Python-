export interface RegionBox {
  x1: number
  y1: number
  x2: number
  y2: number
}

export function normalizeRegionBox(values: number[], pageWidth: number, pageHeight: number): RegionBox {
  const width: number = Math.max(1, pageWidth)
  const height: number = Math.max(1, pageHeight)
  const source: number[] = values.length === 4 ? values : [0, 0, width, height]
  const x1: number = Math.max(0, Math.min(width - 1, source[0]))
  const y1: number = Math.max(0, Math.min(height - 1, source[1]))
  const x2: number = Math.max(x1 + 1, Math.min(width, source[2]))
  const y2: number = Math.max(y1 + 1, Math.min(height, source[3]))
  return { x1: x1, y1: y1, x2: x2, y2: y2 }
}

export function moveRegionBox(
  box: RegionBox,
  offsetX: number,
  offsetY: number,
  previewWidth: number,
  previewHeight: number,
  pageWidth: number,
  pageHeight: number
): RegionBox {
  const width: number = box.x2 - box.x1
  const height: number = box.y2 - box.y1
  const pageOffsetX: number = previewWidth <= 0 ? 0 : offsetX * pageWidth / previewWidth
  const pageOffsetY: number = previewHeight <= 0 ? 0 : offsetY * pageHeight / previewHeight
  const x1: number = Math.max(0, Math.min(pageWidth - width, box.x1 + pageOffsetX))
  const y1: number = Math.max(0, Math.min(pageHeight - height, box.y1 + pageOffsetY))
  return { x1: x1, y1: y1, x2: x1 + width, y2: y1 + height }
}

export function resizeRegionBox(
  box: RegionBox,
  offsetX: number,
  offsetY: number,
  previewWidth: number,
  previewHeight: number,
  pageWidth: number,
  pageHeight: number,
  minimumSize: number = 8
): RegionBox {
  const pageOffsetX: number = previewWidth <= 0 ? 0 : offsetX * pageWidth / previewWidth
  const pageOffsetY: number = previewHeight <= 0 ? 0 : offsetY * pageHeight / previewHeight
  return {
    x1: box.x1,
    y1: box.y1,
    x2: Math.max(box.x1 + minimumSize, Math.min(pageWidth, box.x2 + pageOffsetX)),
    y2: Math.max(box.y1 + minimumSize, Math.min(pageHeight, box.y2 + pageOffsetY))
  }
}
