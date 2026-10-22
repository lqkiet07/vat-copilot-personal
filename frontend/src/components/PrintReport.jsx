import React from 'react'
import { Printer } from 'lucide-react'

export function PrintReport({
  query = '',
  asOfDate = '',
  pipeline = 'P3_FullSystem',
  result = null,
  className = '',
}) {
  const handlePrint = () => {
    if (typeof window !== 'undefined') {
      window.print()
    }
  }

  const riskScore = result?.risk_score ?? 0
  const riskLevel = result?.risk_level || 'LOW'
  const citations = result?.citations || []
  const warnings = result?.warnings || []
  const irac = result?.irac_sections || {}

  return (
    <>
      {/* Interactive Action Button (hidden when printing) */}
      <div className={`print:hidden ${className}`}>
        <button
          type="button"
          onClick={handlePrint}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-medium text-zinc-700 bg-white border border-zinc-300 hover:bg-zinc-50 hover:text-zinc-950 transition-all cursor-pointer"
          title="In biên bản tư vấn ra khổ giấy A4 hoặc lưu thành tệp PDF"
        >
          <Printer className="w-3.5 h-3.5 text-zinc-500" strokeWidth={1.5} />
          <span>In biên bản tư vấn</span>
        </button>
      </div>

      {/* Hidden Formal Report on Screen, Visible on Print */}
      <div className="hidden print:block print:p-8 font-serif leading-relaxed text-black max-w-4xl mx-auto">
        <div className="text-center mb-6 border-b pb-4">
          <p className="font-bold text-sm tracking-widest uppercase">
            CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
          </p>
          <p className="text-xs italic underline">Độc lập - Tự do - Hạnh phúc</p>
          <h1 className="text-xl font-bold uppercase mt-6 tracking-wide">
            BIÊN BẢN TƯ VẤN QUY ĐỊNH THUẾ GIÁ TRỊ GIA TĂNG
          </h1>
          <p className="text-xs italic text-gray-600 mt-1">
            (Hệ thống Hỗ trợ Suy luận Pháp lý Tự động • Temporal GraphRAG CT250H)
          </p>
        </div>

        <div className="space-y-4 text-sm mb-6">
          <div className="grid grid-cols-2 gap-4 bg-gray-50 p-3 rounded border text-xs">
            <div>
              <p><strong>Yêu cầu tư vấn:</strong> {query || '—'}</p>
              <p><strong>Mốc thời gian áp dụng:</strong> {asOfDate || '—'}</p>
            </div>
            <div>
              <p><strong>Kiến trúc suy luận:</strong> {pipeline}</p>
              <p>
                <strong>Điểm rủi ro pháp lý:</strong> {riskScore} / 100 ({riskLevel})
              </p>
            </div>
          </div>

          {warnings.length > 0 && (
            <div className="border border-gray-300 p-3 rounded text-xs bg-gray-50">
              <p className="font-bold mb-1">CẢNH BÁO RỦI RO HIỆU LỰC & SỬA ĐỔI:</p>
              <ul className="list-disc pl-5 space-y-0.5">
                {warnings.map((w, i) => (
                  <li key={`print-warn-${i}`}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-3">
            <div>
              <h2 className="font-bold uppercase text-xs border-b pb-1 mb-1">
                I. Vấn đề pháp lý (Issue)
              </h2>
              <p className="text-xs pl-2">{irac.issue || '—'}</p>
            </div>

            <div>
              <h2 className="font-bold uppercase text-xs border-b pb-1 mb-1">
                II. Căn cứ pháp lý viện dẫn (Rule)
              </h2>
              <p className="text-xs pl-2">{irac.rule || '—'}</p>
              {citations.length > 0 && (
                <p className="text-[11px] pl-2 text-gray-700 mt-1">
                  <strong>Danh mục điều khoản:</strong> {citations.join(', ')}
                </p>
              )}
            </div>

            <div>
              <h2 className="font-bold uppercase text-xs border-b pb-1 mb-1">
                III. Phân tích áp dụng vào bối cảnh (Application)
              </h2>
              <p className="text-xs pl-2">{irac.application || '—'}</p>
            </div>

            <div>
              <h2 className="font-bold uppercase text-xs border-b pb-1 mb-1">
                IV. Kết luận tư vấn (Conclusion)
              </h2>
              <p className="text-xs pl-2 font-semibold">{irac.conclusion || '—'}</p>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 text-center text-xs mt-12 pt-4 border-t">
          <div>
            <p className="font-bold">ĐẠI DIỆN DOANH NGHIỆP TRA CỨU</p>
            <p className="italic text-[10px] text-gray-500">(Ký và ghi rõ họ tên)</p>
          </div>
          <div>
            <p className="italic text-[10px] text-gray-500">
              Ngày xuất biên bản: {new Date().toLocaleDateString('vi-VN')}
            </p>
            <p className="font-bold mt-1">HỆ THỐNG KIỂM TOÁN TỰ ĐỘNG</p>
            <p className="italic text-[10px] text-gray-500">(Xác thực qua Đồ thị Neo4j)</p>
          </div>
        </div>
      </div>
    </>
  )
}

export default PrintReport
