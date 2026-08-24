import { api } from "@/api/endpoints";
import type { Scoreboard } from "@/api/types";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { EventStatusPill } from "@/components/StatusPill";
import { PageHeader, Panel } from "@/components/Page";
import { getErrorMessage } from "@/lib/errors";
import { formatDateTime, formatNumber, formatRelative } from "@/lib/utils";
import { Crown, Medal, RefreshCw, Snowflake, Trophy } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

export function ScoreboardPage() {
  const [scoreboard, setScoreboard] = useState<Scoreboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (quiet = false) => {
    quiet ? setRefreshing(true) : setLoading(true);
    setError("");
    try {
      setScoreboard(await api.participant.scoreboard());
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  return (
    <div className="page-container scoreboard-page">
      <PageHeader
        eyebrow="LIVE RANKINGS"
        title="점수판"
        description="점수가 같으면 풀이 수가 많은 팀, 그다음 마지막 정답이 빠른 팀이 앞섭니다."
        actions={scoreboard ? (
          <button className="button button--secondary button--small" disabled={refreshing} onClick={() => void load(true)} type="button">
            <RefreshCw className={refreshing ? "spin" : ""} size={15} /> 새로고침
          </button>
        ) : undefined}
      />

      {loading ? <LoadingState label="순위를 집계하는 중" /> : error ? <ErrorState message={error} onRetry={() => void load()} /> : !scoreboard ? null : (
        <>
          <div className="scoreboard-meta">
            <div><EventStatusPill state={scoreboard.event.state} /><strong>{scoreboard.event.name}</strong></div>
            <div>
              {scoreboard.frozen && <span className="freeze-notice"><Snowflake size={15} /> 공개 점수 동결</span>}
              {scoreboard.truncated && (
                <span>상위 {formatNumber(scoreboard.entries.length)}팀 / 전체 {formatNumber(scoreboard.total_entries)}팀</span>
              )}
              <span title={formatDateTime(scoreboard.generated_at)}>갱신 {formatRelative(scoreboard.generated_at)}</span>
            </div>
          </div>

          {scoreboard.entries.length === 0 ? (
            <EmptyState title="아직 순위가 없습니다" description="첫 번째 문제를 해결한 팀이 이곳에 기록됩니다." />
          ) : (
            <>
              <div className="podium" aria-label="상위 3개 팀">
                {scoreboard.entries.slice(0, 3).map((entry) => (
                  <article className={`podium-card podium-card--${entry.rank}`} key={entry.team_id}>
                    <div className="podium-rank">{entry.rank === 1 ? <Crown /> : <Medal />}</div>
                    <span>#{entry.rank}</span>
                    <h2>{entry.team_name}</h2>
                    <strong>{formatNumber(entry.score)} <small>pts</small></strong>
                    <p>{formatNumber(entry.solves)} solves</p>
                  </article>
                ))}
              </div>

              <Panel className="score-table-panel">
                <div className="table-scroll">
                  <table className="data-table scoreboard-table">
                    <caption className="sr-only">전체 팀 순위</caption>
                    <thead><tr><th scope="col">순위</th><th scope="col">팀</th><th scope="col">풀이</th><th scope="col">마지막 정답</th><th scope="col">점수</th></tr></thead>
                    <tbody>
                      {scoreboard.entries.map((entry) => (
                        <tr key={entry.team_id}>
                          <td><span className={entry.rank <= 3 ? "rank-badge" : ""}>{entry.rank}</span></td>
                          <th scope="row"><span className="team-cell">{entry.rank === 1 && <Trophy size={15} />} {entry.team_name}</span></th>
                          <td>{formatNumber(entry.solves)}</td>
                          <td title={formatDateTime(entry.last_solve_at)}>{formatRelative(entry.last_solve_at)}</td>
                          <td><strong>{formatNumber(entry.score)}</strong></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </>
          )}
        </>
      )}
    </div>
  );
}
