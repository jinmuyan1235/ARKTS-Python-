import { ExportItem } from './ExportResult'

export interface TrainingEligibility {
  eligible: boolean
  label: string
  reasons: string[]
}
export interface FeedbackItem {
  analysisId: string
  savedAt: string
  reviewStatus: string
  predictedSmiles: string | null
  correctedSmiles: string | null
  sourceReference: string
  sourceLicense: string
  reviewer: string
  reviewedAt: string | null
  revision: number
  imageUrl: string | null
  filename: string
  trainingEligibility: TrainingEligibility
}

export interface FeedbackAuditRecord {
  operation: string
  createdAt: string
  reviewer: string
  oldStatus: string
  newStatus: string
  notes: string
}

export interface FeedbackDetail extends FeedbackItem {
  modelName: string | null
  modelVersion: string | null
  backend: string | null
  confidence: number | null
  correctionType: string
  feedbackAction: string
  duplicateImage: boolean
  duplicateOf: string
  notes: string
  privacyNotes: string
  history: FeedbackAuditRecord[]
}

export interface FeedbackPage {
  items: FeedbackItem[]
  offset: number
  limit: number
  total: number
  hasMore: boolean
  statusCounts: Record<string, number>
}

export interface FeedbackManifestResult {
  exportedCount: number
  skipped: Record<string, number>
  item: ExportItem
}
