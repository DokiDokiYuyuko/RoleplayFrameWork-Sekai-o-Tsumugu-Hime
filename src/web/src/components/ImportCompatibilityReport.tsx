export type ImportCompatibility = { converted: string[]; partial: string[]; unsupported: string[] };

export function ImportCompatibilityReport({ report, title = '导入行为核对' }: {
  report?: ImportCompatibility | null; title?: string;
}) {
  if (!report) return null;
  return <details className="v7-notice" open={report.unsupported.length > 0}>
    <summary>{title}{report.unsupported.length > 0 ? ` · ${report.unsupported.length} 项未执行` : ''}</summary>
    {([['converted', '已支持或转换'], ['partial', '转换差异或仅保存'], ['unsupported', '未执行']] as const)
      .map(([key, label]) => report[key].length > 0 && <div key={key} className="mt-2 text-xs">
        <strong>{label}</strong><ul className="mt-1 list-disc space-y-1 pl-4">
          {report[key].map((row, index) => <li key={index}>{row}</li>)}
        </ul>
      </div>)}
  </details>;
}
