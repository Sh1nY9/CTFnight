import { ApiError } from "@/api/client";

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "알 수 없는 오류가 발생했습니다.";
}
