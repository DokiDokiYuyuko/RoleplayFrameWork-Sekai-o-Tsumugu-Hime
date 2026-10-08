import type { AssistBatch, OptionChoice } from '../types';

export interface WritingRequest {
  intent: string;
  draft_text?: string;
  output_type?: 'draft' | 'material';
  base_candidate?: string;
  expected_branch_revision?: number;
  expected_player_identity_id?: string | null;
}
export interface WritingBatch extends AssistBatch {
  options: (OptionChoice & { title: string })[];
  player_identity_id: string | null;
  context_version: string;
  stale: boolean;
  warnings: string[];
}
