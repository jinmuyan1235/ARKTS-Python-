export interface AdmetItem {
  target: string | null
  taskType: string | null
  prediction: string | number | boolean | null
  probability: number | null
  insideDomain: boolean | null
  message: string | null
}

export interface AdmetSummary {
  status: string
  message: string
  items: AdmetItem[]
}

export interface ChemicalIdentityDetail {
  rawSmiles: string | null
  canonicalSmiles: string | null
  standardizedSmiles: string | null
  isomericSmiles: string | null
  inchi: string | null
  inchiKey: string | null
  formula: string | null
  formalCharge: number | null
  fragmentCount: number | null
  stereocenterCount: number | null
  heavyAtomCount: number | null
}

export interface ImageQualityDetail {
  score: number | null
  passed: boolean | null
  width: number | null
  height: number | null
  contrast: number | null
  blurVariance: number | null
  inkRatio: number | null
  borderInkRatio: number | null
  reasonCodes: string[]
}

export interface LipinskiCheck {
  name: string
  value: number | null
  limit: number | null
  passed: boolean | null
  message: string
}

export interface LipinskiDetail {
  passed: boolean | null
  summary: string
  checks: LipinskiCheck[]
}

export interface RecognitionTrace {
  modelName: string | null
  modelVersion: string | null
  resultOrigin: string | null
  selectedStrategy: string | null
  attemptCount: number | null
  strategyAgreement: boolean | null
  finalSource: string | null
  standardizationChanged: boolean | null
  standardizationWarnings: string[]
}

export interface ReviewActor {
  userId: string
  displayName: string
  role: string
}

export interface ReviewEvent {
  action: string
  smiles: string | null
  createdAt: string
  reason: string | null
  note: string | null
  actor: ReviewActor | null
}

export interface ReviewDetail {
  required: boolean
  status: string
  confirmed: boolean
  reviewedAt: string | null
  lastConfirmedAt: string | null
  reason: string | null
  note: string | null
  reviewedBy: ReviewActor | null
  events: ReviewEvent[]
}

export interface ReviewRequest {
  action: string
  smiles?: string
  reason?: string
  note?: string
}

export interface VisionAnalysisResult {
  analysisId: string
  createdAt: string
  inputType: string
  filename: string | null
  status: string
  message: string
  backend: string
  device: string | null
  smiles: string | null
  predictedSmiles: string | null
  canonicalSmiles: string | null
  confidence: number | null
  inferenceTimeMs: number | null
  imageQualityScore: number | null
  needsReview: boolean
  confirmed: boolean
  reviewStatus: string
  isFavorite: boolean
  decision: string | null
  riskLevel: string | null
  decisionMessage: string | null
  reasonCodes: string[]
  formula: string | null
  molecularWeight: number | null
  logP: number | null
  tpsa: number | null
  hbd: number | null
  hba: number | null
  rotatableBonds: number | null
  ringCount: number | null
  lipinskiPassed: boolean | null
  lipinskiViolations: string[]
  structureImageUrl: string | null
  sourceImageUrl: string | null
  warnings: string[]
  admet: AdmetSummary
  createdBy: UserProfile | null
  identity: ChemicalIdentityDetail
  imageQuality: ImageQualityDetail
  lipinskiDetail: LipinskiDetail
  recognitionTrace: RecognitionTrace
  review: ReviewDetail
}

export interface HealthResult {
  status: string
  appMode: string
  backend: string
  maxUploadBytes: number
  apiVersion: string
  modelRuntime: ModelRuntimeStatus
  taskQueue: TaskQueueStatus
}

export interface HealthCheckStatus {
  state: string
  label: string
  message: string
  sha256?: string | null
  durationMs?: number | null
}

export interface ModelRuntimeStatus {
  state: string
  label: string
  modelName: string
  modelVersion: string | null
  device: string | null
  message: string
  checkedAt: string | null
  cached: boolean
  weights: HealthCheckStatus
  warmup: HealthCheckStatus
  failureReasons: string[]
}

export interface TaskStatusCounts {
  queued: number
  running: number
  paused: number
  completed: number
  failed: number
  cancelled: number
}

export interface TaskQueueStatus {
  queued: number
  running: number
  paused: number
  active: number
  failed: number
  single: TaskStatusCounts
  batch: TaskStatusCounts
  recentFailureReasons: string[]
}

export interface VisionSample {
  id: string
  name: string
  imageUrl: string
}

export interface VisionJob {
  jobId: string
  status: string
  message: string
  inputKind: string
  analysisId: string
  createdAt: string
  updatedAt: string
  result?: VisionAnalysisResult
}

export interface HistoryItem {
  analysisId: string
  createdAt: string
  inputType: string
  filename: string
  backend: string
  decision: string
  status: string
  smiles: string
  isFavorite: boolean
  confirmed: boolean
  needsReview: boolean
  artifactStatus: string
  createdBy: UserProfile | null
}

export interface HistoryPage {
  items: HistoryItem[]
  offset: number
  limit: number
  hasMore: boolean
  nextOffset: number | null
}

export interface UserProfile {
  userId: string
  username: string
  displayName: string
  role: string
  accountType: string
  avatarUrl: string | null
  createdAt: string
}

export interface AuthResult {
  token: string
  expiresInDays: number
  user: UserProfile
}

export interface ChangePasswordResult extends AuthResult {
}
