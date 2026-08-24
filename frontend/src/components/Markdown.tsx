import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({ children }: { children?: string | null }) {
  if (!children) return null;
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ children: linkChildren, href }) => href ? (
              <a href={href} rel="noreferrer noopener" target="_blank">
                {linkChildren}
                <span className="sr-only"> (새 창)</span>
              </a>
            ) : <>{linkChildren}</>,
          img: ({ alt, src }) => src
            ? <img alt={alt ?? ""} src={src} />
            : <span>{alt}</span>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
