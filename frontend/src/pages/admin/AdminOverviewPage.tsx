import { api } from "@/api/endpoints";
import type { AdminChallenge, AdminEvent, AdminSubmission, AdminTeam, AdminUser } from "@/api/types";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { EventStatusPill, ResultPill } from "@/components/StatusPill";
import { PageHeader, Panel } from "@/components/Page";
import { getErrorMessage } from "@/lib/errors";
import { formatDateTime, formatNumber, formatRelative } from "@/lib/utils";
import { ArrowRight, Boxes, CircleDot, Flag, Radio, ShieldCheck, UsersRound } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

interface DashboardData {
  event: AdminEvent;
  challenges: AdminChallenge[];
  submissions: AdminSubmission[];
  users: AdminUser[];
  teams: AdminTeam[];
}

export function countParticipantUsers(users: readonly AdminUser[]): number {
  return users.filter((user) => user.role === "participant").length;
}

export function AdminOverviewPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [event, challenges, submissions, users, teams] = await Promise.all([
        api.admin.event(), api.admin.challenges(), api.admin.submissions({ limit: 8 }), api.admin.users({ limit: 500 }), api.admin.teams({ limit: 500 }),
      ]);
      setData({ event, challenges, submissions, users, teams });
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (loading) return <LoadingState label="운영 현황을 집계하는 중" />;
  if (error || !data) return <ErrorState message={error || "운영 정보를 불러오지 못했습니다."} onRetry={load} />;

  const visible = data.challenges.filter((challenge) => challenge.visible).length;
  const correct = data.submissions.filter((submission) => submission.correct).length;
  const participantUsers = countParticipantUsers(data.users);

  return (
    <div>
      <PageHeader eyebrow="OPERATIONS" title="운영 개요" description="이벤트 상태와 주요 활동을 한눈에 확인하세요." actions={<EventStatusPill state={data.event.state} />} />
      <section className="admin-event-strip">
        <div><span className="live-dot" /><div><small>현재 이벤트</small><strong>{data.event.name}</strong></div></div>
        <dl><div><dt>시작</dt><dd>{formatDateTime(data.event.start_at)}</dd></div><div><dt>종료</dt><dd>{formatDateTime(data.event.end_at)}</dd></div></dl>
        <Link className="button button--secondary button--small" to="/admin/settings">설정 <ArrowRight size={15} /></Link>
      </section>

      <div className="metric-grid">
        <article className="metric-card"><span className="metric-card__icon"><UsersRound /></span><div><small>조회된 참가자</small><strong>{formatNumber(participantUsers)}</strong><p>참가자·팀 조회 상한 각 500 · 팀 {formatNumber(data.teams.length)}개</p></div></article>
        <article className="metric-card"><span className="metric-card__icon"><Boxes /></span><div><small>문제</small><strong>{formatNumber(data.challenges.length)}</strong><p>{visible}개 공개</p></div></article>
        <article className="metric-card"><span className="metric-card__icon"><Flag /></span><div><small>최근 활동</small><strong>{formatNumber(data.submissions.length)}</strong><p>최대 8건 중 {correct}개 정답</p></div></article>
        <article className="metric-card"><span className="metric-card__icon"><CircleDot /></span><div><small>플랫폼</small><strong className="metric-text">정상</strong><p>API 연결됨</p></div></article>
      </div>

      <div className="admin-dashboard-grid">
        <Panel title="최근 제출" description="가장 최근의 채점 활동입니다." actions={<Link className="text-link" to="/admin/submissions">전체 보기 <ArrowRight size={14} /></Link>}>
          {data.submissions.length === 0 ? <EmptyState title="제출 없음" description="경기가 시작되면 활동이 나타납니다." /> : (
            <div className="activity-list">
              {data.submissions.slice(0, 8).map((submission) => (
                <div className="activity-row" key={submission.id}>
                  <span className={`activity-dot ${submission.correct ? "is-correct" : ""}`} />
                  <div><strong>{submission.team_name ?? submission.username}</strong><p>{submission.challenge_title}</p></div>
                  <ResultPill correct={submission.correct} />
                  <time title={formatDateTime(submission.created_at)}>{formatRelative(submission.created_at)}</time>
                </div>
              ))}
            </div>
          )}
        </Panel>
        <Panel title="빠른 작업" description="자주 쓰는 운영 기능입니다.">
          <div className="quick-actions">
            <Link to="/admin/challenges"><Boxes /><div><strong>문제 관리</strong><span>생성·수정·공개</span></div><ArrowRight /></Link>
            <Link to="/admin/announcements"><Radio /><div><strong>공지 전송</strong><span>참가자에게 메시지 전달</span></div><ArrowRight /></Link>
            <Link to="/admin/submissions"><ShieldCheck /><div><strong>제출 감사</strong><span>채점 결과와 요청 추적</span></div><ArrowRight /></Link>
          </div>
        </Panel>
      </div>
    </div>
  );
}
