import { api } from "@/api/endpoints";
import { useAuth } from "@/auth/AuthContext";
import { PageHeader, Panel } from "@/components/Page";
import { useToast } from "@/components/Toast";
import { getErrorMessage } from "@/lib/errors";
import { KeyRound, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";

export function AccountSecurityPage() {
  const { user, refresh } = useAuth();
  const { push } = useToast();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    if (newPassword !== confirmPassword) {
      setError("새 비밀번호가 서로 일치하지 않습니다.");
      return;
    }
    setSubmitting(true);
    try {
      await api.auth.changePassword(currentPassword, newPassword);
      setCurrentPassword(""); setNewPassword(""); setConfirmPassword("");
      await refresh();
      push("비밀번호를 변경했습니다.", "success");
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setSubmitting(false); }
  };

  return (
    <div className="page-container account-page">
      <PageHeader eyebrow="ACCOUNT SECURITY" title="계정 보안" description={`${user?.username} 계정의 인증 정보를 관리합니다.`} />
      <Panel title="비밀번호 변경" description="다른 서비스에서 사용하지 않는 긴 비밀번호를 사용하세요.">
        <form className="settings-form narrow-form" onSubmit={submit}>
          <label className="field"><span>현재 비밀번호</span><input autoComplete="current-password" maxLength={128} onChange={(event) => setCurrentPassword(event.target.value)} required type="password" value={currentPassword} /></label>
          <label className="field"><span>새 비밀번호</span><input autoComplete="new-password" maxLength={128} minLength={12} onChange={(event) => setNewPassword(event.target.value)} required type="password" value={newPassword} /><small>12–128자</small></label>
          <label className="field"><span>새 비밀번호 확인</span><input autoComplete="new-password" maxLength={128} minLength={12} onChange={(event) => setConfirmPassword(event.target.value)} required type="password" value={confirmPassword} /></label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button--primary" disabled={submitting} type="submit"><KeyRound size={16} /> {submitting ? "변경 중…" : "비밀번호 변경"}</button>
        </form>
      </Panel>
      <div className="security-tip"><ShieldCheck /><div><strong>세션 보안</strong><p>로그인은 서버에 안전하게 해시된 세션으로 관리되며, 브라우저에서 세션 원문에 접근할 수 없습니다.</p></div></div>
    </div>
  );
}
