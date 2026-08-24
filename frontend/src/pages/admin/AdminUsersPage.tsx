import { api } from "@/api/endpoints";
import type { AdminUser } from "@/api/types";
import { Modal } from "@/components/Modal";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { PageHeader, Panel } from "@/components/Page";
import { useToast } from "@/components/Toast";
import { getErrorMessage } from "@/lib/errors";
import { formatDateTime, formatNumber } from "@/lib/utils";
import { RefreshCw, Search, ShieldCheck, UserCheck, UserX } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

const ADMIN_USER_PAGE_SIZE = 100;
const MAX_STATUS_REASON_LENGTH = 500;

function isAbortError(error: unknown): boolean {
  return typeof error === "object"
    && error !== null
    && "name" in error
    && error.name === "AbortError";
}

function appendUniqueUsers(current: readonly AdminUser[], next: readonly AdminUser[]): AdminUser[] {
  const seen = new Set(current.map((user) => user.id));
  return [...current, ...next.filter((user) => !seen.has(user.id))];
}

export function AdminUsersPage() {
  const { push } = useToast();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [suspending, setSuspending] = useState<AdminUser | null>(null);
  const [reason, setReason] = useState("");
  const [formError, setFormError] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);
  const loadGeneration = useRef(0);
  const statusGeneration = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  const loadPage = useCallback(async (append: boolean, offset = 0) => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const generation = ++loadGeneration.current;
    append ? setLoadingMore(true) : setLoading(true);
    if (!append) {
      setLoadingMore(false);
      setHasMore(false);
    }
    setError("");
    try {
      const rows = await api.admin.users(
        { limit: ADMIN_USER_PAGE_SIZE, offset },
        { signal: controller.signal },
      );
      if (generation !== loadGeneration.current) return;
      setUsers((current) => append ? appendUniqueUsers(current, rows) : rows);
      setHasMore(rows.length === ADMIN_USER_PAGE_SIZE);
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
  }, []);

  const load = useCallback(() => loadPage(false), [loadPage]);
  const loadMore = useCallback(() => {
    if (!hasMore || loadingMore || loading || savingId) return Promise.resolve();
    return loadPage(true, users.length);
  }, [hasMore, loadPage, loading, loadingMore, savingId, users.length]);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      loadGeneration.current += 1;
      statusGeneration.current += 1;
      requestController.current?.abort();
      requestController.current = null;
    };
  }, [load]);

  const visibleUsers = useMemo(() => {
    const value = query.toLocaleLowerCase().trim();
    return users.filter((user) => !value || [user.username, user.email, user.team?.name, user.team_name]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase()
      .includes(value));
  }, [query, users]);

  const openSuspend = (user: AdminUser) => {
    if (user.role === "admin" || savingId) return;
    setSuspending(user);
    setReason("");
    setFormError("");
  };

  const closeSuspend = useCallback(() => {
    if (savingId) return;
    setSuspending(null);
    setReason("");
    setFormError("");
  }, [savingId]);

  const suspend = async (event: FormEvent) => {
    event.preventDefault();
    if (!suspending || suspending.role === "admin" || savingId) return;
    const normalizedReason = reason.trim();
    if (!normalizedReason) {
      setFormError("정지 사유를 입력해 주세요.");
      return;
    }
    if (normalizedReason.length > MAX_STATUS_REASON_LENGTH) {
      setFormError(`정지 사유는 ${MAX_STATUS_REASON_LENGTH}자 이하여야 합니다.`);
      return;
    }
    if (!window.confirm(`${suspending.username} 계정을 정지할까요? 활성 세션도 사용할 수 없게 됩니다.`)) return;

    const target = suspending;
    const generation = ++statusGeneration.current;
    setSavingId(target.id);
    setFormError("");
    try {
      const updated = await api.admin.setUserStatus(target.id, {
        active: false,
        reason: normalizedReason,
      });
      if (!mounted.current || generation !== statusGeneration.current) return;
      setUsers((current) => current.map((user) => user.id === updated.id ? updated : user));
      setSuspending(null);
      setReason("");
      push(`${updated.username} 계정을 정지했습니다.`, "success");
    } catch (requestError) {
      if (mounted.current && generation === statusGeneration.current) {
        setFormError(getErrorMessage(requestError));
      }
    } finally {
      if (mounted.current && generation === statusGeneration.current) setSavingId(null);
    }
  };

  const reactivate = async (user: AdminUser) => {
    if (user.role === "admin" || savingId) return;
    if (!window.confirm(`${user.username} 계정을 다시 활성화할까요?`)) return;

    const generation = ++statusGeneration.current;
    setSavingId(user.id);
    try {
      const updated = await api.admin.setUserStatus(user.id, { active: true, reason: "" });
      if (!mounted.current || generation !== statusGeneration.current) return;
      setUsers((current) => current.map((item) => item.id === updated.id ? updated : item));
      push(`${updated.username} 계정을 다시 활성화했습니다.`, "success");
    } catch (requestError) {
      if (mounted.current && generation === statusGeneration.current) {
        push(getErrorMessage(requestError), "error");
      }
    } finally {
      if (mounted.current && generation === statusGeneration.current) setSavingId(null);
    }
  };

  return (
    <div>
      <PageHeader
        actions={(
          <button
            aria-label="사용자 목록 새로고침"
            className="button button--secondary button--small"
            disabled={savingId !== null}
            onClick={() => void load()}
            type="button"
          >
            <RefreshCw size={15} /> 새로고침
          </button>
        )}
        description="참가자 계정 상태를 검토하고 필요한 경우 접근을 정지합니다."
        eyebrow="PARTICIPANT MODERATION"
        title="사용자 관리"
      />

      <div className="admin-toolbar">
        <label className="search-field">
          <span className="sr-only">사용자 검색</span>
          <Search size={17} />
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="사용자명, 이메일, 팀 검색"
            type="search"
            value={query}
          />
        </label>
        <span>불러온 사용자 {formatNumber(users.length)}명 · 활성 {formatNumber(users.filter((user) => user.active).length)}명</span>
      </div>

      {loading ? <LoadingState label="사용자 목록을 불러오는 중" /> : error && users.length === 0 ? (
        <ErrorState message={error} onRetry={load} />
      ) : users.length === 0 ? (
        <EmptyState title="등록된 사용자가 없습니다" description="참가자가 등록되면 이곳에서 계정 상태를 관리할 수 있습니다." />
      ) : (
        <>
          {error && <ErrorState message={error} onRetry={load} />}
          {visibleUsers.length === 0 ? (
            <EmptyState title="검색 결과 없음" description="다른 검색어를 입력해 보세요." />
          ) : (
            <Panel className="flush-panel">
              <div className="table-scroll">
                <table className="data-table admin-users-table">
                  <caption className="sr-only">관리자 사용자 목록</caption>
                  <thead><tr><th scope="col">사용자</th><th scope="col">역할</th><th scope="col">팀</th><th scope="col">등록 시각</th><th scope="col">상태</th><th scope="col"><span className="sr-only">작업</span></th></tr></thead>
                  <tbody>{visibleUsers.map((user) => (
                    <tr key={user.id}>
                      <th scope="row"><strong>{user.username}</strong><small>{user.email}</small></th>
                      <td>{user.role === "admin" ? <span className="protected-admin"><ShieldCheck size={14} /> 관리자</span> : "참가자"}</td>
                      <td>{user.team?.name ?? user.team_name ?? "—"}</td>
                      <td>{user.created_at ? <time dateTime={user.created_at}>{formatDateTime(user.created_at)}</time> : "—"}</td>
                      <td><span className={`user-status-badge ${user.active ? "is-active" : "is-suspended"}`}>{user.active ? "활성" : "정지"}</span></td>
                      <td>
                        {user.role === "admin" ? <span className="protected-label">보호됨</span> : (
                          <div className="row-actions">
                            {user.active ? (
                              <button
                                aria-label={`${user.username} 계정 정지`}
                                className="button button--danger-ghost button--small"
                                disabled={savingId !== null}
                                onClick={() => openSuspend(user)}
                                type="button"
                              >
                                <UserX size={14} /> {savingId === user.id ? "처리 중…" : "정지"}
                              </button>
                            ) : (
                              <button
                                aria-label={`${user.username} 계정 재활성화`}
                                className="button button--secondary button--small"
                                disabled={savingId !== null}
                                onClick={() => void reactivate(user)}
                                type="button"
                              >
                                <UserCheck size={14} /> {savingId === user.id ? "처리 중…" : "재활성화"}
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </Panel>
          )}
          <div className="submission-pagination" role="status">
            <p>서버에서 현재 {formatNumber(users.length)}명을 불러왔습니다. 검색은 현재 불러온 범위에 적용됩니다.</p>
            {hasMore ? (
              <button className="button button--secondary" disabled={loadingMore || savingId !== null} onClick={() => void loadMore()} type="button">
                {loadingMore ? "불러오는 중…" : "더 불러오기"}
              </button>
            ) : <span>마지막 사용자까지 불러왔습니다.</span>}
          </div>
        </>
      )}

      <Modal
        description="사유는 보안 감사 기록에 저장됩니다. 비밀번호나 플래그 같은 민감 정보는 입력하지 마세요."
        onClose={closeSuspend}
        open={suspending !== null}
        title={suspending ? `${suspending.username} 계정 정지` : "계정 정지"}
      >
        <form className="moderation-form" onSubmit={suspend}>
          <label className="field">
            <span>정지 사유</span>
            <textarea
              aria-label="정지 사유"
              autoFocus
              maxLength={MAX_STATUS_REASON_LENGTH}
              onChange={(event) => setReason(event.target.value)}
              placeholder="계정을 정지하는 운영상 근거를 입력하세요."
              required
              rows={5}
              value={reason}
            />
            <small>{reason.length}/{MAX_STATUS_REASON_LENGTH}자</small>
          </label>
          {formError && <div className="form-error" role="alert">{formError}</div>}
          <div className="form-actions">
            <button className="button button--danger-ghost" disabled={savingId !== null} type="submit">
              <UserX size={16} /> {savingId ? "정지 처리 중…" : "사유 확인 후 정지"}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
