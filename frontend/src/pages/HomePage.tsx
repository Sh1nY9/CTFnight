import { api } from "@/api/endpoints";
import type { Announcement, CurrentUser, EventSummary } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { Markdown } from "@/components/Markdown";
import { ErrorState, LoadingState } from "@/components/States";
import { EventStatusPill } from "@/components/StatusPill";
import { getErrorMessage } from "@/lib/errors";
import { eventStateLabel, formatDateTime, formatRelative } from "@/lib/utils";
import { ArrowRight, Braces, Clock3, Radio, ShieldCheck, Sparkles, Trophy, UsersRound } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

export function getHomePrimaryAction(user: CurrentUser | null, event: EventSummary | null) {
  if (!user) {
    return event?.state === "registration"
      ? { to: "/register", label: "참가 계정 만들기" }
      : { to: "/login", label: "로그인" };
  }
  if (!user.team && event?.state === "registration") return { to: "/team", label: "팀 구성하기" };
  if (event?.state === "live" || event?.state === "frozen") return { to: "/challenges", label: "문제 풀기" };
  return { to: "/scoreboard", label: "점수판 보기" };
}

export function HomePage() {
  const { user } = useAuth();
  const [event, setEvent] = useState<EventSummary | null>(null);
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [currentEvent, notices] = await Promise.all([
        api.participant.event(),
        api.participant.announcements(),
      ]);
      setEvent(currentEvent);
      setAnnouncements(notices);
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const primaryAction = useMemo(() => getHomePrimaryAction(user, event), [user, event]);

  return (
    <div className="home-page">
      <section className="hero">
        <div className="hero__copy">
          <p className="eyebrow"><Radio size={14} /> INDEPENDENT SECURITY ARENA</p>
          {loading ? (
            <div className="hero-title-skeleton" aria-label="이벤트 정보 로딩 중" />
          ) : (
            <>
              <div className="hero__status">
                {event && <EventStatusPill state={event.state} />}
                <span>{event ? eventStateLabel[event.state] : "이벤트 준비 중"}</span>
              </div>
              <h1><span>Break assumptions.</span><br />Build capability.</h1>
              <p className="hero__lead">
                {event?.name ?? "CTFnight"}에서 실전 보안 문제를 해결하고 팀과 함께 성장하세요.
                모든 점수와 제출은 투명하고 일관된 규칙으로 처리됩니다.
              </p>
            </>
          )}
          <div className="hero__actions">
            <Link className="button button--primary button--large" to={primaryAction.to}>
              {primaryAction.label} <ArrowRight size={18} />
            </Link>
            <Link className="button button--ghost button--large" to="/scoreboard">
              <Trophy size={18} /> 실시간 순위
            </Link>
          </div>
          {event && (
            <dl className="hero__timeline">
              <div><dt>시작</dt><dd>{formatDateTime(event.start_at)}</dd></div>
              <div><dt>종료</dt><dd>{formatDateTime(event.end_at)}</dd></div>
              <div><dt>경기 방식</dt><dd>{event.team_mode === "individual" ? "개인전" : "팀전"}</dd></div>
            </dl>
          )}
        </div>
        <div className="hero__visual" aria-hidden="true">
          <div className="terminal-card">
            <div className="terminal-card__top"><i /><i /><i /><span>ctfnight://arena</span></div>
            <div className="terminal-card__body">
              <p><span className="terminal-prompt">$</span> nmap -sV target.ctfnight</p>
              <p className="terminal-muted">PORT&nbsp;&nbsp;&nbsp;&nbsp;STATE&nbsp;&nbsp;SERVICE</p>
              <p>31337/tcp <span className="terminal-green">open</span>&nbsp;&nbsp;&nbsp;challenge</p>
              <p><span className="terminal-prompt">$</span> ./solve</p>
              <p className="terminal-green">[+] flag accepted · +500 pts</p>
              <p><span className="terminal-prompt">$</span> <span className="terminal-cursor">_</span></p>
            </div>
          </div>
          <div className="signal signal--one" /><div className="signal signal--two" />
        </div>
      </section>

      {error && <ErrorState message={error} onRetry={load} />}

      {event?.description_md && (
        <section className="event-intro">
          <p className="eyebrow">CURRENT EVENT</p>
          <Markdown>{event.description_md}</Markdown>
        </section>
      )}

      <section className="feature-strip" aria-label="플랫폼 특징">
        <article><Braces /><div><strong>다양한 문제</strong><span>Web부터 Pwn, Crypto까지</span></div></article>
        <article><UsersRound /><div><strong>팀 기반 협업</strong><span>초대 코드로 빠르게 합류</span></div></article>
        <article><ShieldCheck /><div><strong>공정한 경기</strong><span>원자적 채점과 감사 기록</span></div></article>
        <article><Sparkles /><div><strong>실시간 반영</strong><span>풀이와 점수를 빠르게 확인</span></div></article>
      </section>

      <section className="home-section">
        <div className="section-heading">
          <div><p className="eyebrow">TRANSMISSIONS</p><h2>최근 공지</h2></div>
          <span>{announcements.length}개의 메시지</span>
        </div>
        {loading ? <LoadingState label="공지를 불러오는 중" /> : announcements.length === 0 ? (
          <div className="notice-empty"><Radio /><p>아직 전달된 공지가 없습니다.</p></div>
        ) : (
          <div className="announcement-grid">
            {announcements.slice(0, 6).map((announcement, index) => (
              <article className="announcement-card" key={announcement.id}>
                <div className="announcement-card__meta">
                  <span>#{String(index + 1).padStart(2, "0")}</span>
                  <time dateTime={announcement.published_at ?? announcement.created_at}>
                    <Clock3 size={13} /> {formatRelative(announcement.published_at ?? announcement.publish_at ?? announcement.created_at)}
                  </time>
                </div>
                <h3>{announcement.title}</h3>
                <Markdown>{announcement.body_md ?? announcement.body}</Markdown>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
