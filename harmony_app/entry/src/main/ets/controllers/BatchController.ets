import { BatchJob } from '../model/BatchResult'

export const MAX_MOBILE_BATCH_IMAGES: number = 30

export function isTerminalBatchStatus(status: string): boolean {
  return status === 'completed' || status === 'failed' || status === 'cancelled'
}

@Observed
export class BatchController {
  selectedUris: string[] = []
  job: BatchJob | undefined = undefined
  activeJobId: string = ''
  uploading: boolean = false
  controlBusy: boolean = false
  polling: boolean = false
  errorMessage: string = ''

  restore(jobId: string): void {
    this.activeJobId = jobId.trim()
  }

  select(imageUris: string[]): void {
    this.selectedUris = imageUris.slice(0, MAX_MOBILE_BATCH_IMAGES)
  }

  clearSelection(): void {
    this.selectedUris = []
  }

  activate(job: BatchJob): void {
    this.job = job
    this.activeJobId = job.jobId
    this.errorMessage = ''
  }

  apply(job: BatchJob): void {
    this.job = job
    this.activeJobId = job.jobId
    this.errorMessage = ''
  }

  pendingJobId(): string {
    if (this.activeJobId.length === 0) {
      return ''
    }
    if (this.job !== undefined && isTerminalBatchStatus(this.job.status)) {
      return ''
    }
    return this.activeJobId
  }

  canPause(): boolean {
    return this.job !== undefined && (this.job.status === 'queued' || this.job.status === 'running')
  }

  canResume(): boolean {
    return this.job !== undefined && this.job.status === 'paused'
  }

  canCancel(): boolean {
    return this.job !== undefined && !isTerminalBatchStatus(this.job.status) && this.job.status !== 'cancelling'
  }

  canRetryFailed(): boolean {
    return this.job !== undefined && isTerminalBatchStatus(this.job.status) && this.job.categoryStats.failed > 0
  }
}
