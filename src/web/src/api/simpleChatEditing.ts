import { jsonRequest } from './request';
import type { SimpleChat } from '../types';

export function editConfirmedSimpleChat(chatId: string, messageId: string, content: string, truncateAfter: boolean, expectedUpdatedAt: string) {
  return jsonRequest<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(messageId)}`, {
    method: 'PATCH', body: JSON.stringify({ content, truncate_after: truncateAfter, expected_updated_at: expectedUpdatedAt }),
  });
}
export function deleteConfirmedSimpleChat(chatId: string, messageId: string, truncateAfter: boolean, expectedUpdatedAt: string) {
  return jsonRequest<SimpleChat>(`/api/v1/simple-chats/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(messageId)}`, {
    method: 'DELETE', body: JSON.stringify({ truncate_after: truncateAfter, expected_updated_at: expectedUpdatedAt }),
  });
}
