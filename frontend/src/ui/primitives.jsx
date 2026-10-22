import React from 'react'

export function Card({ children, className = '', ...rest }) {
  return (
    <section
      className={`bg-surface border border-hairline rounded-[14px] p-5 ${className}`}
      {...rest}
    >
      {children}
    </section>
  )
}

export function CardHeader({ title, caption, action }) {
  return (
    <div className="flex items-start justify-between gap-4 mb-4">
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold text-ink leading-snug">{title}</h2>
        {caption && <p className="mt-0.5 text-[11px] text-muted leading-relaxed">{caption}</p>}
      </div>
      {action}
    </div>
  )
}

export function Pill({ children, onClick, active = false, className = '' }) {
  const base =
    'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium border transition-colors'
  const tone = active
    ? 'bg-ink text-white border-ink'
    : 'bg-surface text-body border-hairline hover:border-neutral-400 hover:text-ink'
  if (!onClick) return <span className={`${base} ${tone} ${className}`}>{children}</span>
  return (
    <button type="button" onClick={onClick} className={`${base} ${tone} ${className}`}>
      {children}
    </button>
  )
}

export function Legend({ tone = 'neutral', textClassName = 'text-body', children }) {
  const dot = {
    ok: 'bg-emerald-500',
    warn: 'bg-amber-500',
    danger: 'bg-rose-500',
    neutral: 'bg-neutral-400',
  }[tone]
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] ${textClassName}`}>
      <span className={`w-2 h-2 rounded-full ${dot}`} />
      {children}
    </span>
  )
}

export function NavRow({ icon, children, active = false, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-left text-[13px] transition-colors ${
        active
          ? 'bg-white/10 text-white font-medium'
          : 'text-neutral-400 hover:text-neutral-100 hover:bg-white/5'
      }`}
    >
      {icon}
      <span>{children}</span>
    </button>
  )
}
