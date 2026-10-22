import React from 'react'

const CITATION_SOURCE = '\\[([A-Z][A-Z0-9_]*_D\\d+)\\]'
const FULL_DATE_SOURCE = '\\d{1,2}\\/\\d{1,2}\\/\\d{4}'
const SHORT_DATE_SOURCE = '\\d{1,2}\\/\\d{4}'
const PERCENT_SOURCE = '\\d+(?:[.,]\\d+)?\\s?%'
const MONEY_SOURCE = '\\d[\\d.,]*\\s?(?:triệu|đồng|VND|USD)'

const COMBINED_PATTERN = new RegExp(
  [CITATION_SOURCE, FULL_DATE_SOURCE, SHORT_DATE_SOURCE, PERCENT_SOURCE, MONEY_SOURCE].join('|'),
  'g',
)

const FULL_DATE_PATTERN = /^\d{1,2}\/\d{1,2}\/\d{4}$/
const SHORT_DATE_PATTERN = /^\d{1,2}\/\d{4}$/

const CITATION_CLASS =
  'bg-[#F3E9D2] text-[#7A4E10] px-1 py-0.5 rounded text-[12px] font-medium hover:bg-[#EBDCBD] transition-colors'
const MARK_CLASS = 'bg-[#EFE7D5] text-ink rounded px-0.5'

export function splitParts(text) {
  if (typeof text !== 'string' || text.length === 0) return []

  const parts = []
  let cursor = 0

  for (const match of text.matchAll(COMBINED_PATTERN)) {
    const raw = match[0]
    const start = match.index
    if (start > cursor) {
      parts.push({ type: 'text', text: text.slice(cursor, start) })
    }

    const citationId = match[1]
    if (citationId) {
      parts.push({ type: 'citation', text: raw, id: citationId })
    } else if (FULL_DATE_PATTERN.test(raw) || SHORT_DATE_PATTERN.test(raw)) {
      parts.push({ type: 'date', text: raw })
    } else {
      parts.push({ type: 'number', text: raw })
    }

    cursor = start + raw.length
  }

  if (cursor < text.length) {
    parts.push({ type: 'text', text: text.slice(cursor) })
  }

  return parts
}

function renderPart(part, key, onCitationClick) {
  if (part.type === 'citation') {
    return React.createElement(
      'button',
      {
        key,
        type: 'button',
        title: 'Xem nguyên văn điều khoản',
        className: CITATION_CLASS,
        onClick: () => onCitationClick?.(part.id),
      },
      part.text,
    )
  }

  if (part.type === 'date' || part.type === 'number') {
    return React.createElement('mark', { key, className: MARK_CLASS }, part.text)
  }

  return part.text
}

export function highlightChildren(children, onCitationClick) {
  return React.Children.map(children, (child, index) => {
    if (typeof child === 'string' || typeof child === 'number') {
      return splitParts(String(child)).map((part, partIndex) =>
        renderPart(part, `hl-${index}-${partIndex}`, onCitationClick),
      )
    }
    return child
  })
}

export default highlightChildren
