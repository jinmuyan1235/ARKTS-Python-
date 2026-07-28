import { UserProfile } from './VisionResult'

export interface DocumentPageInfo {
  pageNumber: number
  width: number
  height: number
  previewUrl: string
}

export interface DocumentRegionInfo {
  regionId: string
  pageNumber: number
  bbox: number[]
  regionType: string
  confidence: number | null
  confirmed: boolean
  status: string
  message: string
  recognitionJobId: string
  analysisId: string
}

export interface DocumentResult {
  documentId: string
  filename: string
  contentType: string
  status: string
  message: string
  detectionJobId: string
  createdAt: string
  updatedAt: string
  createdBy: UserProfile | null
  pageCount: number
  regionCount: number
  pages: DocumentPageInfo[]
  regions: DocumentRegionInfo[]
}

export interface DocumentJobResult {
  documentId: string
  pageCount?: number
  regionCount?: number
  regionId?: string
  analysisId?: string
}

export interface DocumentJob {
  jobId: string
  status: string
  message: string
  inputKind: string
  analysisId: string
  documentId?: string
  regionId?: string
  createdAt: string
  updatedAt: string
  result?: DocumentJobResult
}

export interface DocumentRegionEdit {
  bbox: number[]
  regionType: string
  confirmed: boolean
  note?: string
}
