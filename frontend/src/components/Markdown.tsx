import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Markdown 渲染 —— 净化正文(trafilatura output_format=markdown)的排版层。
 * 阅读级衬线排版:serif 正文 16.5px/1.95、标题衬线降档、链接墨蓝、三线表。
 * GFM 表格/删除线/任务列表支持;链接一律新标签。
 */
export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children }) => <h1 className="mb-4 mt-10 font-serif text-[24px] font-bold leading-snug text-ink-0">{children}</h1>,
        h2: ({ children }) => <h2 className="mb-3 mt-9 font-serif text-[20px] font-bold leading-snug text-ink-0">{children}</h2>,
        h3: ({ children }) => <h3 className="mb-2 mt-7 font-serif text-[17px] font-bold text-ink-0">{children}</h3>,
        h4: ({ children }) => <h4 className="mb-2 mt-6 font-serif text-[16px] font-bold text-ink-0">{children}</h4>,
        p: ({ children }) => <p className="my-5 font-serif text-[16.5px] leading-[1.95] text-ink-1">{children}</p>,
        a: ({ children, href }) => (
          <a href={href} target="_blank" rel="noreferrer" className="text-cyan underline decoration-cyan/30 underline-offset-[3px] transition-colors hover:decoration-cyan/70">
            {children}
          </a>
        ),
        ul: ({ children }) => <ul className="my-5 list-disc space-y-2 pl-6 font-serif">{children}</ul>,
        ol: ({ children }) => <ol className="my-5 list-decimal space-y-2 pl-6 font-serif">{children}</ol>,
        li: ({ children }) => <li className="text-[16.5px] leading-[1.85] text-ink-1 marker:text-signal/70">{children}</li>,
        blockquote: ({ children }) => (
          <blockquote className="my-6 border-l-2 border-signal/40 pl-5 font-serif text-[15.5px] italic leading-[1.9] text-ink-2">{children}</blockquote>
        ),
        code: ({ children, className }) =>
          /language-/.test(className ?? '') ? (
            <code className={`font-mono text-[13px] leading-relaxed text-ink-1 ${className ?? ''}`}>{children}</code>
          ) : (
            <code className="rounded bg-bg-2 px-1.5 py-0.5 font-mono text-[13px] text-amber">{children}</code>
          ),
        pre: ({ children }) => (
          <pre className="my-6 overflow-x-auto rounded-md border border-line bg-bg-1 p-4">{children}</pre>
        ),
        table: ({ children }) => (
          <div className="my-6 overflow-x-auto rounded-md border border-line">
            <table className="w-full border-collapse text-[13.5px]">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="border-b border-line-strong bg-bg-2 px-3 py-2 text-left text-[12px] font-medium tracking-wide text-ink-1">{children}</th>
        ),
        td: ({ children }) => <td className="border-b border-line px-3 py-2 align-top text-ink-1">{children}</td>,
        img: ({ src, alt }) => (
          <img src={src} alt={alt ?? ''} loading="lazy" className="my-6 max-w-full rounded-md border border-line" />
        ),
        hr: () => <hr className="my-10 border-line" />,
        strong: ({ children }) => <strong className="font-bold text-ink-0">{children}</strong>,
      }}
    >
      {children}
    </ReactMarkdown>
  )
}
