export const PROTOCOL_VERSION = "1.0.0" as const;

export type ProtectionStatus = "INITIALIZING" | "UNAVAILABLE";

export interface FoundationStatus {
  readonly protocolVersion: typeof PROTOCOL_VERSION;
  readonly protectionStatus: ProtectionStatus;
}

export function foundationStatus(): FoundationStatus {
  return {
    protocolVersion: PROTOCOL_VERSION,
    protectionStatus: "INITIALIZING",
  };
}
