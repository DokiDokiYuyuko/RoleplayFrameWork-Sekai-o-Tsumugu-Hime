import { PageHeader } from "../design-system";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { personalLibraryApi } from "../features/library/libraryApi";

interface Checklist {
  categories: Array<{
    key: string;
    label: string;
    file_count: number;
    bytes: number;
  }>;
  excludes: string[];
  steps: string[];
  secrets_included: boolean;
}
export default function PersonalLibraryPanel() {
  const list = useQuery({
    queryKey: ["personal-library-checklist"],
    queryFn: () => personalLibraryApi.checklist<Checklist>(),
  });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const verify = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setResult("");
    setError("");
    try {
      const report = await personalLibraryApi.verify(file);
      setResult(
        `清单及文件校验通过${report.file_count == null ? "" : `，共 ${report.file_count} 个文件`}。可以按离线步骤恢复到新目录。`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "校验失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-5">
      <PageHeader
        headingLevel={2}
        title="个人库恢复清单"
        meta="故事分享包与整套创作库的范围不同。下面列出私密库备份覆盖的内容，备份和恢复使用离线维护工具。"
      />
      {list.isPending && <p role="status">正在核对范围…</p>}
      {list.error && <p role="alert">{list.error.message}</p>}
      {list.data && (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr>
                  <th className="border-b p-2">内容</th>
                  <th className="border-b p-2 text-right">文件</th>
                  <th className="border-b p-2 text-right">大小</th>
                </tr>
              </thead>
              <tbody>
                {list.data.categories.map((row) => (
                  <tr key={row.key}>
                    <td className="border-b border-[var(--line)] p-2">
                      {row.label}
                    </td>
                    <td className="border-b border-[var(--line)] p-2 text-right">
                      {row.file_count}
                    </td>
                    <td className="border-b border-[var(--line)] p-2 text-right">
                      {(row.bytes / 1024 / 1024).toFixed(1)} MB
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="rounded-xl border border-[var(--line)] bg-[var(--warning-soft)] p-4 text-sm text-[var(--ink)]">
            <h3 className="font-semibold">需要另行配置</h3>
            {list.data.excludes.map((item) => (
              <p key={item} className="mt-2">
                {item}
              </p>
            ))}
          </div>
          <div>
            <h3 className="font-semibold">备份与恢复</h3>
            <ol className="mt-3 list-decimal space-y-3 pl-5 text-sm">
              {list.data.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            <p className="mt-3 text-sm text-[var(--muted)]">
              离线维护工具在项目
              tools/maintenance/personal_library.py；详细操作见项目的《备份与分享》。恢复会拒绝已有内容的目标目录。
            </p>
          </div>
        </>
      )}
      <label className="v7-field">
        校验已有私密库备份
        <input
          type="file"
          accept=".zip"
          disabled={busy}
          onChange={(e) => void verify(e.target.files?.[0])}
        />
        <small>网页可校验 200 MB 以内的文件；更大备份使用离线工具。</small>
      </label>
      {busy && <p role="status">正在校验清单和文件…</p>}
      {result && (
        <p role="status" className="text-sm text-[var(--color-success)]">
          {result}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-[var(--danger)]">
          {error}
        </p>
      )}
    </div>
  );
}
