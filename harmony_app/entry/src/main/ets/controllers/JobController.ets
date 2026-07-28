import { VisionJob, VisionSample } from '../model/VisionResult'
import { isTerminalJobStatus, userFacingError } from '../services/AppUtils'

const NORMAL_POLL_INTERVAL_MS: number = 2000
const MAX_POLL_BACKOFF_MS: number = 30000
const MAX_LOCAL_TASKS: number = 20

export interface LocalTaskState {
  jobId: string
  status: string
  message: string
  inputKind: string
  analysisId: string
  createdAt: string
  updatedAt: string
  sourceKey: string
  failureCount: number
  nextPollAt: number
  polling: boolean
  networkWaiting: boolean
}

export function pollBackoffMs(failureCount: number): number {
  if (failureCount <= 0) {
    return NORMAL_POLL_INTERVAL_MS
  }
  return Math.min(MAX_POLL_BACKOFF_MS,
    NORMAL_POLL_INTERVAL_MS * Math.pow(2, Math.max(0, failureCount - 1)))
}

/** Recognition, polling, manual-SMILES and completion-banner state. */
@Observed
export class JobController {
  imageUri: string = ''
  samples: VisionSample[] = []
  samplesLoading: boolean = false
  samplesError: string = ''
  tasks: LocalTaskState[] = []
  submitting: boolean = false
  submittingSourceKey: string = ''
  smilesInput: string = ''
  smilesAnalyzing: boolean = false
  completionAnalysisId: string = ''
  completionMessage: string = ''

  /** Restored IDs start in a local-only state and are refreshed from /jobs. */
  restore(jobIds: string[]): void {
    const now: number = Date.now()
    const uniqueIds: string[] = []
    jobIds.forEach((jobId: string) => {
      if (jobId.length > 0 && uniqueIds.indexOf(jobId) < 0) {
        uniqueIds.push(jobId)
      }
    })
    this.tasks = uniqueIds.slice(0, MAX_LOCAL_TASKS).map((jobId: string) => {
      const task: LocalTaskState = {
        jobId: jobId,
        status: 'restoring',
        message: '正在恢复任务状态…',
        inputKind: '',
        analysisId: '',
        createdAt: '',
        updatedAt: '',
        sourceKey: '',
        failureCount: 0,
        nextPollAt: now,
        polling: false,
        networkWaiting: false
      }
      return task
    })
  }

  activate(job: VisionJob, sourceKey: string): void {
    const task: LocalTaskState = this.fromServerJob(job, sourceKey, 0, false)
    this.tasks = [task].concat(this.tasks.filter((item: LocalTaskState) => item.jobId !== job.jobId))
      .slice(0, MAX_LOCAL_TASKS)
  }

  beginSubmission(sourceKey: string): boolean {
    if (this.submitting || this.hasPendingSource(sourceKey)) {
      return false
    }
    this.submitting = true
    this.submittingSourceKey = sourceKey
    return true
  }

  finishSubmission(): void {
    this.submitting = false
    this.submittingSourceKey = ''
  }

  hasPendingSource(sourceKey: string): boolean {
    const separator: number = sourceKey.indexOf(':')
    const sourceKind: string = separator < 0 ? sourceKey : sourceKey.substring(0, separator)
    const restoredWildcard: string = `${sourceKind}:*`
    return this.tasks.some((item: LocalTaskState) =>
      (item.sourceKey === sourceKey || item.sourceKey === restoredWildcard) && !isTerminalJobStatus(item.status))
  }

  beginPolling(jobId: string, nowMs: number, force: boolean = false): boolean {
    const index: number = this.indexOf(jobId)
    if (index < 0) {
      return false
    }
    const task: LocalTaskState = this.tasks[index]
    if (task.polling || isTerminalJobStatus(task.status) || (!force && task.nextPollAt > nowMs)) {
      return false
    }
    this.replace(index, this.copyTask(task, true, task.networkWaiting, task.failureCount, task.nextPollAt, task.message))
    return true
  }

  applyServerJob(job: VisionJob): void {
    const index: number = this.indexOf(job.jobId)
    const sourceKey: string = index < 0 ? '' : this.tasks[index].sourceKey
    const task: LocalTaskState = this.fromServerJob(job, sourceKey, 0, false)
    if (index < 0) {
      this.tasks = [task].concat(this.tasks).slice(0, MAX_LOCAL_TASKS)
    } else {
      this.replace(index, task)
    }
  }

  markNetworkFailure(jobId: string, message: string, nowMs: number): number {
    const index: number = this.indexOf(jobId)
    if (index < 0) {
      return MAX_POLL_BACKOFF_MS
    }
    const task: LocalTaskState = this.tasks[index]
    const failureCount: number = task.failureCount + 1
    const delay: number = pollBackoffMs(failureCount)
    this.replace(index, this.copyTask(task, false, true, failureCount, nowMs + delay,
      `${message}，${Math.ceil(delay / 1000)} 秒后自动重试。`))
    return delay
  }

  markUnavailable(jobId: string, message: string): void {
    const index: number = this.indexOf(jobId)
    if (index < 0) {
      return
    }
    const task: LocalTaskState = this.tasks[index]
    const unavailable: LocalTaskState = {
      jobId: task.jobId,
      status: 'failed',
      message: message,
      inputKind: task.inputKind,
      analysisId: task.analysisId,
      createdAt: task.createdAt,
      updatedAt: task.updatedAt,
      sourceKey: task.sourceKey,
      failureCount: task.failureCount,
      nextPollAt: 0,
      polling: false,
      networkWaiting: false
    }
    this.replace(index, unavailable)
  }

  finishPolling(jobId: string): void {
    const index: number = this.indexOf(jobId)
    if (index < 0) {
      return
    }
    const task: LocalTaskState = this.tasks[index]
    this.replace(index, this.copyTask(task, false, task.networkWaiting, task.failureCount,
      task.nextPollAt, task.message))
  }

  dueJobIds(nowMs: number): string[] {
    return this.tasks.filter((item: LocalTaskState) =>
      !item.polling && !isTerminalJobStatus(item.status) && item.nextPollAt <= nowMs)
      .map((item: LocalTaskState) => item.jobId)
  }

  nextPollDelay(nowMs: number): number {
    const pending: LocalTaskState[] = this.tasks.filter((item: LocalTaskState) =>
      !isTerminalJobStatus(item.status))
    if (pending.length === 0) {
      return -1
    }
    let earliest: number = nowMs + MAX_POLL_BACKOFF_MS
    pending.forEach((item: LocalTaskState) => {
      if (!item.polling) {
        earliest = Math.min(earliest, item.nextPollAt)
      }
    })
    return Math.max(250, Math.min(MAX_POLL_BACKOFF_MS, earliest - nowMs))
  }

  jobIds(): string[] {
    return this.tasks.map((item: LocalTaskState) => item.jobId).slice(0, MAX_LOCAL_TASKS)
  }

  /** Only unfinished jobs belong in Preferences and need restoring after restart. */
  pendingJobIds(): string[] {
    return this.tasks.filter((item: LocalTaskState) => !isTerminalJobStatus(item.status))
      .map((item: LocalTaskState) => item.jobId)
      .slice(0, MAX_LOCAL_TASKS)
  }

  pendingCount(): number {
    return this.tasks.filter((item: LocalTaskState) => !isTerminalJobStatus(item.status)).length
  }

  private fromServerJob(job: VisionJob, sourceKey: string, failureCount: number,
    networkWaiting: boolean): LocalTaskState {
    // The jobs API exposes input kind but not the original sample ID or URI.
    // After app restart, use a conservative wildcard to prevent accidental
    // duplicate submissions until that restored task becomes terminal.
    const resolvedSourceKey: string = sourceKey.length > 0 ? sourceKey :
      (job.inputKind.length > 0 ? `${job.inputKind}:*` : '')
    return {
      jobId: job.jobId,
      status: job.status,
      message: userFacingError(job.message, job.status === 'failed' ?
        '识别任务失败，请重新提交。' : '任务状态已更新。'),
      inputKind: job.inputKind,
      analysisId: job.analysisId.length > 0 ? job.analysisId :
        (job.result === undefined ? '' : job.result.analysisId),
      createdAt: job.createdAt,
      updatedAt: job.updatedAt,
      sourceKey: resolvedSourceKey,
      failureCount: failureCount,
      nextPollAt: isTerminalJobStatus(job.status) ? 0 : Date.now() + NORMAL_POLL_INTERVAL_MS,
      polling: false,
      networkWaiting: networkWaiting
    }
  }

  private copyTask(task: LocalTaskState, polling: boolean, networkWaiting: boolean,
    failureCount: number, nextPollAt: number, message: string): LocalTaskState {
    return {
      jobId: task.jobId,
      status: task.status,
      message: message,
      inputKind: task.inputKind,
      analysisId: task.analysisId,
      createdAt: task.createdAt,
      updatedAt: task.updatedAt,
      sourceKey: task.sourceKey,
      failureCount: failureCount,
      nextPollAt: nextPollAt,
      polling: polling,
      networkWaiting: networkWaiting
    }
  }

  private indexOf(jobId: string): number {
    return this.tasks.findIndex((item: LocalTaskState) => item.jobId === jobId)
  }

  private replace(index: number, task: LocalTaskState): void {
    this.tasks = this.tasks.map((item: LocalTaskState, itemIndex: number) => itemIndex === index ? task : item)
  }
}
