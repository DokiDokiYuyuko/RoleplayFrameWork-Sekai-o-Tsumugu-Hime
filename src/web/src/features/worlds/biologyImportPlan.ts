import type { ArchiveRecord, World } from "../../types";

export type BiologyImportStatus = "new" | "same-title" | "imported" | "updated";

export interface BiologyImportRow {
  id: string;
  title: string;
  visibility: "public" | "private";
  visibilityLabel: string;
  classification: string;
  status: BiologyImportStatus;
  mode: "copy" | "overwrite";
  checked: boolean;
}

const CLASS_LABEL: Record<string, string> = {
  race: "种族",
  species: "物种",
  creature: "怪物 / 生物",
  other: "其他",
};

function provenanceMatch(destination: World, sourceWorldId: string, source: ArchiveRecord) {
  return destination.archive_records.find((item) =>
    item.copied_from_world_id === sourceWorldId && item.copied_from_archive_id === source.id,
  );
}

/** Default checks for the import dialog. Title collisions are not identity. */
export function planBiologyImport(source: World, destination: World): BiologyImportRow[] {
  return source.archive_records.filter((record) => record.kind === "biology").map((record) => {
    const match = provenanceMatch(destination, source.id, record);
    const classification = CLASS_LABEL[record.kind_data.classification] ?? "其他";
    const visibilityLabel = record.visibility === "private" ? "仅作者可见" : "供故事使用";
    const base = {
      id: record.id,
      title: record.title,
      visibility: record.visibility,
      visibilityLabel,
      classification,
    };
    if (match) {
      const updated = match.copied_from_revision !== record.revision;
      return {
        ...base,
        status: updated ? "updated" as const : "imported" as const,
        mode: "overwrite" as const,
        checked: false,
      };
    }
    const trimmed = record.title.trim();
    const sameTitle = destination.archive_records.some((item) =>
      item.kind === "biology" && item.title.trim() === trimmed,
    );
    return {
      ...base,
      status: sameTitle ? "same-title" as const : "new" as const,
      mode: "copy" as const,
      checked: !sameTitle && record.visibility === "public",
    };
  });
}
