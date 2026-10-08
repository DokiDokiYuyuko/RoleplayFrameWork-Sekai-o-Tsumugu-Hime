import { lazy, Suspense, useEffect, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router';
import { useChatStore } from '../store/chatStore';
import { readStoryLocation } from '../api/navigation';
import { beginLoad } from '../utils/loadDiagnostics';
import { AppShell } from '../appearance/AppShell';
import { LoadState } from '../components/LoadState';
import { RouteErrorBoundary } from './RouteErrorBoundary';
function loadPage<T>(loader: () => Promise<T>, label: string): Promise<T> {
  const finish = beginLoad("module", label);
  let timer: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      finish("timeout");
      reject(new Error("页面文件读取超时，请重新加载页面。"));
    }, 15_000);
  });
  return Promise.race([loader(), timeout])
    .then(
      (value) => {
        finish();
        return value;
      },
      (cause) => {
        finish("error");
        throw cause;
      },
    )
    .finally(() => clearTimeout(timer));
}

const ChatPage = lazy(() =>
  loadPage(() => import("../pages/ChatPage"), "/page/ChatPage"),
);
const StoryHome = lazy(() => loadPage(() => import('../features/stories/StoryHome'), '/page/StoryHome').then(module => ({ default: module.StoryHome })));
const StoryWizard = lazy(() => loadPage(() => import('../features/stories/StoryHome'), '/page/StoryWizard').then(module => ({ default: module.StoryWizard })));
const LibraryIndex = lazy(() => loadPage(() => import('./LibraryLayout'), '/page/LibraryIndex').then(module => ({ default: module.LibraryIndex })));
const SectionPage = lazy(() => loadPage(() => import('./LibraryLayout'), '/page/LibrarySection').then(module => ({ default: module.SectionPage })));
const CharactersPage = lazy(() =>
  loadPage(() => import("../pages/CharactersPage"), "/page/CharactersPage"),
);
const CharacterEditorPage = lazy(() =>
  loadPage(() => import("../pages/CharacterEditorPage"), "/page/CharacterEditorPage"),
);
const LorebookPage = lazy(() =>
  loadPage(() => import("../pages/LorebookPage"), "/page/LorebookPage"),
);
const ScenariosPage = lazy(() =>
  loadPage(() => import("../pages/ScenariosPage"), "/page/ScenariosPage"),
);
const SettingsPage = lazy(() =>
  loadPage(() => import("../pages/SettingsPage"), "/page/SettingsPage"),
);
const WorkshopPage = lazy(() =>
  loadPage(() => import("../pages/WorkshopPage"), "/page/WorkshopPage"),
);
const WorldlinePage = lazy(() =>
  loadPage(
    () => import("../features/worldline/WorldlinePage"),
    "/page/worldline",
  ).then((module) => ({
    default: module.WorldlinePage,
  })),
);
const WorldsPage = lazy(() =>
  loadPage(() => import("../features/worlds/WorldsPage"), "/page/WorldsPage"),
);
const WorldOrganizePage = lazy(() =>
  loadPage(
    () => import("../features/worlds/WorldOrganizePage"),
    "/page/WorldOrganizePage",
  ),
);
const AssetImportPage = lazy(() =>
  loadPage(
    () => import("../features/asset-import/AssetImportPage"),
    "/page/AssetImportPage",
  ),
);
const SimpleChatPage = lazy(() =>
  loadPage(() => import("../pages/SimpleChatPage"), "/page/SimpleChatPage"),
);
// Verification surface for the design system. Not linked from navigation and left out of
// production builds unless VITE_UI_KIT=1 (it would use 16 kB of the fixed bundle budget).
const KitGallery = import.meta.env.VITE_UI_KIT
  ? lazy(() => loadPage(() => import("../design-system/KitGallery"), "/page/KitGallery"))
  : null;

function LegacyEntry() {
  const location = readStoryLocation();
  if (location.storyId && location.branchId) {
    const message = location.messageId
      ? `?message=${encodeURIComponent(location.messageId)}`
      : "";
    return (
      <Navigate
        replace
        to={`/stories/${encodeURIComponent(location.storyId)}/branches/${encodeURIComponent(location.branchId)}${message}`}
      />
    );
  }
  return <StoryHome />;
}

function StoryRoute() {
  const { branchId } = useParams();
  const current = useChatStore((s) => s.currentSessionId);
  const ready = useChatStore((s) => s.snapshotLoadedSessionId);
  const status = useChatStore((s) => s.snapshotStatus);
  const error = useChatStore((s) => s.snapshotError);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!branchId) return;
    const finish = beginLoad("state", "/stories/:id/branches/:id");
    void useChatStore
      .getState()
      .selectSession(branchId, false)
      .then(
        () => finish(),
        () => finish("error"),
      );
    return () => {
      if (useChatStore.getState().currentSessionId === branchId)
        useChatStore.getState().disconnect();
    };
  }, [branchId, retry]);
  if (current !== branchId || ready !== branchId || status !== "ready")
    return (
      <>
        <LoadState
          title={status === "error" ? "这条路线未能打开" : "正在打开这条路线"}
          error={error ?? undefined}
          onRetry={
            status === "error"
              ? () => setRetry((value) => value + 1)
              : undefined
          }
        />
        <Link className="page-load-back" to="/">
          返回故事
        </Link>
      </>
    );
  return <ChatPage />;
}

function WorkshopRedirect() {
  const { type } = useParams();
  return <Navigate replace to={`/create${type ? '/' + encodeURIComponent(type) : ''}`} />;
}
export function AppRoutes() {
  const location = useLocation();
  const navigate = useNavigate();
  const boundaryKey = location.pathname;
  return (
    <AppShell>
      <RouteErrorBoundary key={boundaryKey}>
        <Suspense fallback={<LoadState title="正在打开页面" />}>
          <Routes>
            <Route path="/" element={<LegacyEntry />} />
            <Route path="/stories/new" element={<StoryWizard />} />
            <Route
              path="/stories/:storyId/branches/:branchId"
              element={<StoryRoute />}
            />
            <Route
              path="/stories/:storyId/worldline"
              element={
                <Suspense fallback={<LoadState title="正在打开世界线" />}>
                  <WorldlinePage />
                </Suspense>
              }
            />
            <Route path="/library" element={<LibraryIndex />} />
            <Route path="/simple-chats/:chatId?" element={<SimpleChatPage />} />
            <Route
              path="/library/imports/:jobId?/drafts/:draftId?"
              element={<AssetImportPage />}
            />
            <Route
              path="/library/imports/:jobId?"
              element={<AssetImportPage />}
            />
            <Route path="/worlds" element={<WorldsPage />} />
            <Route path="/worlds/:worldId" element={<WorldsPage />} />
            <Route
              path="/worlds/:worldId/organize"
              element={<WorldOrganizePage />}
            />
            <Route
              path="/worlds/:worldId/archive/:kind/:recordId?"
              element={<WorldsPage />}
            />
            <Route
              path="/worlds/:worldId/lorebooks/:bookId?"
              element={<WorldsPage />}
            />
            <Route path="/library/characters" element={<CharactersPage />} />
            <Route path="/library/characters/new" element={<CharacterEditorPage />} />
            <Route path="/library/characters/:id" element={<CharacterEditorPage />} />
            <Route
              path="/library/lorebooks/:id?"
              element={
                <SectionPage title="世界书" eyebrow="LIBRARY / LOREBOOKS" fullBleed>
                  <LorebookPage />
                </SectionPage>
              }
            />
            <Route
              path="/library/scenarios/:id?"
              element={
                <SectionPage title="场景预设" eyebrow="LIBRARY / SCENARIOS" fullBleed>
                  <ScenariosPage
                    onStarted={() => {
                      const sid = useChatStore.getState().currentSessionId;
                      const session = useChatStore
                        .getState()
                        .sessions.find((s) => s.id === sid);
                      if (session)
                        navigate(
                          `/stories/${session.story_id ?? session.id}/branches/${session.id}`,
                        );
                    }}
                  />
                </SectionPage>
              }
            />
            <Route
              path="/create/:type?"
              element={
                <SectionPage title="创作工坊" eyebrow="CREATE / WORKSHOP" fullBleed>
                  <WorkshopPage />
                </SectionPage>
              }
            />
            <Route
              path="/workshop/:type?"
              element={<WorkshopRedirect />}
            />
            <Route path="/settings/:section?" element={<SettingsPage />} />
            {KitGallery && <Route path="/dev/ui-kit" element={<KitGallery />} />}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </RouteErrorBoundary>
    </AppShell>
  );
}

