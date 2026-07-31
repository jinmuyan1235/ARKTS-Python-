export interface CropBounds {
  x: number
  y: number
  width: number
  height: number
}

export interface NormalizedCropRect {
  left: number
  top: number
  right: number
  bottom: number
}

export interface ImageEditSpec {
  rotationDegrees: number
  flipHorizontal: boolean
  flipVertical: boolean
  crop: NormalizedCropRect
}

export type CropHandle = 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right'

const MIN_CROP_SPAN: number = 0.12

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value))
}

export function fullCropRect(): NormalizedCropRect {
  return { left: 0, top: 0, right: 1, bottom: 1 }
}

export function normalizeCropRect(rect: NormalizedCropRect): NormalizedCropRect {
  const left: number = clamp(rect.left, 0, 1 - MIN_CROP_SPAN)
  const top: number = clamp(rect.top, 0, 1 - MIN_CROP_SPAN)
  return {
    left: left,
    top: top,
    right: clamp(rect.right, left + MIN_CROP_SPAN, 1),
    bottom: clamp(rect.bottom, top + MIN_CROP_SPAN, 1)
  }
}

export function moveCropRect(
  rect: NormalizedCropRect,
  deltaX: number,
  deltaY: number
): NormalizedCropRect {
  const normalized: NormalizedCropRect = normalizeCropRect(rect)
  const width: number = normalized.right - normalized.left
  const height: number = normalized.bottom - normalized.top
  const left: number = clamp(normalized.left + deltaX, 0, 1 - width)
  const top: number = clamp(normalized.top + deltaY, 0, 1 - height)
  return { left: left, top: top, right: left + width, bottom: top + height }
}

export function resizeCropRect(
  rect: NormalizedCropRect,
  handle: CropHandle,
  deltaX: number,
  deltaY: number
): NormalizedCropRect {
  const normalized: NormalizedCropRect = normalizeCropRect(rect)
  let left: number = normalized.left
  let top: number = normalized.top
  let right: number = normalized.right
  let bottom: number = normalized.bottom
  if (handle === 'top_left' || handle === 'bottom_left') {
    left = clamp(left + deltaX, 0, right - MIN_CROP_SPAN)
  } else {
    right = clamp(right + deltaX, left + MIN_CROP_SPAN, 1)
  }
  if (handle === 'top_left' || handle === 'top_right') {
    top = clamp(top + deltaY, 0, bottom - MIN_CROP_SPAN)
  } else {
    bottom = clamp(bottom + deltaY, top + MIN_CROP_SPAN, 1)
  }
  return { left: left, top: top, right: right, bottom: bottom }
}

export function cropRectForAspect(
  imageWidth: number,
  imageHeight: number,
  aspectRatio: number
): NormalizedCropRect {
  if (aspectRatio <= 0) return fullCropRect()
  const safeWidth: number = Math.max(1, imageWidth)
  const safeHeight: number = Math.max(1, imageHeight)
  const imageAspect: number = safeWidth / safeHeight
  let width: number = 0.9
  let height: number = 0.9
  if (imageAspect > aspectRatio) {
    width = height * aspectRatio / imageAspect
  } else {
    height = width * imageAspect / aspectRatio
  }
  return {
    left: (1 - width) / 2,
    top: (1 - height) / 2,
    right: (1 + width) / 2,
    bottom: (1 + height) / 2
  }
}

export function cropBoundsForRect(
  width: number,
  height: number,
  rect: NormalizedCropRect
): CropBounds {
  const safeWidth: number = Math.max(1, Math.floor(width))
  const safeHeight: number = Math.max(1, Math.floor(height))
  const normalized: NormalizedCropRect = normalizeCropRect(rect)
  const x: number = Math.min(safeWidth - 1, Math.floor(safeWidth * normalized.left))
  const y: number = Math.min(safeHeight - 1, Math.floor(safeHeight * normalized.top))
  return {
    x: x,
    y: y,
    width: Math.max(1, Math.min(safeWidth - x,
      Math.round(safeWidth * (normalized.right - normalized.left)))),
    height: Math.max(1, Math.min(safeHeight - y,
      Math.round(safeHeight * (normalized.bottom - normalized.top))))
  }
}

export function normalizedRotationDegrees(rotationDegrees: number): number {
  const rounded: number = Math.round(rotationDegrees / 90) * 90
  return ((rounded % 360) + 360) % 360
}

export function isUnchangedImageEdit(spec: ImageEditSpec): boolean {
  const crop: NormalizedCropRect = normalizeCropRect(spec.crop)
  return normalizedRotationDegrees(spec.rotationDegrees) === 0 &&
    !spec.flipHorizontal && !spec.flipVertical &&
    crop.left === 0 && crop.top === 0 && crop.right === 1 && crop.bottom === 1
}
