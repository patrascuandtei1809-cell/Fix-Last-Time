export enum ErrorType {
  DATA_ERROR = "DATA_ERROR",
  PROVIDER_FAILURE = "PROVIDER_FAILURE",
  TIMEOUT = "TIMEOUT",
  INSUFFICIENT_DATA = "INSUFFICIENT_DATA",
  CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED",
  SCORING_FAILURE = "SCORING_FAILURE",
  UNKNOWN_ASSET = "UNKNOWN_ASSET",
}

export class ResearchError extends Error {
  readonly type: ErrorType;
  readonly detail: Record<string, unknown>;

  constructor(type: ErrorType, message: string, detail: Record<string, unknown> = {}) {
    super(message);
    this.name = "ResearchError";
    this.type = type;
    this.detail = detail;
  }
}
