import { StoryLifecycleNotice } from './features/stories/StoryLifecycleNotice';
import { QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router';
import { queryClient } from './queryClient';
import { Bootstrap } from './app/Bootstrap';
import { ToastProvider } from './design-system';
import { CommandRecoveryNotice } from './features/stories/CommandRecoveryNotice';
export default function App() {
  return <QueryClientProvider client={queryClient}><ToastProvider><BrowserRouter><Bootstrap /><CommandRecoveryNotice /><StoryLifecycleNotice /></BrowserRouter></ToastProvider></QueryClientProvider>;
}
