import React from 'react'

const TEXT_CLASS = 'text-[13.5px] leading-relaxed text-ink'

export const MARKDOWN_COMPONENTS = {
  p: ({ children }) => <p className={`${TEXT_CLASS} mt-2 first:mt-0 mb-0`}>{children}</p>,
  strong: ({ children }) => (
    <strong className="font-semibold text-ink">{children}</strong>
  ),
  em: ({ children }) => <em className="italic text-body">{children}</em>,
  h1: ({ children }) => (
    <h1 className="font-serif text-lg text-ink mt-3 mb-1 leading-snug">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="font-serif text-base text-ink mt-3 mb-1 leading-snug">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-sm font-semibold text-ink mt-2 mb-1 leading-snug">{children}</h3>
  ),
  ul: ({ children }) => (
    <ul className={`${TEXT_CLASS} list-disc pl-5 mt-2 mb-0 space-y-1`}>{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className={`${TEXT_CLASS} list-decimal pl-5 mt-2 mb-0 space-y-1`}>{children}</ol>
  ),
  li: ({ children }) => <li className={TEXT_CLASS}>{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-hairline pl-3 mt-2 mb-0 text-body italic">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    const isBlock = /language-(\w+)/.test(className || '')
    if (isBlock) {
      return (
        <code className="block font-mono text-[12px] bg-[#F4F2ED] rounded-md px-3 py-2 mt-2 overflow-x-auto">
          {children}
        </code>
      )
    }
    return (
      <code className="font-mono text-[12px] bg-[#EFEAE2] rounded px-1 py-0.5">{children}</code>
    )
  },
  pre: ({ children }) => <pre className="mt-2 mb-0 overflow-x-auto">{children}</pre>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-ink underline underline-offset-2 hover:text-accent"
    >
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto mt-2">
      <table className="text-[12.5px] border-collapse w-full">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-hairline px-2 py-1 text-left font-semibold text-ink">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-hairline px-2 py-1 text-body">{children}</td>
  ),
  hr: () => <hr className="border-hairline my-3" />,
}

export default MARKDOWN_COMPONENTS
