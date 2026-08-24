import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props { children: ReactNode }
interface State { failed: boolean }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    if (import.meta.env.DEV) console.error(error, info);
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="fatal-error">
          <span className="brand__mark">A</span>
          <h1>화면을 표시하지 못했습니다</h1>
          <p>새로고침해도 문제가 계속되면 운영자에게 알려주세요.</p>
          <button className="button button--primary" onClick={() => window.location.reload()} type="button">새로고침</button>
        </main>
      );
    }
    return this.props.children;
  }
}
