import { api } from "@/api/endpoints";
import { useAuth } from "@/auth/AuthContext";
import { getErrorMessage } from "@/lib/errors";
import { ArrowRight, Eye, EyeOff, KeyRound, ShieldCheck, UserPlus } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

function AuthFrame({ mode }: { mode: "login" | "register" }) {
  const { user, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [accessCode, setAccessCode] = useState("");
  const [registrationAccessMode, setRegistrationAccessMode] = useState<"open" | "code" | null>(null);
  const [registrationModeUnavailable, setRegistrationModeUnavailable] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const isLogin = mode === "login";

  useEffect(() => {
    if (user) navigate(user.password_change_required ? "/account/security" : user.role === "admin" ? "/admin" : "/challenges", { replace: true });
  }, [user, navigate]);

  useEffect(() => {
    if (isLogin) return;
    let current = true;
    api.participant.event()
      .then((event) => {
        if (!current) return;
        setRegistrationAccessMode(event.registration_access_mode ?? "open");
        setRegistrationModeUnavailable(false);
      })
      .catch(() => {
        if (!current) return;
        // Fail safe for usability: show the field when event metadata cannot
        // be loaded. The server remains the authority and rejects bad codes.
        setRegistrationModeUnavailable(true);
      });
    return () => { current = false; };
  }, [isLogin]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const current = isLogin
        ? await login(email.trim(), password)
        : await register(email.trim(), username.trim(), password, accessCode.trim() || undefined);
      const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname;
      navigate(current.password_change_required ? "/account/security" : from ?? (current.role === "admin" ? "/admin" : "/challenges"), { replace: true });
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-page">
      <section className="auth-aside">
        <div>
          <span className="auth-aside__icon">{isLogin ? <KeyRound /> : <UserPlus />}</span>
          <p className="eyebrow">{isLogin ? "WELCOME BACK" : "JOIN THE ARENA"}</p>
          <h1>{isLogin ? "다시 연결하세요." : "첫 플래그를 향해."}</h1>
          <p>{isLogin ? "팀의 진행 상황과 새로운 문제들이 기다리고 있습니다." : "계정을 만들고 팀에 합류해 실전 보안 역량을 증명하세요."}</p>
        </div>
        <ul>
          <li><ShieldCheck size={17} /> 안전한 세션 기반 인증</li>
          <li><ShieldCheck size={17} /> 제출 원문을 남기지 않는 설계</li>
          <li><ShieldCheck size={17} /> 공정한 팀 단위 채점</li>
        </ul>
      </section>
      <section className="auth-card" aria-labelledby="auth-title">
        <p className="eyebrow">CTFnight ACCESS</p>
        <h2 id="auth-title">{isLogin ? "로그인" : "계정 만들기"}</h2>
        <p>{isLogin ? "등록한 이메일로 계속하세요." : "경기에서 사용할 정보를 입력하세요."}</p>
        <form onSubmit={handleSubmit}>
          {!isLogin && (
            <label className="field">
              <span>사용자 이름</span>
              <input
                autoComplete="username"
                maxLength={40}
                minLength={3}
                onChange={(event) => setUsername(event.target.value)}
                pattern={"[A-Za-z0-9_.\\-]+"}
                placeholder="player_one"
                required
                value={username}
              />
              <small>3–40자, 영문·숫자·점·밑줄·하이픈을 사용할 수 있습니다.</small>
            </label>
          )}
          <label className="field">
            <span>이메일</span>
            <input
              autoComplete="email"
              inputMode="email"
              maxLength={254}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="player@example.com"
              required
              type="email"
              value={email}
            />
          </label>
          <label className="field">
            <span>비밀번호</span>
            <span className="password-field">
              <input
                autoComplete={isLogin ? "current-password" : "new-password"}
                maxLength={128}
                minLength={isLogin ? undefined : 12}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={isLogin ? "비밀번호 입력" : "12자 이상 입력"}
                required
                type={showPassword ? "text" : "password"}
                value={password}
              />
              <button aria-label={showPassword ? "비밀번호 숨기기" : "비밀번호 표시"} onClick={() => setShowPassword((value) => !value)} type="button">
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </span>
            {!isLogin && <small>12자 이상을 권장합니다.</small>}
          </label>
          {!isLogin && (registrationAccessMode === "code" || registrationModeUnavailable) && (
            <label className="field">
              <span>등록 접근 코드</span>
              <input
                aria-label="등록 접근 코드"
                autoCapitalize="none"
                autoComplete="off"
                className="mono-input"
                maxLength={128}
                onChange={(event) => setAccessCode(event.target.value)}
                placeholder="운영자에게 받은 코드"
                required={registrationAccessMode === "code"}
                spellCheck={false}
                value={accessCode}
              />
              <small>
                {registrationModeUnavailable
                  ? "이벤트 설정을 확인하지 못했습니다. 코드를 받은 경우 입력하세요."
                  : "이 이벤트는 운영자가 발급한 접근 코드가 있어야 가입할 수 있습니다."}
              </small>
            </label>
          )}
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button--primary button--full" disabled={submitting} type="submit">
            {submitting ? "처리 중…" : isLogin ? "로그인" : "계정 만들기"}
            {!submitting && <ArrowRight size={18} />}
          </button>
        </form>
        <p className="auth-switch">
          {isLogin ? "처음이신가요?" : "이미 계정이 있나요?"}{" "}
          <Link to={isLogin ? "/register" : "/login"}>{isLogin ? "회원가입" : "로그인"}</Link>
        </p>
      </section>
    </div>
  );
}

export function LoginPage() { return <AuthFrame mode="login" />; }
export function RegisterPage() { return <AuthFrame mode="register" />; }
