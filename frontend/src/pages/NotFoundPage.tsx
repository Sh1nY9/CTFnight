import { ArrowLeft, ScanSearch } from "lucide-react";
import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="not-found">
      <ScanSearch size={52} aria-hidden="true" />
      <p className="eyebrow">ERROR 404</p>
      <h1>이 경로에는 플래그가 없습니다.</h1>
      <p>주소가 정확한지 확인하거나 홈으로 돌아가세요.</p>
      <Link className="button button--primary" to="/"><ArrowLeft size={17} /> 홈으로</Link>
    </div>
  );
}
