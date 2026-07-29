export interface BatchCategoryStats {
  accepted: number
  acceptedWithWarning: number
  reviewNeeded: number
  rejected: number
  failed: number
  skipped: number
}

export interface BatchItemResult {
  analysisId: string
  filename: string
  status: string
  category: string
  message: string
  smiles: string | null
  canonicalSmiles: string | null
  confidence: number | null
  valid: boolean
  needsReview: boolean
  confirmed: boolean
  reviewStatus: string
}

export interface BatchJob {
  jobId: string
  status: string
  message: string
  backend: string
  source: string
  parentJobId: string | null
  total: number
  completed: number
  progress: number
  currentFile: string | null
  currentIndex: number | null
  categoryStats: BatchCategoryStats
  results: BatchItemResult[]
  createdAt: string
  startedAt: string | null
  finishedAt: string | null
  updatedAt: string
}
