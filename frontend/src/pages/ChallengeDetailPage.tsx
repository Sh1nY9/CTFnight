import { api } from "@/api/endpoints";
import type { Challenge, EventSummary, SubmissionResult } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Markdown } from "@/components/Markdown";
import { ErrorState, LoadingState } from "@/components/States";
import { useToast } from "@/components/Toast";
import { submissionsAreOpen } from "@/lib/eventGates";
import { getErrorMessage } from "@/lib/errors";
import { DEFAULT_MAX_FLAG_LENGTH, maxFlagLengthFromMeta } from "@/lib/platformLimits";
import { formatNumber, makeIdempotencyKey } from "@/lib/utils";
import { ArrowLeft, Check, Clipboard, ExternalLink, Flag, KeyRound, LockKeyhole, Send, Target, TerminalSquare, UsersRound, X } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

export function ChallengeDetailPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const { push } = useToast();
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [currentEvent, setCurrentEvent] = useState<EventSummary | null>(null);
  const [maxFlagLength, setMaxFlagLength] = useState(DEFAULT_MAX_FLAG_LENGTH);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [flag, setFlag] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<SubmissionResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [loadedChallenge, loadedEvent, meta] = await Promise.all([
        api.participant.challenge(id),
        api.participant.event(),
        api.meta().catch(() => null),
      ]);
      setChallenge(loadedChallenge);
      setCurrentEvent(loadedEvent);
      setMaxFlagLength(maxFlagLengthFromMeta(meta));
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!flag.trim() || !challenge || !submissionsAreOpen(currentEvent)) return;
    setSubmitting(true);
    setResult(null);
    try {
      const submission = await api.participant.submit(challenge.id, flag.trim(), makeIdempotencyKey());
      setResult(submission);
      setFlag("");
      if (submission.correct) {
        push(`${formatNumber(submission.awarded_points)}점을 획득했습니다!`, "success");
        await load();
      }
    } catch (requestError) {
      setResult({ correct: false, message: getErrorMessage(requestError), awarded_points: 0, solved_at: null });
    } finally {
      setSubmitting(false);
    }
  };

  const copyConnection = async () => {
    if (!challenge?.connection_info) return;
    try {
      await navigator.clipboard.writeText(challenge.connection_info);
      push("접속 정보를 복사했습니다.", "success");
    } catch {
      push("클립보드에 복사하지 못했습니다.", "error");
    }
  };

  if (loading) return <div className="page-container"><LoadingState label="문제를 불러오는 중" /></div>;
  if (error || !challenge) return <div className="page-container"><ErrorState message={error || "문제를 찾을 수 없습니다."} onRetry={load} /></div>;

  const exhausted = challenge.max_attempts > 0 && challenge.attempts >= challenge.max_attempts;
  const submissionOpen = submissionsAreOpen(currentEvent);
  const eventFinished = currentEvent?.state === "ended" || currentEvent?.state === "archived";

  return (
    <div className="page-container challenge-detail">
      <Link className="back-link" to="/challenges"><ArrowLeft size={16} /> 문제 목록</Link>
      <header className="challenge-detail__header">
        <div>
          <div className="challenge-detail__meta"><span>{challenge.category}</span><code>{challenge.slug}</code></div>
          <h1>{challenge.title}</h1>
        </div>
        <div className="points-orb"><strong>{formatNumber(challenge.current_points)}</strong><span>POINTS</span></div>
      </header>

      <div className="challenge-detail__layout">
        <div className="challenge-main">
          <section className="panel challenge-description">
            <div className="panel__header"><div><p className="eyebrow">MISSION BRIEF</p><h2>문제 설명</h2></div></div>
            <div className="panel__body"><Markdown>{challenge.description_md}</Markdown></div>
          </section>

          {challenge.connection_info && (
            <section className="connection-card">
              <div><TerminalSquare size={19} /><span>접속 정보</span></div>
              <code>{challenge.connection_info}</code>
              <button aria-label="접속 정보 복사" className="icon-button" onClick={copyConnection} type="button"><Clipboard size={17} /></button>
            </section>
          )}

          <section className="panel submit-panel">
            <div className="panel__header">
              <div><p className="eyebrow">FLAG SUBMISSION</p><h2>플래그 제출</h2></div>
              {challenge.solved && <span className="solved-mark solved-mark--large"><Check size={16} /> 해결 완료</span>}
            </div>
            <div className="panel__body">
              {challenge.solved ? (
                <div className="submission-success"><Check /><div><strong>이미 해결한 문제입니다.</strong><p>다른 문제에도 도전해보세요.</p></div></div>
              ) : !submissionOpen ? (
                <div className="submission-blocker"><LockKeyhole /><div><strong>{eventFinished ? "이벤트 제출이 종료되었습니다." : "현재 제출 기간이 아닙니다."}</strong><p>{eventFinished ? "종료·보관된 이벤트에서는 새 플래그를 제출할 수 없습니다." : "경기 상태와 시작·종료 시각을 확인하세요."}</p></div></div>
              ) : !user?.team ? (
                <div className="submission-blocker">
                  <UsersRound /><div><strong>먼저 팀을 구성하세요</strong><p>플래그는 팀 단위로 채점됩니다.</p></div>
                  <Link className="button button--secondary button--small" to="/team">팀 설정 <ExternalLink size={14} /></Link>
                </div>
              ) : exhausted ? (
                <div className="submission-blocker"><X /><div><strong>최대 시도 횟수에 도달했습니다.</strong><p>운영자에게 문의하세요.</p></div></div>
              ) : (
                <form className="flag-form" onSubmit={submit}>
                  <label htmlFor="flag-input">플래그</label>
                  <div>
                    <KeyRound size={18} aria-hidden="true" />
                    <input id="flag-input" autoComplete="off" maxLength={maxFlagLength} onChange={(event) => setFlag(event.target.value)} placeholder="FLAG{...}" required spellCheck={false} value={flag} />
                    <button className="button button--primary" disabled={submitting || !flag.trim()} type="submit">
                      {submitting ? "검증 중…" : <><Send size={16} /> 제출</>}
                    </button>
                  </div>
                  <small>서버 허용 한도: 최대 {formatNumber(maxFlagLength)}자</small>
                </form>
              )}
              {result && (
                <div className={`submission-result submission-result--${result.correct ? "correct" : "wrong"}`} role="status">
                  {result.correct ? <Check /> : <X />}
                  <div><strong>{result.correct ? "정답입니다!" : "아직 아닙니다."}</strong><p>{result.message}</p></div>
                </div>
              )}
            </div>
          </section>
        </div>

        <aside className="challenge-aside">
          <dl className="stat-list">
            <div><dt><Target size={16} /> 해결 팀</dt><dd>{formatNumber(challenge.solve_count)}</dd></div>
            <div><dt><Flag size={16} /> 내 시도</dt><dd>{challenge.attempts}{challenge.max_attempts > 0 ? ` / ${challenge.max_attempts}` : ""}</dd></div>
            <div><dt>점수 방식</dt><dd>{challenge.scoring_type === "dynamic" ? "동적" : "고정"}</dd></div>
          </dl>
          {challenge.prerequisite_ids.length > 0 && (
            <div className="prerequisite-note"><strong>선행 문제</strong><p>{challenge.prerequisite_ids.length}개의 문제 해결이 필요합니다.</p></div>
          )}
        </aside>
      </div>
    </div>
  );
}
