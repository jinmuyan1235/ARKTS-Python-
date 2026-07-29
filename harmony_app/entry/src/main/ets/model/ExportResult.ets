export interface ExportItem {
  format: string
  label: string
  available: boolean
  reason: string
  formalOnly: boolean
  fileName: string | null
  contentType: string | null
  sizeBytes: number
  downloadUrl: string | null
  expiresAt: string | null
}
export interface ExportManifest {
  scope: string
  resourceId: string
  expiresInSeconds: number
  items: ExportItem[]
}
