import type { EventState } from "@/api/types";
import { eventStateLabel } from "@/lib/utils";

export function EventStatusPill({ state }: { state: EventState }) {
  return <span className={`status-pill status-pill--${state}`}>{eventStateLabel[state]}</span>;
}

export function ResultPill({ correct }: { correct: boolean }) {
  return (
    <span className={`result-pill result-pill--${correct ? "correct" : "wrong"}`}>
      {correct ? "정답" : "오답"}
    </span>
  );
}
