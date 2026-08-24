import { CircleAlert, Inbox, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

export function LoadingState({ label = "불러오는 중" }: { label?: string }) {
  return (
    <div className="state-card" role="status">
      <LoaderCircle className="spin" size={28} aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-card state-card--error" role="alert">
      <CircleAlert size={28} aria-hidden="true" />
      <div>
        <strong>문제가 발생했습니다</strong>
        <p>{message}</p>
      </div>
      {onRetry && (
        <button className="button button--secondary button--small" onClick={onRetry} type="button">
          <RefreshCw size={15} /> 다시 시도
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-card state-card--empty">
      <Inbox size={30} aria-hidden="true" />
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </div>
  );
}
