import { storyCommand } from '../../api/storyCommands';
export type { IncompleteCommand } from '../../api/storyCommands';
export const commandRecovery = { subscribe: storyCommand.subscribe, incomplete: storyCommand.incomplete, reviewNewAttempt: storyCommand.reviewNewAttempt };
