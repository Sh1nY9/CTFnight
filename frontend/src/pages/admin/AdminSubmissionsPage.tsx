import { api } from "@/api/endpoints";
import type { AdminChallenge, AdminSubmission } from "@/api/types";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { ResultPill } from "@/components/StatusPill";
import { PageHeader, Panel } from "@/components/Page";
import {
  ADMIN_SUBMISSION_PAGE_SIZE,
  appendUniqueSubmissions,
  filterAdminSubmissions,
  isAdminSubmissionExportReady,
  serializeAdminSubmissionsCsv,
  submissionCursor,
  type AdminSubmissionCursor,
} from "@/lib/adminSubmissions";
import { getErrorMessage } from "@/lib/errors";
import { formatDateTime, formatNumber } from "@/lib/utils";
import { Download, Filter, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

function isAbortError(error: unknown): boolean {
  return typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError";
}

export function AdminSubmissionsPage() {
  const [submissions, setSubmissions] = useState<AdminSubmission[]>([]);
  const [challenges, setChallenges] = useState<AdminChallenge[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [result, setResult] = useState("all");
  const [challengeId, setChallengeId] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [loadedFilterKey, setLoadedFilterKey] = useState<string | null>(null);
  const loadGeneration = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const activeFilterKey = `${result}\u0000${challengeId}`;

  const loadPage = useCallback(async (append: boolean, cursor?: AdminSubmissionCursor) => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const generation = ++loadGeneration.current;
    const requestedFilterKey = activeFilterKey;
    append ? setLoadingMore(true) : setLoading(true);
    if (!append) {
      setLoadingMore(false);
      setHasMore(false);
      setLoadedFilterKey(null);
    }
    setError("");
    try {
      const [rows, challengeRows] = await Promise.all([
        api.admin.submissions({
          correct: result === "all" ? undefined : result === "correct",
          challenge_id: challengeId || undefined,
          limit: ADMIN_SUBMISSION_PAGE_SIZE,
          before_created_at: cursor?.created_at,
          before_id: cursor?.id,
        }, { signal: controller.signal }),
        append ? Promise.resolve(null) : api.admin.challenges({ signal: controller.signal }),
      ]);
      if (generation !== loadGeneration.current) return;
      setSubmissions((current) => append ? appendUniqueSubmissions(current, rows) : rows);
      if (challengeRows) setChallenges(challengeRows);
      setHasMore(rows.length === ADMIN_SUBMISSION_PAGE_SIZE);
      setLoadedFilterKey(requestedFilterKey);
    } catch (requestError) {
      if (generation === loadGeneration.current && !isAbortError(requestError)) {
        setError(getErrorMessage(requestError));
      }
    } finally {
      if (generation === loadGeneration.current) {
        append ? setLoadingMore(false) : setLoading(false);
      }
      if (requestController.current === controller) requestController.current = null;
    }
  }, [result, challengeId, activeFilterKey]);

  const load = useCallback(() => loadPage(false), [loadPage]);
  const loadMore = useCallback(() => {
    const cursor = submissionCursor(submissions);
    if (!cursor || !hasMore || loadingMore) return Promise.resolve();
    return loadPage(true, cursor);
  }, [hasMore, loadPage, loadingMore, submissions]);

  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current += 1;
      requestController.current?.abort();
      requestController.current = null;
    };
  }, [load]);
  const applySearch = (event: FormEvent) => { event.preventDefault(); setAppliedSearch(search.trim()); };

  const visibleSubmissions = useMemo(
    () => filterAdminSubmissions(submissions, appliedSearch),
    [submissions, appliedSearch],
  );
  const stats = useMemo(() => ({
    correct: visibleSubmissions.filter((item) => item.correct).length,
    wrong: visibleSubmissions.filter((item) => !item.correct).length,
  }), [visibleSubmissions]);
  const exportReady = isAdminSubmissionExportReady({
    loading: loading || loadingMore,
    error,
    activeFilterKey,
    loadedFilterKey,
    count: visibleSubmissions.length,
  });

  const exportCsv = () => {
    if (!exportReady) return;
    const csv = serializeAdminSubmissionsCsv(visibleSubmissions);
    const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `ctfnight-submissions-${new Date().toISOString().slice(0, 10)}.csv`; anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <PageHeader eyebrow="AUDIT TRAIL" title="제출 감사" description="플래그 원문 없이 채점 활동과 결과를 검토합니다." actions={<button className="button button--secondary button--small" disabled={!exportReady} onClick={exportCsv} type="button"><Download size={15} /> 현재 {formatNumber(visibleSubmissions.length)}개 CSV</button>} />

      <div className="submission-stats">
        <div><span>현재 조회 결과</span><strong>{formatNumber(visibleSubmissions.length)}</strong></div>
        <div><span>정답</span><strong className="text-success">{formatNumber(stats.correct)}</strong></div>
        <div><span>오답</span><strong className="text-danger">{formatNumber(stats.wrong)}</strong></div>
        <div><ShieldCheck /><p>제출값은 HMAC 해시만 보존됩니다.</p></div>
      </div>

      <form className="filter-bar" onSubmit={applySearch}>
        <label className="search-field"><span className="sr-only">팀 또는 사용자 검색</span><Search size={17} /><input onChange={(event) => setSearch(event.target.value)} placeholder="팀 또는 사용자 검색" type="search" value={search} /></label>
        <label><span className="sr-only">채점 결과</span><select onChange={(event) => setResult(event.target.value)} value={result}><option value="all">모든 결과</option><option value="correct">정답만</option><option value="wrong">오답만</option></select></label>
        <label><span className="sr-only">문제</span><select onChange={(event) => setChallengeId(event.target.value)} value={challengeId}><option value="">모든 문제</option>{challenges.map((challenge) => <option key={challenge.id} value={challenge.id}>{challenge.title}</option>)}</select></label>
        <button className="button button--secondary button--small" type="submit"><Filter size={15} /> 적용</button>
        <button aria-label="새로고침" className="icon-button" onClick={() => void load()} title="새로고침" type="button"><RefreshCw size={16} /></button>
      </form>

      {loading ? <LoadingState label="제출 기록을 불러오는 중" /> : error ? <ErrorState message={error} onRetry={load} /> : submissions.length === 0 ? (
        <EmptyState title="조건에 맞는 제출이 없습니다" description="필터를 변경하거나 경기가 시작될 때까지 기다려 주세요." />
      ) : (
        <>
          {visibleSubmissions.length === 0 ? <EmptyState title="현재 불러온 범위에 검색 결과가 없습니다" description="다른 검색어를 사용하거나 다음 페이지를 더 불러오세요." /> : (
            <Panel className="flush-panel">
              <div className="table-scroll">
                <table className="data-table submissions-table">
                  <caption className="sr-only">제출 감사 기록</caption>
                  <thead><tr><th scope="col">시각</th><th scope="col">팀 / 사용자</th><th scope="col">문제</th><th scope="col">결과</th><th scope="col">점수</th><th scope="col">제출 / IP 지문</th></tr></thead>
                  <tbody>{visibleSubmissions.map((submission) => (
                    <tr key={submission.id}>
                      <td><time dateTime={submission.created_at}>{formatDateTime(submission.created_at)}</time></td>
                      <td><strong>{submission.team_name ?? "개인"}</strong><small>{submission.username}</small></td>
                      <td>{submission.challenge_title}</td>
                      <td><ResultPill correct={submission.correct} /></td>
                      <td>{submission.correct ? `+${formatNumber(submission.awarded_points ?? 0)}` : "—"}</td>
                      <td><code title="제출값 HMAC 지문">{submission.submitted_fingerprint ?? "—"}</code><small title="IP HMAC 지문">IP {submission.ip_fingerprint ?? "—"}</small></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </Panel>
          )}
          <div className="submission-pagination" role="status">
            <p>서버에서 현재 {formatNumber(submissions.length)}개를 불러왔습니다. CSV에는 이 범위에서 검색된 {formatNumber(visibleSubmissions.length)}개만 포함됩니다.</p>
            {hasMore ? <button className="button button--secondary" disabled={loadingMore} onClick={() => void loadMore()} type="button">{loadingMore ? "불러오는 중…" : "더 불러오기"}</button> : <span>마지막 기록까지 불러왔습니다.</span>}
          </div>
        </>
      )}
    </div>
  );
}
