import { api } from "@/api/endpoints";
import type { RegistrationCode } from "@/api/types";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Panel } from "@/components/Page";
import { useToast } from "@/components/Toast";
import { getErrorMessage } from "@/lib/errors";
import { formatDateTime, formatNumber, fromDateTimeLocal } from "@/lib/utils";
import { Clipboard, KeyRound, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

type CodeStatus = "active" | "exhausted" | "expired" | "revoked";

function registrationCodeStatus(code: RegistrationCode): CodeStatus {
  if (!code.active || code.revoked_at) return "revoked";
  if (code.expires_at && new Date(code.expires_at).getTime() <= Date.now()) return "expired";
  if (code.max_uses !== null && code.use_count >= code.max_uses) return "exhausted";
  return "active";
}

const statusLabel: Record<CodeStatus, string> = {
  active: "사용 가능",
  exhausted: "소진됨",
  expired: "만료됨",
  revoked: "폐기됨",
};

export function AdminRegistrationCodes({ readOnly }: { readOnly: boolean }) {
  const { push } = useToast();
  const [codes, setCodes] = useState<RegistrationCode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [label, setLabel] = useState("");
  const [maxUses, setMaxUses] = useState("1");
  const [unlimited, setUnlimited] = useState(false);
  const [expiresAt, setExpiresAt] = useState("");
  const [creating, setCreating] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [newCode, setNewCode] = useState<{ id: string; label: string; value: string } | null>(null);
  const mounted = useRef(true);
  const loadGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setError("");
    try {
      const rows = await api.admin.registrationCodes();
      if (mounted.current && generation === loadGeneration.current) setCodes(rows);
    } catch (requestError) {
      if (mounted.current && generation === loadGeneration.current) setError(getErrorMessage(requestError));
    } finally {
      if (mounted.current && generation === loadGeneration.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      loadGeneration.current += 1;
    };
  }, [load]);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (readOnly || creating || revokingId) return;
    const normalizedLabel = label.trim();
    const parsedMaxUses = unlimited ? null : Number(maxUses);
    if (!normalizedLabel) {
      setError("코드 용도를 구분할 라벨을 입력해 주세요.");
      return;
    }
    if (parsedMaxUses !== null && (!Number.isInteger(parsedMaxUses) || parsedMaxUses < 1 || parsedMaxUses > 10_000)) {
      setError("사용 횟수는 1에서 10,000 사이의 정수여야 합니다.");
      return;
    }
    if (expiresAt && new Date(expiresAt).getTime() <= Date.now()) {
      setError("만료 시각은 현재보다 뒤여야 합니다.");
      return;
    }

    setCreating(true);
    setError("");
    try {
      const created = await api.admin.createRegistrationCode({
        label: normalizedLabel,
        max_uses: parsedMaxUses,
        expires_at: fromDateTimeLocal(expiresAt),
      });
      const { access_code, ...stored } = created;
      setCodes((current) => [stored, ...current.filter((item) => item.id !== stored.id)]);
      setNewCode({ id: stored.id, label: stored.label, value: access_code });
      setLabel("");
      setMaxUses("1");
      setUnlimited(false);
      setExpiresAt("");
      push("등록 접근 코드를 생성했습니다.", "success");
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setCreating(false);
    }
  };

  const revoke = async (code: RegistrationCode) => {
    if (readOnly || creating || revokingId || registrationCodeStatus(code) === "revoked") return;
    if (!window.confirm(`“${code.label}” 등록 코드를 폐기할까요? 다시 활성화할 수 없습니다.`)) return;
    setRevokingId(code.id);
    setError("");
    try {
      await api.admin.revokeRegistrationCode(code.id);
      const revokedAt = new Date().toISOString();
      setCodes((current) => current.map((item) => item.id === code.id
        ? { ...item, active: false, revoked_at: item.revoked_at ?? revokedAt }
        : item));
      if (newCode?.id === code.id) setNewCode(null);
      push("등록 접근 코드를 폐기했습니다.", "success");
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setRevokingId(null);
    }
  };

  const copy = async () => {
    if (!newCode) return;
    try {
      await navigator.clipboard.writeText(newCode.value);
      push("등록 접근 코드를 복사했습니다.", "success");
    } catch {
      push("코드를 복사하지 못했습니다. 직접 선택해 복사하세요.", "error");
    }
  };

  return (
    <Panel
      actions={(
        <button className="button button--secondary button--small" disabled={loading || creating || revokingId !== null} onClick={() => void load()} type="button">
          <RefreshCw size={15} /> 새로고침
        </button>
      )}
      description="코드 원문은 생성 직후 한 번만 표시되며 서버에는 HMAC만 저장됩니다."
      title="등록 접근 코드"
    >
      {readOnly && <div className="warning-box" role="status"><ShieldCheck /><p>보관된 이벤트의 코드는 열람만 할 수 있습니다.</p></div>}

      {!readOnly && (
        <form className="registration-code-form" onSubmit={create}>
          <label className="field"><span>라벨</span><input maxLength={80} onChange={(event) => setLabel(event.target.value)} placeholder="예: 예선 참가자 1차" required value={label} /></label>
          <label className="field"><span>최대 사용 횟수</span><input disabled={unlimited} max={10_000} min={1} onChange={(event) => setMaxUses(event.target.value)} required={!unlimited} type="number" value={maxUses} /></label>
          <label className="field"><span>만료 시각</span><input onChange={(event) => setExpiresAt(event.target.value)} type="datetime-local" value={expiresAt} /><small>비워 두면 만료 시각을 두지 않습니다.</small></label>
          <label className="checkbox-control registration-code-unlimited"><input aria-label="사용 횟수 무제한" checked={unlimited} onChange={(event) => setUnlimited(event.target.checked)} type="checkbox" />사용 횟수 무제한<small>유출 시 피해가 커질 수 있으므로 제한 사용을 권장합니다.</small></label>
          <button className="button button--primary" disabled={creating || revokingId !== null} type="submit"><Plus size={16} /> {creating ? "생성 중…" : "코드 생성"}</button>
        </form>
      )}

      {newCode && (
        <div className="invite-reveal registration-code-reveal" role="status">
          <KeyRound />
          <div><strong>{newCode.label} · 지금 한 번만 표시</strong><p>페이지를 떠나기 전에 안전한 채널에 보관하세요. 감사 로그와 목록에는 원문이 남지 않습니다.</p><code>{newCode.value}</code></div>
          <button className="button button--secondary button--small" onClick={() => void copy()} type="button"><Clipboard size={15} /> 복사</button>
        </div>
      )}

      {error && codes.length > 0 && <div className="registration-code-state"><ErrorState message={error} onRetry={load} /></div>}
      {loading ? <LoadingState label="등록 접근 코드를 불러오는 중" /> : error && codes.length === 0 ? (
        <ErrorState message={error} onRetry={load} />
      ) : codes.length === 0 ? (
        <EmptyState title="발급된 코드가 없습니다" description="코드 가입 모드를 켜기 전에 사용할 코드를 먼저 생성하세요." />
      ) : (
        <div className="table-scroll registration-code-list">
          <table className="data-table">
            <caption className="sr-only">등록 접근 코드 목록</caption>
            <thead><tr><th scope="col">라벨</th><th scope="col">상태</th><th scope="col">사용량</th><th scope="col">만료</th><th scope="col">생성 시각</th><th scope="col"><span className="sr-only">작업</span></th></tr></thead>
            <tbody>{codes.map((code) => {
              const status = registrationCodeStatus(code);
              return (
                <tr key={code.id}>
                  <th scope="row">{code.label}</th>
                  <td><span className={`registration-code-badge is-${status}`}>{statusLabel[status]}</span></td>
                  <td>{formatNumber(code.use_count)} / {code.max_uses === null ? "무제한" : formatNumber(code.max_uses)}</td>
                  <td>{code.expires_at ? <time dateTime={code.expires_at}>{formatDateTime(code.expires_at)}</time> : "없음"}</td>
                  <td><time dateTime={code.created_at}>{formatDateTime(code.created_at)}</time></td>
                  <td><div className="row-actions"><button aria-label={`${code.label} 코드 폐기`} className="icon-button icon-button--danger" disabled={readOnly || creating || revokingId !== null || status === "revoked"} onClick={() => void revoke(code)} title="폐기" type="button"><Trash2 size={15} /></button></div></td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
