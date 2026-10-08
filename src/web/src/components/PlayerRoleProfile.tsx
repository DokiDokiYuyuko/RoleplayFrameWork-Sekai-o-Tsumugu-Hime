import type { PlayerIdentity } from '../types';
import { Modal, Avatar } from './common';
import { CharacterInfoModal } from './CharacterInfoModal';
import { toView } from '../api/adapters';
import { playerAvatarUrl, currentPlayerAvatarUrl } from '../utils/playerIdentity.js';

export function PlayerRoleProfile({ identity, branchId, onClose, current = false }: {
  identity: PlayerIdentity | null; branchId: string | null; onClose: () => void; current?: boolean;
}) {
  const avatar = current ? currentPlayerAvatarUrl(branchId, identity) : playerAvatarUrl(branchId, identity);
  if (identity?.character) return <CharacterInfoModal character={toView(identity.character)}
    readOnly snapshotAvatar avatarSrc={avatar} onClose={onClose} />;
  return <Modal title={identity?.name ?? '历史玩家身份'} onClose={onClose}>
    <Avatar name={identity?.name ?? '玩家'} color="slate" src={avatar} />
    <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
      {identity?.persona || '这条旧消息没有可验证的身份快照，保留原始消息，未套用当前角色资料。'}
    </p>
  </Modal>;
}
