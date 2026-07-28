/** Stable backend-shaped fixtures used by frontend state and parser tests. */
export const AUTH_SUCCESS_JSON: string = `{
  "token":"session-token","expiresInDays":7,
  "user":{"userId":"user-1","username":"chemist","displayName":"杨佩骏","role":"复核员","avatarUrl":null,"createdAt":"2026-07-28T00:00:00Z"}
}`

export const AUTH_EXPIRED_JSON: string = `{"detail":"登录会话已失效，请重新登录。"}`

export const JOB_QUEUED_JSON: string = `{
  "jobId":"job-1","status":"queued","message":"任务已进入队列。","inputKind":"sample","analysisId":"analysis-1",
  "createdAt":"2026-07-28T00:00:00Z","updatedAt":"2026-07-28T00:00:00Z"
}`

export const JOB_RUNNING_JSON: string = `{
  "jobId":"job-1","status":"running","message":"模型正在识别。","inputKind":"sample","analysisId":"analysis-1",
  "createdAt":"2026-07-28T00:00:00Z","updatedAt":"2026-07-28T00:00:02Z"
}`

export const JOB_COMPLETED_JSON: string = `{
  "jobId":"job-1","status":"completed","message":"识别完成。","inputKind":"sample","analysisId":"analysis-1",
  "createdAt":"2026-07-28T00:00:00Z","updatedAt":"2026-07-28T00:00:04Z",
  "result":{"analysisId":"analysis-1","status":"success","smiles":"CCO"}
}`

export const HISTORY_PAGE_ONE_JSON: string = `{
  "items":[{
    "analysisId":"analysis-1","createdAt":"2026-07-28T00:00:00Z","inputType":"image","filename":"ethanol.png",
    "backend":"decimer","decision":"accepted","status":"success","smiles":"CCO","isFavorite":false,
    "confirmed":false,"needsReview":true,"artifactStatus":"available",
    "createdBy":{"userId":"user-1","username":"chemist","displayName":"杨佩骏","role":"复核员","avatarUrl":null,"createdAt":"2026-07-28T00:00:00Z"}
  }],"offset":0,"limit":20,"hasMore":true,"nextOffset":1
}`

export const HISTORY_PAGE_TWO_JSON: string = `{
  "items":[{
    "analysisId":"analysis-2","createdAt":"2026-07-28T00:01:00Z","inputType":"smiles","filename":"",
    "backend":"manual","decision":"accepted","status":"success","smiles":"CC(=O)O","isFavorite":true,
    "confirmed":true,"needsReview":false,"artifactStatus":"available","createdBy":null
  }],"offset":1,"limit":20,"hasMore":false,"nextOffset":null
}`

export const REVIEW_CONFIRMED_JSON: string = `{
  "analysisId":"analysis-1","status":"success","inputType":"image","smiles":"CCO","confirmed":true,
  "needsReview":false,"reviewStatus":"confirmed","isFavorite":false,
  "review":{"required":true,"status":"confirmed","confirmed":true,"reviewedAt":"2026-07-28T00:03:00Z",
    "lastConfirmedAt":"2026-07-28T00:03:00Z","reason":null,"note":null,
    "reviewedBy":{"userId":"user-1","displayName":"杨佩骏","role":"复核员"},
    "events":[{"action":"confirm_structure","smiles":"CCO","createdAt":"2026-07-28T00:03:00Z","reason":null,"note":null,
      "actor":{"userId":"user-1","displayName":"杨佩骏","role":"复核员"}}]}
}`

export const REVIEW_UNABLE_JSON: string = `{
  "analysisId":"analysis-1","status":"success","inputType":"image","smiles":"CCO","confirmed":false,
  "needsReview":true,"reviewStatus":"unable_to_confirm","isFavorite":false,
  "review":{"required":true,"status":"unable_to_confirm","confirmed":false,"reviewedAt":"2026-07-28T00:04:00Z",
    "lastConfirmedAt":null,"reason":"image_unclear","note":"原图局部模糊","reviewedBy":null,"events":[]}
}`

export const MALFORMED_JSON: string = `{"jobId":"job-1",`
