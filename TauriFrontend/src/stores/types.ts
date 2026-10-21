export interface LoginForm {
  account: string;
  password: string;
  email: string;
  username: string;
  code: string;
  registerPassword: string;
  debugCode: string;
}

export interface UploadTask {
  id: string;
  projectId: number;
  filename: string;
  mediaId?: number;
  jobId?: string;
  status: "uploading" | "queued" | "running" | "succeeded" | "failed" | "duplicate";
  progress: number;
  message: string;
  error?: string;
}

export interface WorkbenchPreferences {
  autoRefresh: boolean;
  livePollIntervalMs: number;
}

export type WorkbenchMode = "core" | "scenic";
