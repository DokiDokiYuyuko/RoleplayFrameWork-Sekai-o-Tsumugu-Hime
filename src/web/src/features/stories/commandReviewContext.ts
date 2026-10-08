import type { IncompleteCommand } from '../../api/storyCommands';
import type { Session } from '../../types';

export function commandReviewContext(command: IncompleteCommand, currentBranch: string | null, sessions: readonly Session[]): string | null {
  const branch = command.path.match(/^\/api\/v1\/(?:sessions|branches)\/([^/?]+)/)?.[1];
  if (branch && decodeURIComponent(branch) !== currentBranch) return null;
  let action = '先前操作';
  if (command.path === '/api/v1/sessions') action = '新建故事';
  else if (command.path.includes('/stories/import') || command.path.includes('/chat-import/st/')) action = '导入故事';
  else if (/\/scenarios\/[^/]+\/start$/.test(command.path)) action = '从场景新建故事';
  else if (command.path.endsWith('/fork')) action = '创建新路线';
  else if (command.path.includes('/save?')) action = '创建存档';
  else if (command.path.endsWith('/restore')) action = '恢复先前存档';
  else if (/swipe|regenerate/.test(command.path)) action = '重新生成回复';
  else if (/continue|\/groups\/[^/]+\/reply$/.test(command.path)) action = '生成回复';
  else if (command.path.includes('/memory/consolidate')) action = '整理记忆';
  else if (command.path.includes('/memory/records') || command.path.includes('/memories/')) action = '修改角色记忆';
  else if (command.path.includes('/stories/trash/')) action = command.method === 'DELETE' ? '清除回收故事' : '恢复回收故事';
  else if (command.method === 'DELETE' && command.path.includes('/stories/')) action = '删除故事';
  else if (command.path.includes('/director/pending/')) action = '执行导演决策';
  else if (command.path.includes('/groups')) action = '更新群体';
  else if (command.path.includes('/scene/switch')) action = '切换场景';
  else if (command.path.includes('/participants')) action = '加入人物';
  const session = branch ? sessions.find(session => session.id === decodeURIComponent(branch)) : null;
  return session ? `「${session.branch_name ?? session.title}」的${action}` : branch ? `当前路线的${action}` : action;
}
