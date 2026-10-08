import { useState } from 'react';
import { Modal } from './common';
import type { HygieneReport } from '../types';

const CATEGORY_NAMES: Record<string, string> = {
  C1: '代替玩家言行',
  C2: '代替其他角色发言',
  C3: '心灵感应（回应未表露的内心）',
  C4: '出戏（OOC/元话语）',
};

/** R34 校验详情弹层：违规类别+证据句+重新校验入口（挂在气泡 ⚠️ 角标上）。 */
export function HygieneModal({
  report,
  onRecheck,
  onClose,
}: {
  report: HygieneReport;
  onRecheck: () => Promise<void>;
  onClose: () => void;
}) {
  const [checking, setChecking] = useState(false);

  return (
    <Modal title="输出卫生校验" onClose={onClose}>
      <p className="mb-4 text-sm leading-relaxed text-slate-600">
        {report.passed
          ? '本次回复通过了行为契约校验。'
          : `这条回复未通过行为契约校验${report.attempts > 1 ? '（重试后仍违规，已放行）' : ''}。`}
        {report.corrected && ' 此前一版被自动修正过。'}
      </p>
      {report.violations.length > 0 && (
        <ul className="mb-4 space-y-2">
          {report.violations.map((v, i) => (
            <li key={i} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
              <div className="text-xs font-medium text-amber-700">
                {v.category} · {CATEGORY_NAMES[v.category] ?? '未知'}
              </div>
              <div className="mt-1 text-sm italic leading-relaxed text-amber-800">「{v.evidence}」</div>
            </li>
          ))}
        </ul>
      )}
      <div className="flex justify-between">
        <p className="text-[10px] text-slate-400">
          校验模型独立计费（成本面板·校验列）· 对回复不满意也可直接重roll
        </p>
        <button
          type="button"
          disabled={checking}
          onClick={async () => {
            setChecking(true);
            try {
              await onRecheck();
              onClose();
            } catch {
              setChecking(false);
            }
          }}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          {checking ? '校验中…' : '重新校验当前内容'}
        </button>
      </div>
    </Modal>
  );
}
